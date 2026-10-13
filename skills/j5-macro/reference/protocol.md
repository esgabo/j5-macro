# Driving the j5 engine directly (JSON lines)

Most agents should use the `j5` command. Speak the engine's protocol only when you need
a long-lived session: several runs, pausing and stepping, editing scripts by step id, or
authoring tools without starting a process each time. The specification is
`docs/contract.md` (https://github.com/esgabo/j5-macro/blob/main/docs/contract.md).

## Session

Start `python -m j5_macro.engine` (the Python that has j5 installed; options
`--runs-dir DIR`, `--stop-key COMBO`) and exchange one JSON object per line on its
standard input and output. It emits a `ready` event, then expects `hello`. It exits when
its standard input closes, stopping an active run first.

```console
$ printf '%s\n' \
  '{"v":1,"kind":"command","type":"hello","id":"1","payload":{"v_min":1,"v_max":1,"client":"agent"}}' \
  '{"v":1,"kind":"command","type":"load","id":"2","payload":{"source":"version: 1\nsteps:\n  - id: hi\n    log: hello\n"}}' \
  '{"v":1,"kind":"command","type":"run","id":"3","payload":{"script_id":"s1","mode":"dry"}}' \
  | J5_BACKEND=fake python -m j5_macro.engine
```

- **Commands**: `{"v":1, "kind":"command", "type":..., "id":"<yours>", "payload":{...}}`.
- **Responses**: `{"kind":"response", "id":<same>, "ok":true, "result":{...}}` or
  `"ok":false, "error":{code, message, details?}`. Match them by `id`.
- **Events**: `{"kind":"event", "type":..., "run_id":..., "seq":..., "payload":{...}}`,
  the same events `j5 run --json` prints. Ignore unknown types and fields.
- `J5_BACKEND=fake` records input and touches nothing: useful to try the protocol.

## Commands you are likely to need

| Command | Payload | Result |
| --- | --- | --- |
| `hello` | `v_min`, `v_max`, `client` | `v`, `capabilities` (backend, step types, `vision`, display scales) |
| `load` | `path` or `source` (YAML text) | `script_id`, steps, params, images, warnings |
| `validate` | `script_id` | `ok`, `errors`, `warnings` |
| `run` | `script_id`, `mode` (`normal`, `step`, `dry`), `params?`, `vars?`, `seed?`, `input_profile?`, `allow_exec?` (false), `display_scale?`, `captures?` | `run_id`; then events until `run_finished` |
| `pause`, `resume`, `stop` | `run_id` | Control the active run. `stop` releases held keys |
| `step_over`, `step_into`, `step_out` | `run_id` | Stepping while paused |
| `get_state`, `evaluate`, `set_variable` | `run_id`, ... | Inspect while paused (secrets masked) |
| `list_windows` | — | `windows: [{title, bbox}]` |
| `screenshot` | `path?`, `window?`, `region?` | `path`, `bbox`, `display_scale`, `window?`, `window_bbox?` |
| `capture_region` | `bbox`, `window?`, and `path` or `script_id`+`name`, `padding?`, `transparent?`, `overwrite?` | `path` or `image_name`+`version`, `bbox`, ... |
| `find_image` | `image`, `script_id?`, `window?`, `region?`, `threshold?`, `scale?`, `display_scale?` | `found`, `score`, `bbox`, `center`, ... |
| `get_script`, `edit_script` | structured edits by step id (`insert`, `update`, `move`, `delete`, `set_meta`) | new `version`, `valid`, `errors` |
| `list_runs`, `get_run` | — / `run_id` | the saved run history |

`window` payloads are `{"title": "..."}`. Only one run at a time (`busy` otherwise).
`allow_exec: true` is the protocol form of `--allow-exec`: the same consent rule applies.

From Python, `j5_macro.client.EngineClient` (asyncio) starts the engine and handles the
framing.
