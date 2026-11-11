#!/usr/bin/env bash
# Create, commit, and push one candidate branch after an explicit human confirmation.
# Local .git_info remembers the last feature/number/message but is never committed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
INFO_FILE="$ROOT_DIR/.git_info"

usage() {
  cat <<'EOF'
Usage:
  # .git_info에 준비된 현재 후보를 그대로 publish
  bash baseline/scripts/create_candidate_branch.sh

  # First candidate (or explicit override)
  bash baseline/scripts/create_candidate_branch.sh \
    --feature <change-feature> --number <01> \
    --update <change-summary> [--message <full-commit-message>]

  # Next candidate: read .git_info and increase the number automatically
  bash baseline/scripts/create_candidate_branch.sh --update <change-summary>

Optional: [--base <git-ref>] [--remote origin]

Without arguments, the branch and message already prepared in .git_info are used as-is.
The branch is TraderPark/<feature>-<number>. All non-ignored changes are staged by default.
Before changing Git state, the script shows the branch, commit message, and changed paths,
then continues only after an exact y/yes confirmation.
EOF
}

stored_feature=""
stored_number=""
stored_message=""
if [[ -f "$INFO_FILE" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      feature) stored_feature="$value" ;;
      number) stored_number="$value" ;;
      message) stored_message="$value" ;;
    esac
  done < "$INFO_FILE"
fi

FEATURE=""
NUMBER=""
UPDATE=""
MESSAGE=""
BASE="HEAD"
REMOTE="origin"
FEATURE_WAS_SET=0
NUMBER_WAS_SET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --feature) FEATURE="${2:-}"; FEATURE_WAS_SET=1; shift 2 ;;
    --number) NUMBER="${2:-}"; NUMBER_WAS_SET=1; shift 2 ;;
    --update) UPDATE="${2:-}"; shift 2 ;;
    --message) MESSAGE="${2:-}"; shift 2 ;;
    --base) BASE="${2:-}"; shift 2 ;;
    --remote) REMOTE="${2:-}"; shift 2 ;;
    --all) shift ;; # 이전 호출과 호환: 이제 기본 동작이므로 아무것도 하지 않는다.
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

next_number() {
  local previous="$1"
  [[ "$previous" =~ ^[0-9]{2,}$ ]] || return 1
  local width=${#previous}
  local numeric=$((10#$previous + 1))
  printf "%0${width}d" "$numeric"
}

if [[ "$FEATURE_WAS_SET" == "1" && -n "$stored_feature" && "$FEATURE" != "$stored_feature" && "$NUMBER_WAS_SET" != "1" ]]; then
  echo "A different --feature requires an explicit --number (for example 01)" >&2
  exit 2
fi
if [[ -z "$FEATURE" ]]; then FEATURE="$stored_feature"; fi
# --update는 '다음 후보'라는 뜻이다. 인자 없이 실행하면 .git_info의 현재 후보를 그대로 쓴다.
if [[ -z "$NUMBER" && -n "$stored_number" ]]; then
  if [[ -n "$UPDATE" ]]; then NUMBER="$(next_number "$stored_number")"; else NUMBER="$stored_number"; fi
fi

[[ "$FEATURE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "--feature is required initially and may use letters, digits, dot, underscore, or hyphen" >&2
  exit 2
}
[[ "$NUMBER" =~ ^[0-9]{2,}$ ]] || {
  echo "--number is required initially and must be zero-padded, for example 01" >&2
  exit 2
}
if [[ -z "$MESSAGE" && -z "$UPDATE" ]]; then MESSAGE="$stored_message"; fi
[[ -n "$UPDATE" || -n "$MESSAGE" ]] || {
  echo "Prepare .git_info first, or provide --update or --message" >&2
  exit 2
}
if [[ -z "$MESSAGE" ]]; then MESSAGE="feat(${FEATURE}): ${UPDATE}"; fi

cd "$ROOT_DIR"
SOURCE_BRANCH="$(git branch --show-current)"
[[ -n "$SOURCE_BRANCH" ]] || { echo "Detached HEAD cannot create a candidate branch" >&2; exit 2; }
git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null || {
  echo "--base is not a commit: $BASE" >&2
  exit 2
}
git remote get-url "$REMOTE" >/dev/null || { echo "Unknown remote: $REMOTE" >&2; exit 2; }

BRANCH="TraderPark/${FEATURE}-${NUMBER}"
git check-ref-format --branch "$BRANCH" >/dev/null
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "Local branch already exists: $BRANCH" >&2
  exit 2
fi
if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  echo "Remote branch already exists: $REMOTE/$BRANCH" >&2
  exit 2
fi
if [[ -z "$(git status --porcelain)" ]]; then
  echo "No non-ignored changes to commit" >&2
  exit 2
fi

echo ""
echo "Candidate publish preview"
echo "  source : $SOURCE_BRANCH @ $(git rev-parse --short "$BASE")"
echo "  branch : $BRANCH"
echo "  commit : $MESSAGE"
echo "  update : ${UPDATE:-'(full message supplied directly)'}"
echo ""
echo "Changed files (all non-ignored changes will be committed):"
git status --short
echo ""
git diff --stat HEAD
echo ""
read -r -p "Create, commit, and push this candidate? [y/N] " CONFIRM
case "$CONFIRM" in
  y|Y|yes|YES|Yes) ;;
  *) echo "Cancelled: no branch, commit, or push was made."; exit 0 ;;
esac

git switch -c "$BRANCH" "$BASE"
git add -A
git diff --cached --quiet && {
  echo "Only ignored changes exist; nothing was committed" >&2
  exit 2
}
git commit -m "$MESSAGE"

# Keep this local metadata only after the candidate commit itself has succeeded.
INFO_TMP="$ROOT_DIR/.git_info.tmp.$$"
umask 077
{
  printf 'feature=%s\n' "$FEATURE"
  printf 'number=%s\n' "$NUMBER"
  printf 'message=%s\n' "$MESSAGE"
} > "$INFO_TMP"
mv "$INFO_TMP" "$INFO_FILE"

git push -u "$REMOTE" "$BRANCH"
echo "Candidate published: $BRANCH"
echo "Saved local candidate metadata: .git_info"
echo "Next: cd baseline && bash scripts/evaluate_current_candidate.sh"
