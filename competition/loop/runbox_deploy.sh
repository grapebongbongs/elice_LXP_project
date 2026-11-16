#!/usr/bin/env bash
# 런박스에서 실행: GitHub 의 브랜치를 받아 작업 트리를 그 상태로 맞춘다.
#   bash loop/runbox_deploy.sh exp/09-loop-and-retrieval
# - 런박스에서 직접 만든 커밋이 있어도 지우지 않는다. 앞으로 감기(fast-forward)가 안 되면 멈추고 알린다.
# - .env 는 git 이 추적하지 않으므로 그대로 남는다. 만일을 대비해 .git/env.keep 에 사본을 두고, 없어졌으면 되살린다.
set -e
cd "$(dirname "$0")/.."
REF="${1:?브랜치 이름을 주세요}"
[ -f .env ] && cp -p .env .git/env.keep
git fetch -q origin
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$REF" ]; then
  git checkout -q "$REF" 2>/dev/null || git checkout -q -b "$REF" "origin/$REF"
fi
if ! git merge -q --ff-only "origin/$REF"; then
  echo "받지 못했습니다: 런박스에 GitHub 에 올리지 않은 커밋이나 겹치는 변경이 있습니다. (git status / git log origin/$REF..HEAD 확인)"
  exit 1
fi
[ -f .env ] || { [ -f .git/env.keep ] && cp -p .git/env.keep .env; }
echo "deployed: $(git log --oneline -1)"
git status --short
