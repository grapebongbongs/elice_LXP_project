#!/usr/bin/env bash
# Commit and push numeric experiment artifacts from a candidate branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"

usage() {
  cat <<'EOF'
Usage:
  publish_experiment_result.sh [--result-dir <exp-result-id>] [--report <report.json>] [--remote origin]

Without --result-dir and --report, publish every unrecorded repository-root exp-result-*
directory that contains report.json in one commit. Already recorded result directories are
skipped. Use --result-dir or --report to publish exactly one result instead.
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

is_recorded_result() {
  local result_dir="$1"
  local result_rel="${result_dir#"$ROOT_DIR"/}"
  git ls-files --error-unmatch "$result_rel/report.json" >/dev/null 2>&1
}

if [[ -z "$REPORT" && -z "$RESULT_DIR" ]]; then
  CANDIDATES=()
  while IFS= read -r candidate; do CANDIDATES+=("$candidate"); done < <(
    find "$ROOT_DIR" -mindepth 1 -maxdepth 1 -type d -name 'exp-result-*' -print | sort | while IFS= read -r candidate; do
      [[ -f "$candidate/report.json" ]] || continue
      is_recorded_result "$candidate" || printf '%s\n' "$candidate"
    done
  )
  if [[ "${#CANDIDATES[@]}" -eq 0 ]]; then
    echo "No unrecorded exp-result-* directory with report.json was found"
    exit 0
  fi
else
  if [[ -n "$REPORT" && -z "$RESULT_DIR" ]]; then RESULT_DIR="$(dirname "$REPORT")"; fi
  if [[ -n "$RESULT_DIR" && -z "$REPORT" ]]; then REPORT="$RESULT_DIR/report.json"; fi
  [[ -n "$REPORT" && -f "$REPORT" ]] || { echo "--report must name an existing report.json file" >&2; exit 2; }
  [[ -n "$RESULT_DIR" && -d "$RESULT_DIR" ]] || { echo "--result-dir must name an existing directory" >&2; exit 2; }

  RESULT_DIR="$(cd "$RESULT_DIR" && pwd -P)"
  REPORT="$(cd "$(dirname "$REPORT")" && pwd -P)/$(basename "$REPORT")"
  [[ "$REPORT" == "$RESULT_DIR/report.json" ]] || { echo "--report must be $RESULT_DIR/report.json" >&2; exit 2; }
  CANDIDATES=("$RESULT_DIR")
fi

BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { echo "Detached HEAD cannot publish a result" >&2; exit 2; }
[[ "$BRANCH" != "main" && "$BRANCH" != "champion" ]] || { echo "Refusing to publish from $BRANCH" >&2; exit 2; }
git diff --quiet || { echo "Tracked changes exist before publishing" >&2; exit 2; }
git diff --cached --quiet || { echo "Staged changes exist before publishing" >&2; exit 2; }

RESULT_RELS=()
EXPERIMENT_IDS=()
for candidate in "${CANDIDATES[@]}"; do
  candidate="$(cd "$candidate" && pwd -P)"
  case "$candidate" in
    "$ROOT_DIR"/exp-result-*) result_rel="${candidate#"$ROOT_DIR"/}" ;;
    *) echo "--result-dir must be an exp-result-<id> directory inside the repository" >&2; exit 2 ;;
  esac
  report="$candidate/report.json"
  [[ -f "$report" ]] || { echo "--report must name an existing report.json file" >&2; exit 2; }
  python3 "$METRICS" validate-report --report "$report"
  experiment_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$report")"
  [[ "$result_rel" == "exp-result-${experiment_id}" ]] || { echo "Result directory does not match report experiment_id: $result_rel" >&2; exit 2; }
  RESULT_RELS+=("$result_rel")
  EXPERIMENT_IDS+=("$experiment_id")
done

printf '[publish] selected results:\n'
printf '  %s\n' "${RESULT_RELS[@]}"

while IFS= read -r path; do
  allowed=0
  for result_rel in "${RESULT_RELS[@]}"; do
    if [[ "$path" == "$result_rel/"* ]]; then allowed=1; break; fi
  done
  [[ "$allowed" -eq 1 ]] || { echo "Unexpected untracked file: $path" >&2; exit 2; }
done < <(git ls-files --others --exclude-standard)

git add -- "${RESULT_RELS[@]}"
git diff --cached --quiet && { echo "No publishable numeric artifacts found" >&2; exit 2; }
if [[ "${#EXPERIMENT_IDS[@]}" -eq 1 ]]; then
  commit_message="experiment: record ${EXPERIMENT_IDS[0]}"
else
  commit_message="experiment: record ${#EXPERIMENT_IDS[@]} results"
fi
git commit -m "$commit_message"
git push "$REMOTE" "HEAD:refs/heads/${BRANCH}"
echo "Published experiment artifacts on ${BRANCH}:"
printf '  %s\n' "${RESULT_RELS[@]}"
