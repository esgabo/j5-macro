# The j5-browser container

`j5-browser` is a Docker image with j5, Chromium and a virtual desktop. It starts on
demand, runs one j5 script against a Chromium window, leaves the results (files,
downloads, text, screenshots and j5's events) in a folder and exits with j5's exit code.
It suits schedulers and workflow tools such as n8n, cron or CI. The same image keeps
the desktop up, viewable in a web browser, to write and debug scripts.

It contains:

| Part | What it is |
| --- | --- |
| [KasmVNC](https://github.com/kasmtech/KasmVNC) | The X server (Xvnc) j5 drives, which can also show the desktop in a web browser |
| openbox | A small window manager: every window maximised, without decorations |
| Chromium | Debian's Chromium, started the same way every run (see [Chromium](#chromium)) |
| j5 | Installed from this repository, with the `x11` and `vision` extras |

The files are in [docker/j5-browser](../docker/j5-browser/).

## Build

From the repository root:

```console
$ docker build -f docker/j5-browser/Dockerfile -t j5-browser .
```

The image is based on `debian:trixie-slim` (`--build-arg BASE_IMAGE=...` uses another
registry's copy of it) and installs j5 from the checkout, so it always has the j5 of the
commit it is built from. It is built and tested on `linux/amd64`; the Dockerfile also
knows the `arm64` KasmVNC package, but that architecture is not tested yet.

## Run a job

A job is a folder with the script and what it needs:

```text
job/
  script.yaml        the j5 script (required)
  params.yaml        its params (optional)
  img/...            template images it references, or embed them with j5 pack
```

```console
$ mkdir -p out
$ docker run --rm --shm-size=1g --user "$(id -u):$(id -g)" \
    -v "$PWD/job:/job:ro" -v "$PWD/out:/out" \
    -e START_URL=https://example.com \
    -e J5_PARAM_ENV=password=SITE_PASSWORD -e SITE_PASSWORD \
    j5-browser run
$ echo $?
0
```

The container starts the desktop and Chromium (on `START_URL`), runs
`j5 run --json /job/script.yaml` with standard input closed, writes the results and exits.
`run` is the default command and can be left out.

### Results

| Path in `/out` | Content |
| --- | --- |
| `result.json` | A summary: `exit_code`, `status` (`completed`, `failed`, `stopped`, `timeout`), `reason`, `started`, `finished`, and `files`, the files the job produced |
| `events.jsonl` | Every event of the run, one JSON line each ([JSON output](cli.md#json-output)) |
| `j5.log` | j5's own messages (its standard error) |
| `downloads/` | Everything Chromium downloads: PDFs (they download instead of opening), CSV exports, files |
| `runs/` | The run's history, with screen captures of failed image searches ([j5 runs](cli.md#j5-runs)) |
| anything else | What the script writes itself with [`exec`](scripts.md#exec) steps |

`result.json` for a successful job:

```json
{
  "exit_code": 0,
  "status": "completed",
  "reason": null,
  "started": "2026-10-15T11:46:15+00:00",
  "finished": "2026-10-15T11:46:22+00:00",
  "files": ["clipboard.txt", "downloads/report.pdf", "screen.png"]
}
```

### Exit codes

The container exits with j5's [exit code](cli.md#exit-codes): 0 completed, 1 failed,
2 the script could not be loaded or started (also: no script in `/job`, `/out` not
writable, the display did not start), 3 stopped, 130 interrupted (`docker stop`). It
exits with 124 when the job runs longer than `JOB_TIMEOUT`.

### Settings

Every setting is an environment variable (`docker run -e NAME=value`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `START_URL` | `about:blank` | The page Chromium opens |
| `START_BROWSER` | `1` | `0`: do not start Chromium; the script starts it with an `exec` step running `j5-chromium URL` |
| `J5_SCRIPT` | `/job/script.yaml` | The script to run |
| `J5_PARAMS_FILE` | `/job/params.yaml` | Params file, used when it exists (`--params-file`) |
| `J5_PARAM_ENV` | - | Params from environment variables: `name=VARIABLE,name2=VARIABLE2` (`--param-env`). The way to pass secrets |
| `J5_FAST` | `0` | `1`: instant input instead of human-like movement and typing (`--fast`) |
| `J5_ALLOW_EXEC` | `0` | `1`: let the script's `exec` steps run programs (`--allow-exec`) |
| `J5_SEED` | - | Seed of the human-like input (`--seed`) |
| `J5_DISPLAY_SCALE` | - | Display scale (`--display-scale`); the desktop is at 1 |
| `J5_ARGS` | - | Other `j5 run` options, split on spaces: `--var retries=5` |
| `JOB_TIMEOUT` | `10m` | Stop the job after this long (exit code 124); a [`timeout`](https://www.gnu.org/software/coreutils/timeout) duration |
| `GEOMETRY` | `1280x800` | Size of the desktop and the Chromium window |
| `CHROMIUM_KIOSK` | `0` | `1`: Chromium full screen, without tabs or address bar |
| `CHROMIUM_PROFILE` | a new one | A folder for Chromium's profile, to keep logins and cookies between jobs (mount a volume there) |
| `CHROMIUM_SANDBOX` | `auto` | `auto`, `1` or `0`: see [Chromium](#chromium) |
| `CHROMIUM_FLAGS` | - | More Chromium flags, split on spaces |
| `VNC_PASSWORD` | - | Password of the web desktop (user `j5`, or `VNC_USER`). Required by `author`; with `run` it makes the desktop viewable while the job runs |
| `VNC_PORT` | `6901` | Port of the web desktop inside the container |

Give secrets only through `J5_PARAM_ENV` and secret params: they are then masked as `***`
in `events.jsonl`, `j5.log` and the run history ([secrets](scripts.md#params-and-secrets)).

### Files and users

The image's user is `j5` (uid 1000), and `/out` must be writable by the user the
container runs as. Run it as yourself with `--user "$(id -u):$(id -g)"` so the results
belong to you; any uid works. Run several jobs at once in separate containers, each with
its own `/out`: each container has its own display, so their mice and keyboards do not
interfere.

## Write and debug scripts: author mode

Scripts must be written against the screen they will see, so capture templates in the
container, not on your own desktop: fonts, the Chromium version and the screen size all
change what pages look like.

```console
$ docker run --rm -d --name j5-author --shm-size=1g -p 127.0.0.1:6901:6901 \
    -e VNC_PASSWORD=choose-one -v "$PWD/job:/job" j5-browser author
```

Open `http://localhost:6901` (user `j5`, the password you chose) to see the desktop and
use it. Then run j5 on it with `docker exec`, exactly as on a desktop
([authoring tools](cli.md#authoring-tools)):

```console
$ docker exec -it j5-author j5 windows
$ docker exec -it j5-author j5 capture --window Chromium --embed /job/script.yaml --name login
$ docker exec -it j5-author j5 find '#login' --script /job/script.yaml --window Chromium
$ docker exec -it j5-author j5 run --step /job/script.yaml
```

`j5 capture` and `j5 pick` show their overlay on the container's desktop, in the web
page. Without a password the author mode refuses to start; `VNC_NO_AUTH=1` overrides
that, for a private network only. The web desktop has no TLS: keep port 6901 on
`127.0.0.1`, or put it behind a proxy with HTTPS.

`docker run ... j5-browser shell` starts the desktop and opens a shell on it instead, and
any other command runs on the desktop: `docker run --rm j5-browser j5 windows --json`.

## Docker Compose

[docker/j5-browser/compose.yaml](../docker/j5-browser/compose.yaml) has both modes, with
`./job` and `./out` next to it:

```console
$ cd docker/j5-browser
$ mkdir -p job out
$ export J5_UID=$(id -u) J5_GID=$(id -g)
$ VNC_PASSWORD=choose-one docker compose up author       # http://localhost:6901
$ docker compose run --rm job                            # one job; exit code as above
```

Copy it next to your own job folder to use it elsewhere. `J5_JOB_DIR` and `J5_OUT_DIR`
move the folders, and `J5_IMAGE` names an image built elsewhere.

## Scripts for the container

- **Begin by waiting.** Chromium takes a moment to start. Start with `wait_window_in`
  (Chromium's titles end in `- Chromium`) or `wait_image_in` and a generous timeout,
  never a fixed `sleep`.
- **Downloads** land in `/out/downloads` without a dialog. To wait for one, use an
  `exec` step that checks for the file (below). Chromium briefly shows a downloads
  bubble at the top right of the page after each download; `key: escape` closes it.
- **Text from a page:** select and copy it in the page (`key: ctrl+a` or a drag, then
  `key: ctrl+c`), then save the clipboard with `xclip`, retrying for a moment: Chromium
  fills the clipboard shortly after the key press.
- **A picture of the screen:** `j5 screenshot -o /out/name.png`, or
  `--window Chromium` for the window only.
- **`--fast`** (`J5_FAST=1`) when nobody needs the input to look human.

`exec` steps need `J5_ALLOW_EXEC=1`. A script that saves a page's text and waits for a
download:

```yaml
version: 1
name: Save a report
params:
  user: {description: Account to sign in with}
  password: {secret: true}
steps:
  - id: wait_page
    wait_window_in: {title: Reports, timeout: 30s}
  - id: sign_in
    type: "${user}\t${password}\n"
  - id: copy_summary
    key: ctrl+a
  - id: copy
    key: ctrl+c
  - id: save_summary   # retried: Chromium fills the clipboard a moment after ctrl+c
    exec:
      command: >-
        for i in $(seq 1 50); do
        xclip -o -selection clipboard > /out/summary.txt 2>/dev/null && exit 0; sleep 0.1;
        done; exit 1
      shell: true
      wait: true
  - id: export
    click: {image: img/export_pdf.png}
  - id: wait_pdf
    exec:
      command: for i in $(seq 1 300); do ls /out/downloads/*.pdf && exit 0; sleep 0.2; done; exit 1
      shell: true
      wait: true
      timeout: 70s
```

## Chromium

`j5-chromium` starts Chromium the same way every time, so pages look the same from run to
run and templates keep matching:

- a new, empty profile for every job (unless `CHROMIUM_PROFILE` is set), no first-run
  pages, sign-in, password manager, translation bar or notifications;
- the window maximised at the desktop's size, at scale 1, with the GPU off;
- downloads sent to `/out/downloads` without asking, PDFs downloaded instead of shown
  (Chromium's managed policies in
  [j5.json](../docker/j5-browser/rootfs/etc/chromium/policies/managed/j5.json));
- the same fonts and font rendering in every container.

The **sandbox**: Chromium's sandbox needs user namespaces, which Docker's default
security profile may block. With `CHROMIUM_SANDBOX=auto` the container checks, and starts
Chromium without its sandbox when it cannot have one, saying so on the container's
standard error. A
one-job container that is thrown away afterwards limits the damage a malicious page could
do, but for untrusted sites prefer a host where the sandbox works (`CHROMIUM_SANDBOX=1`
makes Chromium fail rather than run without it).

Templates belong to an image: a new Chromium version can change how pages are drawn.
Pin the image you capture with (a tag or digest) and capture again when you update it.

## From n8n and other schedulers

Any tool that can run `docker run` can run a job. With n8n:

1. Write the job folder (`script.yaml`, `params.yaml`) for the execution, for example to
   `/jobs/<execution id>/job`.
2. Start the container with an **Execute Command** node (when n8n can reach Docker) or
   an **SSH** node on the Docker host:
   `docker run --rm --user 1000:1000 -v /jobs/ID/job:/job:ro -v /jobs/ID/out:/out -e ... j5-browser`.
3. Branch on the exit code, or read `/jobs/ID/out/result.json`.
4. Send the files listed in `result.json` (`downloads/...`, your own files) with
   **Read Binary Files** and the **Telegram** (Send Document), **Send Email** or storage
   nodes. On failure, send the newest PNG under `out/runs/` and the `step_failed` event
   from `events.jsonl`.
5. Delete `/jobs/ID` afterwards.

## Tests

[docker/j5-browser/test/run-tests.sh](../docker/j5-browser/test/run-tests.sh) runs the
image end to end: a job that finds an image in Chromium, types, copies text, downloads a
file and takes a screenshot; the same job as another user; a failing job; a job that runs
out of time; the setup errors; and the author mode's password-protected web desktop. The
`Container` workflow builds the image and runs them when the image or j5 changes.

```console
$ docker build -f docker/j5-browser/Dockerfile -t j5-browser . && docker/j5-browser/test/run-tests.sh
```
