# OctoPrint PSU Control - Home Assistant
Adds Home Assistant support to OctoPrint-PSUControl as a sub-plugin.

This is a maintained fork of [edekeijzer/OctoPrint-PSUControl-HomeAssistant](https://github.com/edekeijzer/OctoPrint-PSUControl-HomeAssistant), which is archived. Changes since upstream 1.0.6 are listed under [Changes in this fork](#changes-in-this-fork).

## Install
In OctoPrint go to *Settings → Plugin Manager → Get More → from URL* and enter:

```
https://github.com/jared-parrish321/OctoPrint-PSUControl-HomeAssistant/archive/main.zip
```

Or from a shell on the OctoPrint host (OctoPi):

```sh
~/oprint/bin/pip install --upgrade https://github.com/jared-parrish321/OctoPrint-PSUControl-HomeAssistant/archive/main.zip
sudo service octoprint restart
```

## Setup
- Configure this plugin (see below) and press **Test connection**
- Select this plugin as Switching *and* Sensing method in [PSU Control](https://github.com/kantlivelong/OctoPrint-PSUControl)
- *Automatically turn PSU ON* in PSU Control: upstream warned to turn this off, because when Home Assistant was unreachable the PSU was reported as off, so every queued G-code triggered a blocking turn-on call and stalled the print (upstream issues
[#4](https://github.com/edekeijzer/OctoPrint-PSUControl-HomeAssistant/issues/4),
[#11](https://github.com/edekeijzer/OctoPrint-PSUControl-HomeAssistant/issues/11),
[#16](https://github.com/edekeijzer/OctoPrint-PSUControl-HomeAssistant/issues/16)).
Since 1.2.0, while a job is running (printing, pausing, paused, resuming or cancelling) the plugin reports the PSU as on if HA can't be reached and skips turn-on calls, so auto-on can no longer stall a print. Outside a job, an auto-on trigger still waits up to the request timeout (about 8 s worst case) if HA is down.

## Configuration
* Enter the URL of your Home Assistant installation, e.g. `http://192.168.1.10:8123`. If you leave out `http://` it is added when you save. Use the direct LAN address; a URL behind a login page (such as Cloudflare Access) won't work.
* In Home Assistant, ideally create a dedicated user for OctoPrint, log in as it and open its profile
* On the *Security* tab, go to *Long-Lived Access Tokens*
* Click *Create Token*, give the token a name (suggestion: OctoPrint PSUControl) and click *OK*
* Copy the token and paste it into the *Access token* field in the plugin settings. Only OctoPrint admins can read it back.
* At *Entity ID* enter the ID of the Home Assistant entity you want to control (for example: *switch.my_smart_outlet*)
* If your HA installation is running HTTPS with a self-signed certificate, uncheck the *Verify certificate* option
* Press **Test connection**. It shows whether HA answered and the entity's current state.

## Behaviour
* Every request to Home Assistant times out after 3 s (connect) / 5 s (read), so a hung HA can't block PSU Control.
* After a turn-off, the plugin checks the entity's state up to 3 times (2 s apart) and sends the off command again if needed. If the PSU still can't be confirmed off, a sticky error notification appears in OctoPrint and an ERROR is logged.
* If HA can't be reached, the PSU is reported as **on during a job** (the printer is clearly powered) and **off otherwise**. An ERROR is logged and a notification shown once when HA goes away, and again when it comes back.
* Turn-on requests are skipped while a job is running. Turn-off requests are always sent.

## Support
Please check your logs first. If they do not explain your issue, open an issue in GitHub. Please set *octoprint.plugins.psucontrol* and *octoprint.plugins.psucontrol_homeassistant* to **DEBUG** and include the relevant logs. The access token is masked in this plugin's log lines, but check the log for anything else private before posting it.

## Changes in this fork
* 1.2.0
  * Safe with *Automatically turn PSU ON*: HA outages during a job no longer stall the print
  * One notification when HA becomes unreachable and when it recovers, instead of an ERROR every poll
  * Browsers no longer autofill the OctoPrint login into the Address and Access token fields
* 1.1.0
  * Request timeouts, response status checks and JSON validation
  * Turn-off is verified and retried, with a visible notification on failure
  * Access token is restricted to admins, masked in logs and hidden in the settings form
  * Address and Entity ID are validated and normalised; new **Test connection** button
  * TLS warnings are no longer silenced for all of OctoPrint
  * Software update checks point at this fork
  * Packaging moved from `setup.py` to `pyproject.toml`

## License
AGPLv3, see [LICENSE](LICENSE). Original work Copyright (C) 2021 Erik de Keijzer; modified by jared-parrish321.
