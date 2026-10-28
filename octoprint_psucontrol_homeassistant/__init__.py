__author__ = "Erik de Keijzer <erik.de.keijzer@gmail.com>"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"
__copyright__ = "Copyright (C) 2021 Erik de Keijzer - Released under terms of the AGPLv3 License. Modified 2026 by jared-parrish321."

import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import flask
import octoprint.plugin
import requests
from octoprint.access.permissions import Permissions

# (connect, read) seconds. Without a timeout requests waits forever, which would
# block PSU Control's turn_psu_off() if Home Assistant stops answering.
REQUEST_TIMEOUT = (3.05, 5)

# After a turn-off, check the entity really is off and retry if not.
OFF_VERIFY_ATTEMPTS = 3
OFF_VERIFY_DELAY = 2

ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


class PSUControlHomeAssistantError(Exception):
    pass


def normalize_address(address):
    """Return (normalized_address, error). Adds http:// when no scheme is given."""
    address = (address or "").strip().rstrip("/")
    if not address:
        return "", "Address is empty"
    if "://" not in address:
        address = "http://" + address
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https"):
        return address, "Address must start with http:// or https://"
    if not parts.netloc:
        return address, "Address has no host"
    if parts.query or parts.fragment:
        return address, "Address must not contain ? or #"
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")), None


def normalize_entity_id(entity_id):
    """Return (entity_id, domain, error). A bare name is treated as switch.<name>."""
    entity_id = (entity_id or "").strip().lower()
    if not entity_id:
        return "", "", "Entity ID is empty"
    if "." not in entity_id:
        entity_id = "switch." + entity_id
    if not ENTITY_ID_RE.match(entity_id):
        return entity_id, "", "Entity ID must look like switch.my_plug"
    domain = entity_id.split(".", 1)[0]
    # Groups don't have turn_on/off/toggle services
    if domain == "group":
        domain = "homeassistant"
    return entity_id, domain, None


def mask_secret(value):
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


