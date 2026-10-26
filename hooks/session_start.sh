#!/bin/sh
# Create the plugin's virtual environment in the plugin data directory.
#
# Runs as a SessionStart hook: it builds ${CLAUDE_PLUGIN_DATA}/venv from
# ${CLAUDE_PLUGIN_ROOT}/requirements.txt on the first session and again after
# an update changes requirements.txt, then writes a marker file holding the
# venv path. Every failure path exits 0 so the session is never blocked.

set -u

root=${CLAUDE_PLUGIN_ROOT:-}
data=${CLAUDE_PLUGIN_DATA:-}
[ -n "$root" ] || exit 0
[ -n "$data" ] || exit 0

venv=$data/venv
marker=$data/yt-pipeline-venv
requirements=$root/requirements.txt

if [ -x "$venv/bin/python" ] && [ -f "$data/requirements.txt" ] && cmp -s "$requirements" "$data/requirements.txt"; then
    printf '%s\n' "$venv" > "$marker" 2>/dev/null || exit 0
    exit 0
fi

mkdir -p "$data" || exit 0
python3 -m venv "$venv" || exit 0
"$venv/bin/python" -m pip install -r "$requirements" || exit 0
cp "$requirements" "$data/requirements.txt" || exit 0
printf '%s\n' "$venv" > "$marker" || exit 0
exit 0
