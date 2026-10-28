$(function () {
    function PSUControlHomeAssistantViewModel(parameters) {
        var self = this;
        var PLUGIN = "psucontrol_homeassistant";

        self.settingsViewModel = parameters[0];
        self.loginState = parameters[1];
        self.settings = undefined;

        // The access token field is write-only: it starts empty and only replaces the
        // saved token when something is typed into it.
        self.newToken = ko.observable("");
        self.tokenIsSet = ko.pureComputed(function () {
            var s = self.settings && self.settings.plugins && self.settings.plugins[PLUGIN];
            return !!(s && s.api_key());
        });

        self.testBusy = ko.observable(false);
        self.testResult = ko.observable("");
        self.testOk = ko.observable(false);

        self.onBeforeBinding = function () {
            self.settings = self.settingsViewModel.settings;
        };

        self.onSettingsShown = function () {
            self.newToken("");
        };

        self.onSettingsBeforeSave = function () {
            var token = self.newToken().trim();
            if (token) {
                self.settings.plugins[PLUGIN].api_key(token);
                self.newToken("");
            }
        };

        self.testConnection = function () {
            var s = self.settings.plugins[PLUGIN];
            self.testBusy(true);
            self.testResult("");
            OctoPrint.simpleApiCommand(PLUGIN, "test", {
                address: s.address(),
                // Empty means "use the saved token" on the server side
                api_key: self.newToken().trim(),
                entity_id: s.entity_id(),
                verify_certificate: s.verify_certificate()
            })
                .done(function (response) {
                    self.testOk(response.ok);
                    self.testResult(response.message);
                })
                .fail(function (xhr) {
                    self.testOk(false);
                    self.testResult("Test request failed (HTTP " + xhr.status + ")");
                })
                .always(function () {
                    self.testBusy(false);
                });
        };

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin !== PLUGIN || !data || data.type !== "notify") return;
            if (!self.loginState.isUser()) return;
            new PNotify({
                title: "PSU Control - Home Assistant",
                text: data.text,
                type: data.level === "error" ? "error" : "notice",
                hide: !data.sticky
            });
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: PSUControlHomeAssistantViewModel,
        dependencies: ["settingsViewModel", "loginStateViewModel"],
        elements: ["#settings_plugin_psucontrol_homeassistant"]
    });
});
