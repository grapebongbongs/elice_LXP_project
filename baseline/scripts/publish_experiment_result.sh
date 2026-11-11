#!/usr/bin/env bash
# Commit and push numeric experiment artifacts from one candidate branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"

usage() {
  cat <<'EOF'
Usage:
  publish_experiment_result.sh [--result-dir <exp-result-id>] [--report <report.json>] [--remote origin]

Without --result-dir and --report, publish the only repository-root exp-result-* directory
that contains report.json. If more than one exists, specify --result-dir explicitly.
EOF
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

cd "$ROOT_DIR"

if [[ -z "$REPORT" && -z "$RESULT_DIR" ]]; then
  CANDIDATES=()
  while IFS= read -r candidate; do CANDIDATES+=("$candidate"); done < <(
    find "$ROOT_DIR" -mindepth 1 -maxdepth 1 -type d -name 'exp-result-*' -print | sort | while IFS= read -r candidate; do
      [[ -f "$candidate/report.json" ]] && printf '%s\n' "$candidate"
    done
  )
  if [[ "${#CANDIDATES[@]}" -eq 0 ]]; then
    echo "No exp-result-* directory with report.json was found; provide --result-dir" >&2
    exit 2
  fi
  if [[ "${#CANDIDATES[@]}" -ne 1 ]]; then
    echo "Multiple completed result directories found; choose one with --result-dir:" >&2
    printf '  %s\n' "${CANDIDATES[@]}" >&2
    exit 2
  fi
  RESULT_DIR="${CANDIDATES[0]}"
fi

if [[ -n "$REPORT" && -z "$RESULT_DIR" ]]; then RESULT_DIR="$(dirname "$REPORT")"; fi
if [[ -n "$RESULT_DIR" && -z "$REPORT" ]]; then REPORT="$RESULT_DIR/report.json"; fi
[[ -n "$REPORT" && -f "$REPORT" ]] || { echo "--report must name an existing report.json file" >&2; exit 2; }
[[ -n "$RESULT_DIR" && -d "$RESULT_DIR" ]] || { echo "--result-dir must name an existing directory" >&2; exit 2; }

RESULT_DIR="$(cd "$RESULT_DIR" && pwd -P)"
REPORT="$(cd "$(dirname "$REPORT")" && pwd -P)/$(basename "$REPORT")"

case "$RESULT_DIR" in
  "$ROOT_DIR"/exp-result-*) RESULT_REL="${RESULT_DIR#"$ROOT_DIR"/}" ;;
  *) echo "--result-dir must be an exp-result-<id> directory inside the repository" >&2; exit 2 ;;
esac
[[ "$REPORT" == "$RESULT_DIR/report.json" ]] || { echo "--report must be $RESULT_REL/report.json" >&2; exit 2; }

BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { echo "Detached HEAD cannot publish a result" >&2; exit 2; }
[[ "$BRANCH" != "main" && "$BRANCH" != "champion" ]] || { echo "Refusing to publish from $BRANCH" >&2; exit 2; }
git diff --quiet || { echo "Tracked changes exist before publishing" >&2; exit 2; }
git diff --cached --quiet || { echo "Staged changes exist before publishing" >&2; exit 2; }

python3 "$METRICS" validate-report --report "$REPORT"
EXPERIMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$REPORT")"
[[ "$RESULT_REL" == "exp-result-${EXPERIMENT_ID}" ]] || { echo "Result directory does not match report experiment_id" >&2; exit 2; }
echo "[publish] selected $RESULT_REL"

while IFS= read -r path; do
  [[ "$path" == "$RESULT_REL/"* ]] || { echo "Unexpected untracked file: $path" >&2; exit 2; }
done < <(git ls-files --others --exclude-standard)

git add -- "$RESULT_REL"
git diff --cached --quiet && { echo "No publishable numeric artifacts found in $RESULT_REL" >&2; exit 2; }
git commit -m "experiment: record ${EXPERIMENT_ID}"
git push "$REMOTE" "HEAD:refs/heads/${BRANCH}"
echo "Published experiment artifacts: ${BRANCH}:${RESULT_REL}"
