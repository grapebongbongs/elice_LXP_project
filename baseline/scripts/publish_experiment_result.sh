#!/usr/bin/env bash
# Commit and push a sanitized numeric experiment report on the current candidate branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"

usage() {
  echo "Usage: publish_experiment_result.sh --report /tmp/report.json [--remote origin]"
}

REPORT=""
REMOTE="origin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --report) REPORT="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$REPORT" && -f "$REPORT" ]] || { echo "--report must name an existing JSON file" >&2; exit 2; }
cd "$ROOT_DIR"
BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { echo "Detached HEAD cannot publish a result" >&2; exit 2; }
[[ "$BRANCH" != "main" && "$BRANCH" != "champion" ]] || { echo "Refusing to publish from $BRANCH" >&2; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "Checkout must be clean before publishing" >&2; exit 2; }

python3 "$METRICS" validate-report --report "$REPORT"
EXPERIMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$REPORT")"
DESTINATION="experiment-reports/${EXPERIMENT_ID}.json"
mkdir -p "$(dirname "$DESTINATION")"
cp "$REPORT" "$DESTINATION"
git add -- "$DESTINATION"
git commit -m "experiment: record ${EXPERIMENT_ID}"
git push "$REMOTE" "HEAD:refs/heads/${BRANCH}"
echo "Published sanitized result: ${BRANCH}:${DESTINATION}"
