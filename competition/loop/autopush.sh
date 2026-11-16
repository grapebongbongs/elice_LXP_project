#!/usr/bin/env bash
# 1시간마다 변경을 커밋해 GitHub 에 올린다 (런박스에서 실행).
#   nohup bash loop/autopush.sh > loop/.autopush.log 2>&1 &
#   간격 변경: INTERVAL=1800 bash loop/autopush.sh      한 번만: ONCE=1 bash loop/autopush.sh
#
# 안전장치
# - .env 같은 키 파일이 스테이지에 올라오면 그 회차는 올리지 않는다.
# - 스테이지된 내용에 OPENAI_API_KEY 값이 그대로 들어 있으면 올리지 않는다.
# - 강제 push 는 하지 않는다. 원격이 앞서 있으면 rebase 로 받아 온 뒤 올리고, 충돌이면 멈추고 알린다.
set -u
cd "$(dirname "$0")/.."
INTERVAL="${INTERVAL:-3600}"
[ -f .env ] && { set -a; source .env; set +a; }

push_once() {
  local branch; branch="$(git rev-parse --abbrev-ref HEAD)"
  git add -A
  if git diff --cached --name-only | grep -Eq '(^|/)\.env$'; then
    echo "[autopush] .env 가 스테이지에 있습니다. 이번 회차는 올리지 않습니다."; git reset -q; return 1
  fi
  if [ -n "${OPENAI_API_KEY:-}" ] && git diff --cached | grep -qF "$OPENAI_API_KEY"; then
    echo "[autopush] 변경 내용에 API 키가 들어 있습니다. 이번 회차는 올리지 않습니다."; git reset -q; return 1
  fi
  if ! git diff --cached --quiet; then
    git commit -q -m "auto: hourly snapshot $(date '+%Y-%m-%d %H:%M')" && echo "[autopush] 커밋 $(git rev-parse --short HEAD)"
  fi
  git fetch -q origin "$branch" 2>/dev/null
  if git rev-parse -q --verify "origin/$branch" >/dev/null && [ -n "$(git rev-list "HEAD..origin/$branch")" ]; then
    git pull -q --rebase origin "$branch" || { git rebase --abort 2>/dev/null; echo "[autopush] 원격과 충돌. 사람이 정리해야 합니다."; return 1; }
  fi
  if ! git rev-parse -q --verify "origin/$branch" >/dev/null || [ -n "$(git rev-list "origin/$branch..HEAD")" ]; then
    git push -q origin "$branch" && git push -q origin --tags && echo "[autopush] $(date '+%H:%M') push 완료 ($branch @$(git rev-parse --short HEAD))"
  else
    echo "[autopush] $(date '+%H:%M') 올릴 변경 없음"
  fi
}

while true; do
  push_once
  [ -n "${ONCE:-}" ] && break
  sleep "$INTERVAL"
done
