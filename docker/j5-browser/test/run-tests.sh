#!/usr/bin/env bash
# End-to-end tests of the j5-browser image: a job that drives Chromium and produces
# files (also as another user), a failing job, a job that runs out of time or is stopped,
# the setup errors, and the author mode's web desktop.
#
# Usage: docker/j5-browser/test/run-tests.sh [IMAGE]     (default: j5-browser)
# Needs docker and python3 on the host.
set -euo pipefail

image=${1:-j5-browser}
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0

pass() { echo "ok    $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
check() {  # check DESCRIPTION COMMAND...
  local what=$1; shift
  if "$@"; then pass "$what"; else fail "$what"; fi
}
json() {  # json FILE KEY: one top-level field of a JSON file
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"
}
show_logs() {
  local out=$1
  echo "---- $out/j5.log"; cat "$out/j5.log" 2>/dev/null || true
  echo "---- $out/events.jsonl (last 20)"; tail -n 20 "$out/events.jsonl" 2>/dev/null || true
}

run_job() {  # run_job NAME [docker run options...]; sets $out and $code
  local name=$1; shift
  out="$work/$name"
  mkdir -p "$out"
  chmod 0777 "$out"
  code=0
  docker run --rm --shm-size=512m --user "$(id -u):$(id -g)" \
    -v "$here/$name:/job:ro" -v "$out:/out" "$@" "$image" run \
    || code=$?
}

echo "== smoke: drive Chromium, produce files"
run_job smoke -e START_URL=file:///job/page.html -e J5_ALLOW_EXEC=1 -e J5_FAST=1 \
  -e J5_PARAM_ENV=token=SMOKE_TOKEN -e SMOKE_TOKEN=s3cr3t-t0ken
check "exit code 0 (got $code)" test "$code" -eq 0
check "result.json says completed" test "$(json "$out/result.json" status)" = completed
check "the download landed in /out/downloads" \
  grep -qx "downloaded by j5" "$out/downloads/smoke.txt"
check "the copied text landed in /out/clipboard.txt" \
  grep -qx "Hello from j5 #42" "$out/clipboard.txt"
check "the screenshot was written" test -s "$out/screen.png"
check "result.json lists the files" \
  python3 -c 'import json,sys; f=json.load(open(sys.argv[1]))["files"]; sys.exit(not {"downloads/smoke.txt","clipboard.txt","screen.png"} <= set(f))' "$out/result.json"
check "the secret is masked in the events" bash -c "! grep -q s3cr3t-t0ken '$out/events.jsonl' && grep -q 'token was \*\*\*' '$out/events.jsonl'"
[[ $failures -eq 0 ]] || show_logs "$out"

echo "== another user: docker run --user"
before=$failures
out="$work/other-user"
mkdir -p "$out"
chmod 0777 "$out"
code=0
docker run --rm --shm-size=512m --user 4321:4321 -v "$here/smoke:/job:ro" -v "$out:/out" \
  -e START_URL=file:///job/page.html -e J5_ALLOW_EXEC=1 -e J5_FAST=1 \
  -e J5_PARAM_ENV=token=SMOKE_TOKEN -e SMOKE_TOKEN=s3cr3t-t0ken "$image" run || code=$?
check "exit code 0 (got $code)" test "$code" -eq 0
check "the files belong to that user" test "$(stat -c %u "$out/result.json")" = 4321
[[ $failures -eq $before ]] || show_logs "$out"

echo "== fail: an image that never appears"
before=$failures
run_job fail
check "exit code 1 (got $code)" test "$code" -eq 1
check "result.json says failed" test "$(json "$out/result.json" status)" = failed
check "a step_failed event names image_not_found" grep -q '"image_not_found"' "$out/events.jsonl"
check "a capture of the failed search is in /out/runs" \
  bash -c "find '$out/runs' -name '*.png' | grep -q ."
[[ $failures -eq $before ]] || show_logs "$out"

echo "== timeout: JOB_TIMEOUT stops a run"
before=$failures
started=$SECONDS
run_job timeout -e JOB_TIMEOUT=5s
check "exit code 124 (got $code)" test "$code" -eq 124
check "stopped within 30 s (took $((SECONDS - started)) s)" test $((SECONDS - started)) -lt 30
check "result.json says timeout" test "$(json "$out/result.json" status)" = timeout
check "the run was saved as stopped" grep -q '"status":"stopped"' "$out/events.jsonl"
[[ $failures -eq $before ]] || show_logs "$out"

echo "== docker stop: interrupts the run cleanly"
before=$failures
out="$work/stop"
mkdir -p "$out"
chmod 0777 "$out"
cid=$(docker run -d --shm-size=512m --user "$(id -u):$(id -g)" \
  -v "$here/timeout:/job:ro" -v "$out:/out" "$image" run)
for _ in $(seq 1 100); do grep -q step_started "$out/events.jsonl" 2>/dev/null && break; sleep 0.2; done
docker stop -t 20 "$cid" >/dev/null
code=$(docker inspect "$cid" --format '{{.State.ExitCode}}')
docker rm "$cid" >/dev/null
check "exit code 130 (got $code)" test "$code" -eq 130
check "result.json says stopped" test "$(json "$out/result.json" status)" = stopped
check "j5 was interrupted once" bash -c "! grep -q 'interrupted again' '$out/j5.log'"
[[ $failures -eq $before ]] || show_logs "$out"

echo "== setup errors"
code=0
docker run --rm "$image" run >/dev/null 2>&1 || code=$?
check "no script: exit code 2 (got $code)" test "$code" -eq 2
code=0
docker run --rm "$image" author >/dev/null 2>&1 || code=$?
check "author without VNC_PASSWORD: exit code 2 (got $code)" test "$code" -eq 2

echo "== author: the web desktop"
port=${TEST_VNC_PORT:-16901}
cid=$(docker run -d --rm -p "127.0.0.1:$port:6901" -e VNC_PASSWORD=test-pass "$image" author)
status() { curl -s -o /dev/null -w '%{http_code}' "$@" "http://127.0.0.1:$port/" || true; }
for _ in $(seq 1 50); do [[ "$(status -u j5:test-pass)" == 200 ]] && break; sleep 0.2; done
check "the page answers with the password" test "$(status -u j5:test-pass)" = 200
check "and refuses without it" test "$(status)" = 401
windows=
for _ in $(seq 1 30); do  # Chromium may still be starting
  windows=$(docker exec "$cid" j5 windows --json 2>/dev/null || true)
  grep -qi chrom <<<"$windows" && break
  sleep 0.5
done
check "j5 sees the browser window through docker exec" grep -qi chrom <<<"$windows"
docker stop -t 5 "$cid" >/dev/null

echo
if [[ $failures -eq 0 ]]; then
  echo "all tests passed"
else
  echo "$failures check(s) failed"
  exit 1
fi
