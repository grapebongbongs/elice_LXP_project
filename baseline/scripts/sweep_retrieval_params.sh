#!/usr/bin/env bash
# Sequential, single-checkout sweep for tutor hybrid retrieval parameters.
# Every candidate uses the standard 64-concurrency check and ten public evaluations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$BASELINE_DIR/.." && pwd)"
EVALUATE="$SCRIPT_DIR/evaluate_current_candidate.sh"
CONFIG_FILE="$BASELINE_DIR/app/tutor/config.py"
INFO_FILE="$ROOT_DIR/.git_info"

usage() {
  cat <<'EOF'
Usage:
  bash baseline/scripts/sweep_retrieval_params.sh --candidates 5:60,5:20,3:60 [options]

Each candidate is DENSE_K:RRF_K. Candidates run sequentially from the same source commit.

Options:
  --candidates <D:R,...>  Required positive integer pairs, for example 5:60,5:20,3:60.
  --feature <slug>        Branch feature (default: tutor-hybrid-retrieval).
  --start-number <NN>     First branch number; defaults to .git_info number + 1.
  --base <git-ref>        Shared source ref (default: current HEAD).
  --remote <name>         Git remote (default: origin).
  --yes                   Skip the one launch confirmation.

The script creates TraderPark/<feature>-<number>, changes only HYBRID_DENSE_K and
HYBRID_RRF_K, commits and pushes it, then invokes evaluate_current_candidate.sh.
Any Git, 64-concurrency, scoring, or publish failure stops the sweep immediately.
EOF
}

CANDIDATES_CSV=""
FEATURE="tutor-hybrid-retrieval"
START_NUMBER=""
BASE="HEAD"
REMOTE="origin"
AUTO_CONFIRM=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidates) CANDIDATES_CSV="${2:-}"; shift 2 ;;
    --feature) FEATURE="${2:-}"; shift 2 ;;
    --start-number) START_NUMBER="${2:-}"; shift 2 ;;
    --base) BASE="${2:-}"; shift 2 ;;
    --remote) REMOTE="${2:-}"; shift 2 ;;
    --yes) AUTO_CONFIRM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$CANDIDATES_CSV" ]] || { echo "--candidates is required" >&2; usage >&2; exit 2; }
