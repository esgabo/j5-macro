// Minimal extension code for the j5 debugger: plain JavaScript, no build step.
//
// It only decides how to start the debug adapter (the `j5 dap` command, which speaks
// the Debug Adapter Protocol on stdio) and fills in a configuration when F5 is pressed
// without a launch.json. Everything else happens in the Python adapter.
//
// Adapter command, first match wins:
//   1. the launch configuration's "python": <python> -m j5_macro.dap
//   2. the "j5.adapterCommand" setting, e.g. ["/path/to/venv/bin/j5", "dap"]
//   3. j5 dap, from the PATH
"use strict";

const vscode = require("vscode");

function adapterCommand(session) {
  const config = session.configuration;
  if (typeof config.python === "string" && config.python.length > 0) {
    return [config.python, "-m", "j5_macro.dap"];
  }
  const setting = vscode.workspace
    .getConfiguration("j5", session.workspaceFolder)
    .get("adapterCommand");
  if (Array.isArray(setting) && setting.length > 0) {
    return setting.map(String);
  }
  return ["j5", "dap"];
}

function activate(context) {
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory("j5", {
      createDebugAdapterDescriptor(session) {
        const [command, ...args] = adapterCommand(session);
        const options = {};
        if (session.workspaceFolder) {
          options.cwd = session.workspaceFolder.uri.fsPath;
        }
        return new vscode.DebugAdapterExecutable(command, args, options);
      },
    }),
    vscode.debug.registerDebugConfigurationProvider("j5", {
      resolveDebugConfiguration(folder, config) {
        // F5 without a launch.json: debug the YAML file in the active editor.
        if (!config.type && !config.request && !config.name) {
          const editor = vscode.window.activeTextEditor;
          if (editor && editor.document.languageId === "yaml") {
            config.type = "j5";
            config.name = "Debug j5 script";
            config.request = "launch";
            config.program = "${file}";
            config.stopOnEntry = true;
          }
        }
        if (!config.program) {
          return vscode.window
            .showInformationMessage("Set \"program\" to the j5 YAML script to debug.")
            .then(() => undefined);
        }
        return config;
      },
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
