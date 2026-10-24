#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO="https://github.com/Dorusto/yt-pipeline.git"

usage() {
    cat <<'EOF'
Usage: deploy/smoke_plugin.sh [options]

Install yt-pipeline in a fresh container and get a recap from a folder of clips.

Options:
  --source DIR     folder of clips to recap (required unless --dry-run)
  --brief TEXT     the brief in plain words (required unless --dry-run)
  --workdir DIR    working directory (default: a fresh mktemp -d)
  --repo URL       repository to clone and the marketplace source
                   (`owner/repo` or a local path)
                   (default: https://github.com/Dorusto/yt-pipeline.git)
  --skip-plugin    skip the Claude Code plugin install (test the yt entry
                   point only)
  --dry-run        print the steps and change nothing
  --help           show this help

Exit codes: 0 success; 2 usage error (bad flag, missing --source/--brief);
3 a step failed after work started.

Examples:
  deploy/smoke_plugin.sh --source <clips-folder> --brief "<brief>"
  deploy/smoke_plugin.sh --dry-run
EOF
}

require_value() {
    if [ "$#" -lt 2 ]; then
        echo "[error] $1 needs a value" >&2
        usage >&2
        exit 2
    fi
}

fail() {
    echo "[error] $1 failed" >&2
    exit 3
}

SOURCE=""
BRIEF=""
WORKDIR=""
REPO="$DEFAULT_REPO"
SKIP_PLUGIN=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) require_value "$@"; SOURCE="$2"; shift 2 ;;
        --brief) require_value "$@"; BRIEF="$2"; shift 2 ;;
        --workdir) require_value "$@"; WORKDIR="$2"; shift 2 ;;
        --repo) require_value "$@"; REPO="$2"; shift 2 ;;
        --skip-plugin) SKIP_PLUGIN=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --help|-h) usage; exit 0 ;;
        -*) echo "[error] unknown option: $1" >&2; usage >&2; exit 2 ;;
        *) echo "[error] unexpected argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$DRY_RUN" -eq 0 ]; then
    if [ -z "$SOURCE" ]; then
        echo "[error] missing --source DIR" >&2
        usage >&2
        exit 2
    fi
    if [ -z "$BRIEF" ]; then
        echo "[error] missing --brief TEXT" >&2
        usage >&2
        exit 2
    fi
fi

if [ -z "$WORKDIR" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        WORKDIR="<workdir>"
    else
        WORKDIR="$(mktemp -d)"
    fi
fi

CLONE="$WORKDIR/yt-pipeline"
WORKSPACE="$WORKDIR/projects"

marketplace_source() {
    case "$REPO" in
        *://*)
            local path="${REPO#*://}"
            path="${path#*/}"
            echo "${path%.git}"
            ;;
        *) echo "$REPO" ;;
    esac
}
MARKETPLACE_SOURCE="$(marketplace_source)"

if [ "$DRY_RUN" -eq 1 ]; then
    SHOWN_SOURCE="${SOURCE:-<source>}"
    SHOWN_BRIEF="${BRIEF:-<brief>}"
    echo "[dry-run] clone: git clone $REPO $CLONE"
    echo "[dry-run] venv: uv venv && uv pip install -r requirements.txt in $CLONE"
    echo "[dry-run] yt on PATH: ln -s $CLONE/bin/yt ~/.local/bin/yt"
    echo "[dry-run] entry point: yt --help && yt doctor --source $SHOWN_SOURCE --workspace $WORKSPACE"
    if [ "$SKIP_PLUGIN" -eq 1 ]; then
        echo "[dry-run] plugin: skipped (--skip-plugin)"
    else
        echo "[dry-run] plugin: claude plugin marketplace add $MARKETPLACE_SOURCE && claude plugin install yt-director@yt-pipeline"
    fi
    echo "[dry-run] recap: yt recap --source $SHOWN_SOURCE --brief $SHOWN_BRIEF --workspace $WORKSPACE"
    echo "[dry-run] output: check the recap MP4 under $WORKSPACE"
    exit 0
fi

echo "[smoke] clone: $REPO -> $CLONE"
if [ -d "$CLONE/.git" ]; then
    echo "[smoke] reusing the existing clone at $CLONE"
else
    git clone "$REPO" "$CLONE" || fail "clone"
fi

echo "[smoke] venv: installing requirements in $CLONE"
if [ -x "$CLONE/.venv/bin/python" ]; then
    echo "[smoke] reusing the existing virtual environment"
elif command -v uv >/dev/null 2>&1; then
    ( cd "$CLONE" && uv venv && uv pip install -r requirements.txt ) || fail "venv"
else
    ( cd "$CLONE" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt ) || fail "venv"
fi

echo "[smoke] yt on PATH: linking $CLONE/bin/yt into ~/.local/bin"
mkdir -p "$HOME/.local/bin" || fail "yt on PATH"
ln -sf "$CLONE/bin/yt" "$HOME/.local/bin/yt" || fail "yt on PATH"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

echo "[smoke] entry point: yt --help"
yt --help >/dev/null || fail "entry point"
echo "[smoke] entry point: yt doctor --source $SOURCE --workspace $WORKSPACE"
if yt doctor --source "$SOURCE" --workspace "$WORKSPACE"; then
    :
else
    rc=$?
    if [ "$rc" -eq 1 ]; then
        echo "[smoke] warning: yt doctor found something to look at (exit 1); continuing" >&2
    else
        fail "entry point"
    fi
fi

if [ "$SKIP_PLUGIN" -eq 1 ]; then
    echo "[smoke] plugin: skipped (--skip-plugin)"
elif command -v claude >/dev/null 2>&1; then
    echo "[smoke] plugin: claude plugin marketplace add $MARKETPLACE_SOURCE"
    claude plugin marketplace add "$MARKETPLACE_SOURCE" || fail "plugin"
    echo "[smoke] plugin: claude plugin install yt-director@yt-pipeline"
    claude plugin install yt-director@yt-pipeline || fail "plugin"
else
    echo "[smoke] warning: claude is not on PATH; skipping the plugin install" >&2
fi

echo "[smoke] recap: yt recap --source $SOURCE --brief $BRIEF --workspace $WORKSPACE"
yt recap --source "$SOURCE" --brief "$BRIEF" --workspace "$WORKSPACE" || fail "recap"

echo "[smoke] output: looking for the recap MP4 under $WORKSPACE"
RECAP=""
if [ -f "$WORKSPACE/recap_720p.mp4" ]; then
    RECAP="$WORKSPACE/recap_720p.mp4"
else
    RECAP="$(find "$WORKSPACE" -maxdepth 2 -name '*.mp4' -print -quit 2>/dev/null || true)"
fi
if [ -z "$RECAP" ]; then
    fail "output"
fi
echo "[smoke] recap written: $RECAP"
exit 0
