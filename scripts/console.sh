#!/bin/bash
# The translation console on top of strcht: its beacon is our /pinhole/list.
#
#   ./run.sh                  # strcht           http://127.0.0.1:8790
#   ./scripts/console.sh      # the console      http://127.0.0.1:8788
#
# The console is another project, run from its own checkout. Nothing there is touched: its .env stays as it
# is (Palabra key, targets, site password, …); what is set here wins over it for this one process, so the
# same console can keep running elsewhere on its usual beacon.
#
# Which console, in our .env (see .env.example) or the environment:
#   CONSOLE_DIR         its checkout
#   CONSOLE_LIST_VAR    the name of ITS variable for the beacon url
#   CONSOLE_TOKEN_VAR   … and for the beacon's bearer token (optional)
#   CONSOLE_HIDE        names the console shows that this demo must not (comma separated): console/skin.py
#                       rewrites them in every page, script, option list, sign-in prompt and log line
# Per run:
#   CONSOLE_PORT / CONSOLE_HOST      where it listens                    (8788 / 127.0.0.1)
#   STRCHT_URL                       where strcht is                     (http://127.0.0.1:<our PORT, 8790>)
#   SOURCE_LANG / DETECTABLE_LANGS   what the speakers speak. The recordings are in several languages, often
#                       two in one stream, so the default is detection among the languages the beacon names
#                       (description.txt). SOURCE_LANG=ru ./scripts/console.sh pins one.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ours() { [ -f "$HERE/.env" ] && sed -n "s/^$1=//p" "$HERE/.env" | tail -1 || true; }
setting() { local v="${!1:-}"; if [ -n "$v" ]; then printf '%s' "$v"; else ours "$1"; fi; }   # environment, then our .env

CONSOLE_DIR="$(setting CONSOLE_DIR)"
CONSOLE_DIR="${CONSOLE_DIR/#\~/$HOME}"
LIST_VAR="$(setting CONSOLE_LIST_VAR)"
TOKEN_VAR="$(setting CONSOLE_TOKEN_VAR)"
HIDE="$(setting CONSOLE_HIDE)"
CONSOLE_HOST="${CONSOLE_HOST:-127.0.0.1}"
CONSOLE_PORT="${CONSOLE_PORT:-8788}"
PORT_OURS="$(ours PORT)"
STRCHT_URL="${STRCHT_URL:-http://127.0.0.1:${PORT_OURS:-8790}}"
TOKEN="$(setting PINHOLE_TOKEN)"

if [ -z "$CONSOLE_DIR" ] || [ -z "$LIST_VAR" ]; then
  echo "which console? set CONSOLE_DIR and CONSOLE_LIST_VAR in $HERE/.env (see .env.example)"; exit 1
fi
PY="$CONSOLE_DIR/.venv/bin/python"
[ -x "$PY" ] || { echo "no .venv in $CONSOLE_DIR — set the console up as its README says"; exit 1; }
list="$(curl -fsS -H "authorization: bearer ${TOKEN:-none}" "$STRCHT_URL/pinhole/list")" \
  || { echo "strcht does not answer at $STRCHT_URL/pinhole/list — start it first: ./run.sh"; exit 1; }

export SOURCE_LANG="${SOURCE_LANG:-auto}"
if [ -z "${DETECTABLE_LANGS+x}" ] && [ "$SOURCE_LANG" = auto ]; then
  DETECTABLE_LANGS="$(printf '%s' "$list" | "$PY" -c 'import json, sys
print(",".join(dict.fromkeys(l for s in json.load(sys.stdin) for l in s.get("languages", []))))')"
fi
export DETECTABLE_LANGS="${DETECTABLE_LANGS:-}"
export "$LIST_VAR=$STRCHT_URL/pinhole/list"
[ -z "$TOKEN_VAR" ] || export "$TOKEN_VAR=${TOKEN:-strcht}"
export PORT="$CONSOLE_PORT" SKIN_HIDE="$HIDE" PYTHONPATH="$HERE/console${PYTHONPATH:+:$PYTHONPATH}"

n="$(printf '%s' "$list" | "$PY" -c 'import json, sys; print(len(json.load(sys.stdin)))')"
echo "console  http://$CONSOLE_HOST:$CONSOLE_PORT   beacon $STRCHT_URL/pinhole/list ($n streams)   source $SOURCE_LANG${DETECTABLE_LANGS:+ [$DETECTABLE_LANGS]}"
cd "$CONSOLE_DIR"
exec "$PY" -m uvicorn skin:create --factory --host "$CONSOLE_HOST" --port "$CONSOLE_PORT" --log-level warning --ws-ping-interval 20 --ws-ping-timeout 20
