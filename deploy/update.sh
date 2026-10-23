#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVICE_NAME="${SERVICE_NAME:-yt-pipeline-web}"
ENV_FILE="${ENV_FILE:-/etc/yt-pipeline/env}"
SMOKE_URL="${SMOKE_URL:-http://127.0.0.1:8091/upload}"
PYTHON="$REPO_ROOT/.venv/bin/python"
PIP="$REPO_ROOT/.venv/bin/pip"
DEPLOYED_TAG_FILE="$REPO_ROOT/.deployed-tag"
CONSTRAINTS="$REPO_ROOT/requirements-constraints.txt"

usage() {
    cat <<'EOF'
Usage: deploy/update.sh <tag> [--dry-run]

Update a pilot host to a released tag, run a smoke test and roll back on failure.

Arguments:
  <tag>        the released tag to check out (required)

Options:
  --dry-run    print the steps and change nothing
  --help       show this help

Examples:
  deploy/update.sh <tag>            # update to a released tag, smoke test, roll back on failure
  deploy/update.sh <tag> --dry-run  # print the steps, change nothing
  SMOKE_URL=http://<container>:8091/upload deploy/update.sh <tag>
EOF
}

TAG=""
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --help|-h) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *)
            if [ -n "$TAG" ]; then
                usage >&2
                exit 2
            fi
            TAG="$arg"
            ;;
    esac
done

if [ -z "$TAG" ]; then
    usage >&2
    exit 2
fi

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

install_requirements() {
    if [ -f "$CONSTRAINTS" ]; then
        "$PIP" install -r "$REPO_ROOT/requirements.txt" -c "$CONSTRAINTS"
    else
        "$PIP" install -r "$REPO_ROOT/requirements.txt"
    fi
}

rollback() {
    local failed_step="$1"
    local previous=""
    if [ -f "$DEPLOYED_TAG_FILE" ]; then
        previous="$(cat "$DEPLOYED_TAG_FILE" 2>/dev/null || true)"
    fi
    if [ -n "$previous" ]; then
        echo "[error] $failed_step failed; rolling back to $previous" >&2
        git -C "$REPO_ROOT" checkout "$previous" || true
        install_requirements || true
        systemctl restart "$SERVICE_NAME" || true
        exit 3
    fi
    echo "[error] $failed_step failed; no previous tag in $DEPLOYED_TAG_FILE, nothing to roll back to" >&2
    exit 3
}

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] git -C $REPO_ROOT fetch --tags"
    echo "[dry-run] git -C $REPO_ROOT checkout $TAG"
    if [ -f "$CONSTRAINTS" ]; then
        echo "[dry-run] $PIP install -r $REPO_ROOT/requirements.txt -c $CONSTRAINTS"
    else
        echo "[dry-run] $PIP install -r $REPO_ROOT/requirements.txt"
    fi
    echo "[dry-run] smoke test: $PYTHON $REPO_ROOT/scripts/serve.py --check --json --port 0 --workspaces ${YT_PIPELINE_WORKSPACES:-}"
    echo "[dry-run] systemctl restart $SERVICE_NAME"
    echo "[dry-run] smoke test: curl -fsS --max-time 10 $SMOKE_URL"
    echo "[dry-run] echo $TAG > $DEPLOYED_TAG_FILE"
    exit 0
fi

echo "[update] fetching tags"
git -C "$REPO_ROOT" fetch --tags

echo "[update] checking out $TAG"
git -C "$REPO_ROOT" checkout "$TAG"

echo "[update] installing requirements"
install_requirements || rollback "install"

echo "[update] smoke test: serve.py --check"
"$PYTHON" "$REPO_ROOT/scripts/serve.py" --check --json --port 0 --workspaces "${YT_PIPELINE_WORKSPACES:-}" || rollback "serve.py --check"

echo "[update] restarting $SERVICE_NAME"
systemctl restart "$SERVICE_NAME" || rollback "systemctl restart"

echo "[update] smoke test: GET $SMOKE_URL"
smoke_ok=0
for _ in $(seq 1 10); do
    if curl -fsS --max-time 10 "$SMOKE_URL" >/dev/null 2>&1; then
        smoke_ok=1
        break
    fi
    sleep 1
done
if [ "$smoke_ok" -ne 1 ]; then
    rollback "HTTP smoke test"
fi

echo "$TAG" > "$DEPLOYED_TAG_FILE"
echo "[update] updated to $TAG"
exit 0
