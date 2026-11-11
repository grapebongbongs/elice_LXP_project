#!/usr/bin/env bash
# Commit and push numeric experiment artifacts from one candidate branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"

usage() {
  echo "Usage: publish_experiment_result.sh --report <exp-result-id/report.json> --result-dir <exp-result-id> [--remote origin]"
}

REPORT=""
RESULT_DIR=""
REMOTE="origin"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --report) REPORT="$2"; shift 2 ;;
    --result-dir) RESULT_DIR="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$REPORT" && -f "$REPORT" ]] || { echo "--report must name an existing JSON file" >&2; exit 2; }
[[ -n "$RESULT_DIR" && -d "$RESULT_DIR" ]] || { echo "--result-dir must name an existing directory" >&2; exit 2; }
cd "$ROOT_DIR"

case "$RESULT_DIR" in
  "$ROOT_DIR"/exp-result-*) RESULT_REL="${RESULT_DIR#"$ROOT_DIR"/}" ;;
  exp-result-*) RESULT_REL="$RESULT_DIR" ;;
  *) echo "--result-dir must be an exp-result-<id> directory inside the repository" >&2; exit 2 ;;
esac
[[ "$REPORT" == "$ROOT_DIR/$RESULT_REL/report.json" || "$REPORT" == "$RESULT_REL/report.json" ]] || { echo "--report must be $RESULT_REL/report.json" >&2; exit 2; }

BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { echo "Detached HEAD cannot publish a result" >&2; exit 2; }
[[ "$BRANCH" != "main" && "$BRANCH" != "champion" ]] || { echo "Refusing to publish from $BRANCH" >&2; exit 2; }
git diff --quiet || { echo "Tracked changes exist before publishing" >&2; exit 2; }
git diff --cached --quiet || { echo "Staged changes exist before publishing" >&2; exit 2; }

python3 "$METRICS" validate-report --report "$REPORT"
EXPERIMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$REPORT")"
[[ "$RESULT_REL" == "exp-result-${EXPERIMENT_ID}" ]] || { echo "Result directory does not match report experiment_id" >&2; exit 2; }

while IFS= read -r path; do
  [[ "$path" == "$RESULT_REL/"* ]] || { echo "Unexpected untracked file: $path" >&2; exit 2; }
done < <(git ls-files --others --exclude-standard)

git add -- "$RESULT_REL"
git diff --cached --quiet && { echo "No publishable numeric artifacts found in $RESULT_REL" >&2; exit 2; }
git commit -m "experiment: record ${EXPERIMENT_ID}"
git push "$REMOTE" "HEAD:refs/heads/${BRANCH}"
echo "Published experiment artifacts: ${BRANCH}:${RESULT_REL}"
