# j5 Macro Debugger for VS Code

Debug [j5-macro](https://github.com/esgabo/j5-macro) YAML scripts in VS Code: breakpoints on
steps, stop on entry, step over/into/out, the call stack of enclosing `loop`/`if` blocks,
variables (secrets show as `***`), watch expressions and hovers, and the run's log in the
Debug Console.

The extension has no build step: `package.json` declares the `j5` debugger and
`extension.js` (plain JavaScript) only chooses how to start the adapter, `j5 dap`, which
speaks the Debug Adapter Protocol on stdio. The mapping between DAP and the engine is
described in [`docs/contract.md`](../../docs/contract.md), "Debug Adapter Protocol".

## Install it locally

1. Install j5-macro so the `j5` command works (in a virtual environment is fine):

   ```bash
   pip install -e ".[x11,vision]"     # from the repository root
   j5 dap --help
   ```

2. Make the extension visible to VS Code, either by linking this folder into the
   extensions directory (then restart VS Code):

   ```bash
   # Linux and macOS
   ln -s "$PWD/editors/vscode" ~/.vscode/extensions/j5-macro.j5-macro-debug-0.0.1
   ```

   ```powershell
   # Windows (PowerShell; a junction needs no administrator rights)
   New-Item -ItemType Junction -Path "$env:USERPROFILE\.vscode\extensions\j5-macro.j5-macro-debug-0.0.1" -Target "$PWD\editors\vscode"
   ```

   or by packaging it with [vsce](https://github.com/microsoft/vscode-vsce) and installing
   the `.vsix`:

   ```bash
   cd editors/vscode
   npx @vscode/vsce package
   code --install-extension j5-macro-debug-0.0.1.vsix
   ```

3. If `j5` is not on the `PATH` VS Code sees (for example, it lives in a virtual
   environment that is not activated), tell the extension where it is, either with the
   setting

   ```json
   "j5.adapterCommand": ["/path/to/venv/bin/j5", "dap"]
   ```

   or with `"python": "/path/to/venv/bin/python"` in the launch configuration (the
   adapter then starts as `python -m j5_macro.dap`).

## Debug a script

Open a j5 YAML script and press F5: without a `launch.json` the extension debugs the file
in the active editor and stops before its first step. Click in the gutter to set
breakpoints: a breakpoint on any line of a step (its `- id:` line, its parameters, a
comment just above it) moves to the line where the step starts.

An example `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "j5",
      "request": "launch",
      "name": "Debug j5 script",
      "program": "${file}",
      "stopOnEntry": true
    },
    {
      "type": "j5",
      "request": "launch",
      "name": "Fill the form (dry run)",
      "program": "${workspaceFolder}/scripts/form.yaml",
      "dryRun": true,
      "params": {"customer": "ACME #42", "copies": 2},
      "paramsFromEnv": {"password": "FORM_PASSWORD"},
      "seed": 7
    }
  ]
}
```

Launch attributes:

| Attribute | Meaning |
| --- | --- |
| `program` | The YAML script (required) |
| `stopOnEntry` | Pause before the first step |
| `params` | Values for the script's `params` |
| `paramsFromEnv` | Params read from environment variables (param name to variable name); use it for secrets so they stay out of `launch.json` |
| `seed` | Seed of the run, to repeat the same human-like input |
| `dryRun` | Send no input; input steps describe what they would do |
| `fast` | Direct mouse and keyboard strategy instead of human-like movement |
| `noDebug` | Run without stopping (Run Without Debugging sets it) |
| `cwd` | Directory a relative `program` is resolved against |
| `python` | Start the adapter with this interpreter (`python -m j5_macro.dap`) |

In the Debug Console you can type expressions in the j5 expression language (for example
`attempts + 1` or `len(items)`); they are evaluated while the run is paused. Values typed in
the Variables view are read as JSON (`5`, `"text"`, `[1, 2]`, `true`), and anything that is
not JSON is taken as text.

Stopping the debug session stops the run and releases every key and button it held. The
emergency stop key (`ctrl+alt+shift+q`) and the screen-corner failsafe work as in `j5 run`.
