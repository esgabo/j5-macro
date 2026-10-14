// Minimal extension code for the j5 debugger: plain JavaScript, no build step.
//
// It only decides how to start the debug adapter (the `j5 dap` command, which speaks
// the Debug Adapter Protocol on stdio) and fills in a configuration when F5 is pressed
// without a launch.json. Everything else happens in the Python adapter.
//
// It also adds thin commands over the j5 CLI's authoring tools (j5 capture, pick, find
// and pack): each runs the CLI next to the script in the active editor and inserts the
// step it prints at the cursor.
//
// Adapter command, first match wins:
//   1. the launch configuration's "python": <python> -m j5_macro.dap
//   2. the "j5.adapterCommand" setting, e.g. ["/path/to/venv/bin/j5", "dap"]
//   3. j5 dap, from the PATH
"use strict";

const path = require("path");
const { execFile } = require("child_process");
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

// --- authoring commands ----------------------------------------------------------------

// The j5 CLI: the "j5.command" setting, else the adapter command without its "dap",
// else j5 from the PATH.
function cliCommand(folder) {
  const config = vscode.workspace.getConfiguration("j5", folder);
  const command = config.get("command");
  if (Array.isArray(command) && command.length > 0) {
    return command.map(String);
  }
  const adapter = config.get("adapterCommand");
  if (Array.isArray(adapter) && adapter.length > 1 && adapter[adapter.length - 1] === "dap") {
    return adapter.slice(0, -1).map(String);
  }
  return ["j5"];
}

// Runs `j5 args...` in the script's directory; resolves with its exit code and output.
function runJ5(editor, args) {
  const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
  const [command, ...prefix] = cliCommand(folder);
  const cwd = path.dirname(editor.document.uri.fsPath);
  return new Promise((resolve) => {
    execFile(command, [...prefix, ...args], { cwd, maxBuffer: 16 * 1024 * 1024 },
      (error, stdout, stderr) => {
        const code = error ? (typeof error.code === "number" ? error.code : -1) : 0;
        const errors = String(stderr || "") || (error ? error.message : "");
        resolve({ code, stdout: String(stdout), stderr: errors });
      });
  });
}

// The active j5 script, saved (the CLI reads, and may rewrite, the file on disk).
async function savedScript() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.isUntitled || editor.document.languageId !== "yaml") {
    vscode.window.showWarningMessage("Open a saved j5 YAML script first.");
    return undefined;
  }
  if (editor.document.isDirty && !(await editor.document.save())) {
    return undefined;
  }
  return editor;
}

// Inserts the step the CLI printed at the cursor, as a list item on an empty line.
async function insertStep(editor, stdout) {
  const step = stdout.trim();
  if (!step) {
    return;
  }
  await editor.edit((edit) => {
    const position = editor.selection.active;
    const before = editor.document.lineAt(position.line).text.slice(0, position.character);
    edit.insert(position, before.trim() === "" ? `- ${step}` : step);
  });
}

// Reloads the file after the CLI changed it on disk (it was saved just before, so
// nothing is lost).
async function reload(editor) {
  await vscode.window.showTextDocument(editor.document, editor.viewColumn);
  await vscode.commands.executeCommand("workbench.action.files.revert");
  return vscode.window.activeTextEditor || editor;
}

function lastLine(text) {
  const lines = text.trim().split(/\r?\n/);
  return lines[lines.length - 1].replace(/^j5: /, "");
}

// Shows a failure; true when the command succeeded.
function report(result, title) {
  if (result.code === 3) {
    vscode.window.showInformationMessage(`${title}: cancelled.`);
  } else if (result.code !== 0) {
    vscode.window.showErrorMessage(`${title} failed: ${lastLine(result.stderr) || "see the j5 CLI"}`);
  }
  return result.code === 0;
}

async function captureTemplate() {
  let editor = await savedScript();
  if (!editor) {
    return;
  }
  const name = await vscode.window.showInputBox({
    prompt: "Name of the template (letters, digits, _ and -)",
    value: "image",
    validateInput: (value) => (/^[A-Za-z_][A-Za-z0-9_-]*$/.test(value) ? undefined
      : "Letters, digits, _ and -, not starting with a digit or -"),
  });
  if (!name) {
    return;
  }
  const where = await vscode.window.showQuickPick([
    { label: `Embed in this script as #${name}`, embed: true },
    { label: `Save as img/${name}.png next to the script`, embed: false },
  ], { placeHolder: "Where should the template go?" });
  if (!where) {
    return;
  }
  const file = editor.document.uri.fsPath;
  let args = ["capture", "--embed", file, "--name", name];
  if (!where.embed) {
    const images = vscode.Uri.file(path.join(path.dirname(file), "img"));
    await vscode.workspace.fs.createDirectory(images);
    args = ["capture", "-o", path.join("img", `${name}.png`)];
  }
  const result = await runJ5(editor, args);
  if (!report(result, "j5 capture")) {
    return;
  }
  if (where.embed) {
    editor = await reload(editor);
  }
  await insertStep(editor, result.stdout);
}

async function pickPoint() {
  const editor = await savedScript();
  if (!editor) {
    return;
  }
  const result = await runJ5(editor, ["pick"]);
  if (report(result, "j5 pick")) {
    await insertStep(editor, result.stdout);
  }
}

async function findImage() {
  const editor = await savedScript();
  if (!editor) {
    return;
  }
  const selected = editor.document.getText(editor.selection).trim().replace(/^["']|["']$/g, "");
  const image = await vscode.window.showInputBox({
    prompt: "Template to find: a path relative to the script, or #name for an embedded image",
    value: selected,
  });
  if (!image) {
    return;
  }
  const file = editor.document.uri.fsPath;
  const result = await runJ5(editor, ["find", image, "--script", file, "--show"]);
  if (result.code === 1 && result.stdout.trim() === "" && /not found/.test(result.stderr)) {
    vscode.window.showWarningMessage(lastLine(result.stderr));
    return;
  }
  if (report(result, "j5 find")) {
    vscode.window.showInformationMessage(lastLine(result.stderr));
    await insertStep(editor, result.stdout);
  }
}

async function packScript() {
  const editor = await savedScript();
  if (!editor) {
    return;
  }
  const file = editor.document.uri.fsPath;
  const result = await runJ5(editor, ["pack", file, "-o", file]);
  if (report(result, "j5 pack")) {
    await reload(editor);
    vscode.window.showInformationMessage(lastLine(result.stderr));
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("j5.captureTemplate", captureTemplate),
    vscode.commands.registerCommand("j5.pickPoint", pickPoint),
    vscode.commands.registerCommand("j5.findImage", findImage),
    vscode.commands.registerCommand("j5.packScript", packScript),
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