[[ "$FEATURE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "--feature must use only letters, digits, dot, underscore, or hyphen" >&2
  exit 2
}

next_number() {
  local previous="$1"
  [[ "$previous" =~ ^[0-9]{2,}$ ]] || return 1
  printf "%0${#previous}d" "$((10#$previous + 1))"
}

if [[ -z "$START_NUMBER" && -f "$INFO_FILE" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" == "number" ]] && START_NUMBER="$(next_number "$value")"
  done < "$INFO_FILE"
fi
[[ "$START_NUMBER" =~ ^[0-9]{2,}$ ]] || {
  echo "--start-number is required when .git_info has no valid previous number" >&2
  exit 2
}

IFS=',' read -r -a PAIRS <<< "$CANDIDATES_CSV"
[[ "${#PAIRS[@]}" -gt 0 ]] || { echo "--candidates is empty" >&2; exit 2; }
declare -a DENSE_VALUES=() RRF_VALUES=()
declare -A SEEN_PAIRS=()
for pair in "${PAIRS[@]}"; do
  IFS=':' read -r dense rrf extra <<< "$pair"
  [[ -n "${dense:-}" && -n "${rrf:-}" && -z "${extra:-}" ]] || {
    echo "Invalid candidate '$pair'; use DENSE_K:RRF_K" >&2
    exit 2
  }
  [[ "$dense" =~ ^[1-9][0-9]*$ && "$rrf" =~ ^[1-9][0-9]*$ ]] || {
    echo "Candidate values must be positive integers: $pair" >&2
    exit 2
  }
  [[ -z "${SEEN_PAIRS[$pair]:-}" ]] || { echo "Duplicate candidate: $pair" >&2; exit 2; }
  SEEN_PAIRS[$pair]=1
  DENSE_VALUES+=("$dense")
  RRF_VALUES+=("$rrf")
done

cd "$ROOT_DIR"
[[ -z "$(git status --porcelain)" ]] || { echo "Sweep requires a clean checkout" >&2; exit 2; }
[[ -n "$(git branch --show-current)" ]] || { echo "Sweep cannot start from detached HEAD" >&2; exit 2; }
git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null || { echo "Invalid --base: $BASE" >&2; exit 2; }
BASE_COMMIT="$(git rev-parse "$BASE^{commit}")"
git remote get-url "$REMOTE" >/dev/null || { echo "Unknown remote: $REMOTE" >&2; exit 2; }
git var GIT_AUTHOR_IDENT >/dev/null 2>&1 || {
  echo "Git author identity is not configured; set user.name and user.email before an unattended sweep" >&2
  exit 2
}
[[ -x "$EVALUATE" && -f "$CONFIG_FILE" ]] || { echo "Required evaluation or config file is missing" >&2; exit 2; }

echo "Retrieval parameter sweep preview"
echo "  base commit: ${BASE_COMMIT}"
echo "  feature:     ${FEATURE}"
echo "  runs/value:  64-concurrency check + 10 public evaluations"
for index in "${!DENSE_VALUES[@]}"; do
  number=$(printf "%0${#START_NUMBER}d" "$((10#$START_NUMBER + index))")
  echo "  candidate:   TraderPark/${FEATURE}-${number}  (dense_k=${DENSE_VALUES[$index]}, rrf_k=${RRF_VALUES[$index]})"
done
if [[ "$AUTO_CONFIRM" != "1" ]]; then
  read -r -p "Run this sequential sweep? [y/N] " confirm
  case "$confirm" in y|Y|yes|YES|Yes) ;; *) echo "Cancelled."; exit 0 ;; esac
fi

write_git_info() {
  local number="$1" message="$2" temporary="$ROOT_DIR/.git_info.tmp.$$"
  umask 077
  {
    printf 'feature=%s\n' "$FEATURE"
    printf 'number=%s\n' "$number"
    printf 'message=%s\n' "$message"
  } > "$temporary"
  mv "$temporary" "$INFO_FILE"
}

set_config_value() {
  local name="$1" env_name="$2" value="$3"
  grep -qE "^${name} = int\\(os.getenv\\(\"${env_name}\", \"[0-9]+\"\\)\\)$" "$CONFIG_FILE" || {
    echo "Could not find expected ${name} setting" >&2
    exit 2
  }
  # Replace the full assignment rather than capturing nested parentheses in sed.
  # This is portable to the GNU sed available in the Elice RunBox.
  sed -i -E "s|^${name} = int\\(os.getenv\\(\"${env_name}\", \"[0-9]+\"\\)\\)$|${name} = int(os.getenv(\"${env_name}\", \"${value}\"))|" "$CONFIG_FILE"
  grep -q "${env_name}\", \"${value}\"" "$CONFIG_FILE" || {
    echo "Failed to set ${name}=${value}" >&2
    exit 2
  }
}

for index in "${!DENSE_VALUES[@]}"; do
  dense_k="${DENSE_VALUES[$index]}"
  rrf_k="${RRF_VALUES[$index]}"
  number=$(printf "%0${#START_NUMBER}d" "$((10#$START_NUMBER + index))")
  branch="TraderPark/${FEATURE}-${number}"
  message="feat(${FEATURE}): dense_k=${dense_k}, rrf_k=${rrf_k}"

  git show-ref --verify --quiet "refs/heads/$branch" && { echo "Local branch already exists: $branch" >&2; exit 2; }
  git ls-remote --exit-code --heads "$REMOTE" "$branch" >/dev/null 2>&1 && {
    echo "Remote branch already exists: $REMOTE/$branch" >&2; exit 2;
  }

  echo "[sweep] creating $branch (dense_k=${dense_k}, rrf_k=${rrf_k})"
  git switch -c "$branch" "$BASE_COMMIT"
  set_config_value HYBRID_DENSE_K TUTOR_HYBRID_DENSE_K "$dense_k"
  set_config_value HYBRID_RRF_K TUTOR_HYBRID_RRF_K "$rrf_k"
  python3 -m py_compile "$CONFIG_FILE"
  git diff --check
  git add -- "$CONFIG_FILE"
  git commit -m "$message"
  write_git_info "$number" "$message"
  git push -u "$REMOTE" "$branch"

  echo "[sweep] evaluating $branch"
  bash "$EVALUATE"
  [[ -z "$(git status --porcelain)" ]] || {
    echo "Evaluation left uncommitted changes on $branch; stopping sweep" >&2
    exit 2
  }
done

echo "[sweep] complete: ${#DENSE_VALUES[@]} candidates evaluated sequentially"
