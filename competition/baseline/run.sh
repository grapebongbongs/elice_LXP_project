#!/usr/bin/env bash
# 런박스 서버 실행: bash run.sh   (포트 변경: PORT=8001 bash run.sh, 8080 금지)
set -e
cd "$(dirname "$0")"
for f in ../.env .env; do
  [ -f "$f" ] && { set -a; source "$f"; set +a; }
done
PORT="${PORT:-8000}"
if [ "$PORT" = "8080" ]; then echo "8080 은 VSCode 가 사용합니다"; exit 1; fi
if [ -z "$SERVER_TOKEN" ] && [ -z "$TEAM_TOKEN" ]; then echo "WARNING: SERVER_TOKEN 이 비어 있습니다 (.env 에 설정)"; fi
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --no-access-log --timeout-keep-alive 60