class PSUControl_HomeAssistant(octoprint.plugin.StartupPlugin,
                               octoprint.plugin.TemplatePlugin,
                               octoprint.plugin.AssetPlugin,
                               octoprint.plugin.SimpleApiPlugin,
                               octoprint.plugin.SettingsPlugin,
                               octoprint.plugin.RestartNeedingPlugin):

    def __init__(self):
        self.config = dict()
        self._verify_lock = threading.Lock()

    ##~~ Settings

    def get_settings_defaults(self):
        return dict(
            address='',
            api_key='',
            entity_id='',
            verify_certificate=True,
        )

    def get_settings_restricted_paths(self):
        # The HA token can call any service in Home Assistant. Only admins may read it.
        return dict(admin=[["api_key"]])

    def get_settings_version(self):
        return 2

    def on_settings_migrate(self, target, current=None):
        if current is None or current < 2:
            address, _ = normalize_address(self._settings.get(["address"]))
            if address:
                self._settings.set(["address"], address)

    def on_settings_initialized(self):
        self.reload_settings()

    def on_settings_save(self, data):
        if "address" in data:
            data["address"], error = normalize_address(data["address"])
            if error:
                self._notify("error", "Address: {}".format(error))
        if "entity_id" in data:
            _, _, error = normalize_entity_id(data["entity_id"])
            if error:
                self._notify("error", "Entity ID: {}".format(error))
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self.reload_settings()

    def reload_settings(self):
        for k, v in self.get_settings_defaults().items():
            if isinstance(v, bool):
                v = self._settings.get_boolean([k])
            elif isinstance(v, int):
                v = self._settings.get_int([k])
            elif isinstance(v, float):
                v = self._settings.get_float([k])
            else:
                v = self._settings.get([k])

            self.config[k] = v
            self._logger.debug("{}: {}".format(k, mask_secret(v) if k == "api_key" else v))

    ##~~ Startup

    def on_startup(self, host, port):
        psucontrol_helpers = self._plugin_manager.get_helpers("psucontrol")
        if not psucontrol_helpers or 'register_plugin' not in psucontrol_helpers.keys():
            self._logger.warning("The version of PSUControl that is installed does not support plugin registration.")
            return

        self._logger.debug("Registering plugin with PSUControl")
        psucontrol_helpers['register_plugin'](self)

    ##~~ Home Assistant API

    def _request(self, method, path, payload=None, config=None):
        """Call the HA REST API. Returns the decoded JSON body or raises PSUControlHomeAssistantError."""
        config = config or self.config
        address, error = normalize_address(config.get('address'))
        if error:
            raise PSUControlHomeAssistantError("Invalid address '{}': {}".format(config.get('address'), error))
        if not config.get('api_key'):
            raise PSUControlHomeAssistantError("Access token is empty")

        url = address + '/api' + path
        headers = {'Authorization': 'Bearer ' + config['api_key']}
        verify = bool(config.get('verify_certificate', True))

        # With verify off urllib3 emits InsecureRequestWarning (once per location by default).
        # Upstream silenced it globally for all of OctoPrint; we leave it visible.
        try:
            response = requests.request(method, url, headers=headers, json=payload,
                                        verify=verify, timeout=REQUEST_TIMEOUT,
                                        allow_redirects=False)
        except requests.exceptions.Timeout:
            raise PSUControlHomeAssistantError("Timed out talking to {}".format(address))
        except requests.exceptions.SSLError as e:
            raise PSUControlHomeAssistantError("TLS error talking to {}: {}".format(address, e))
        except requests.exceptions.ConnectionError as e:
            raise PSUControlHomeAssistantError("Cannot connect to {}: {}".format(address, e))
        except requests.exceptions.RequestException as e:
            raise PSUControlHomeAssistantError("Request to {} failed: {}".format(address, e))

        self._logger.debug("{} {} -> {} {}".format(method, path, response.status_code, response.text[:500]))

        if response.status_code == 401:
            raise PSUControlHomeAssistantError("Home Assistant returned 401 Unauthorized. Check the access token.")
        if response.status_code == 404:
            raise PSUControlHomeAssistantError("Home Assistant returned 404 Not Found for {}. Check the entity ID.".format(path))
        if 300 <= response.status_code < 400:
            raise PSUControlHomeAssistantError(
                "Home Assistant answered with a redirect to {}. Use the direct LAN address of HA."
                .format(response.headers.get('Location')))
        if not 200 <= response.status_code < 300:
            raise PSUControlHomeAssistantError("Home Assistant returned HTTP {}: {}".format(
                response.status_code, response.text[:200]))

        try:
            return response.json()
        except ValueError:
            raise PSUControlHomeAssistantError("Home Assistant did not return JSON. Is the address pointing at HA?")

    def _entity(self, config=None):
        config = config or self.config
        entity_id, domain, error = normalize_entity_id(config.get('entity_id'))
        if error:
            raise PSUControlHomeAssistantError("Invalid entity ID '{}': {}".format(config.get('entity_id'), error))
        return entity_id, domain

    def _read_state(self, config=None):
        entity_id, _ = self._entity(config)
        data = self._request('GET', '/states/' + entity_id, config=config)
        state = data.get('state') if isinstance(data, dict) else None
        if state is None:
            raise PSUControlHomeAssistantError("No state in Home Assistant's answer for {}".format(entity_id))
        return state

    def change_psu_state(self, state):
        entity_id, domain = self._entity()
        service = 'turn_' + state if state else 'toggle'
        self._request('POST', '/services/{}/{}'.format(domain, service), payload={'entity_id': entity_id})

    ##~~ PSU Control sub-plugin interface

    def turn_psu_on(self):
        self._logger.debug("Switching PSU On")
        try:
            self.change_psu_state('on')
        except PSUControlHomeAssistantError as e:
            self._logger.error("Turning PSU on failed: {}".format(e))
            self._notify("error", "Turning the printer ON failed: {}".format(e))

    def turn_psu_off(self):
        self._logger.debug("Switching PSU Off")
        try:
            self.change_psu_state('off')
        except PSUControlHomeAssistantError as e:
            self._logger.error("Turning PSU off failed: {}".format(e))
        # Verify (and retry) in the background so callers such as the API handler aren't held up.
        threading.Thread(target=self._verify_off, name="psucontrol_homeassistant_verify_off",
                         daemon=True).start()

    def _verify_off(self):
        if not self._verify_lock.acquire(blocking=False):
            return  # a verification is already running
        try:
            last_error = None
            for attempt in range(1, OFF_VERIFY_ATTEMPTS + 1):
                time.sleep(OFF_VERIFY_DELAY)
                try:
                    state = self._read_state()
                    if state == 'off':
                        if attempt > 1:
                            self._logger.info("PSU confirmed off after {} attempts".format(attempt))
                        return
                    last_error = "entity is still '{}'".format(state)
                except PSUControlHomeAssistantError as e:
                    last_error = str(e)
                self._logger.warning("PSU not confirmed off (attempt {}/{}): {}. Retrying.".format(
                    attempt, OFF_VERIFY_ATTEMPTS, last_error))
                try:
                    self.change_psu_state('off')
                except PSUControlHomeAssistantError as e:
                    last_error = str(e)
            self._logger.error("PSU could NOT be confirmed off after {} attempts: {}".format(
                OFF_VERIFY_ATTEMPTS, last_error))
            self._notify("error", "Printer power could NOT be confirmed OFF: {}".format(last_error), sticky=True)
        finally:
            self._verify_lock.release()

    def get_psu_state(self):
        # PSU Control only understands True/False, so "unreachable" has to be reported
        # as False. Log it at ERROR so it never looks like a normal "off".
        try:
            return self._read_state() == 'on'
        except PSUControlHomeAssistantError as e:
            self._logger.error("Unable to read PSU state, reporting OFF: {}".format(e))
            return False

    ##~~ UI notifications

    def _notify(self, level, text, sticky=False):
        self._plugin_manager.send_plugin_message(self._identifier, dict(
            type="notify", level=level, text=text, sticky=sticky))

    ##~~ SimpleApiPlugin: "Test connection" button

    def get_api_commands(self):
        return dict(test=[])

    def is_api_protected(self):
        return True

    def on_api_command(self, command, data):
        if not Permissions.SETTINGS.can():
            flask.abort(403)
        if command != "test":
            return

        # Test the values currently typed in the form, falling back to saved ones.
        config = dict(self.config)
        for k in ("address", "api_key", "entity_id", "verify_certificate"):
            if data.get(k) not in (None, ""):
                config[k] = data[k]

        try:
            api = self._request('GET', '/', config=config)
            state = self._read_state(config)
        except PSUControlHomeAssistantError as e:
            return flask.jsonify(ok=False, message=str(e))

        entity_id, _ = self._entity(config)
        return flask.jsonify(ok=True, message="Connected ({}). {} is '{}'.".format(
            api.get('message', 'API running'), entity_id, state))

    ##~~ Assets and templates

    def get_assets(self):
        return dict(js=["js/psucontrol_homeassistant.js"])

    def is_template_autoescaped(self):
        # The settings template has no Jinja variables; opt in before OctoPrint 1.13 enforces it.
        return True

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=True)
        ]

    ##~~ Software Update hook

    def get_update_information(self):
        return dict(
            psucontrol_homeassistant=dict(
                displayName="PSU Control - Home Assistant",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="jared-parrish321",
                repo="OctoPrint-PSUControl-HomeAssistant",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/jared-parrish321/OctoPrint-PSUControl-HomeAssistant/archive/{target_version}.zip"
            )
        )


__plugin_name__ = "PSU Control - Home Assistant"
__plugin_pythoncompat__ = ">=3.8,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = PSUControl_HomeAssistant()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
