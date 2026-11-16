#!/usr/bin/env bash
# 개발 PC 에서 실행: 로컬 파이썬(또는 셸) 스크립트 파일을 런박스로 보내 실행하고 출력을 읽어 온다.
#   bash loop/browser/rbpy.sh <작업 id> <로컬 파일> [대기 초]
# rb.mjs job 의 명령 문자열에 따옴표·여러 줄이 섞이면 깨지기 쉬워, 파일을 base64 로 실어 보낸다.
# .py 는 python3, 그 밖의 파일은 bash 로 실행한다. 출력은 런박스의 /tmp/rb/<id>.out 에 남는다(공개 세트 분석용).
set -e
cd "$(dirname "$0")/../.."
id="$1"; file="$2"; secs="${3:-120}"
[ -f "$file" ] || { echo "파일이 없습니다: $file"; exit 2; }
case "$file" in *.py) run=python3; ext=py ;; *) run=bash; ext=sh ;; esac
b64=$(base64 -w0 "$file")
MSYS_NO_PATHCONV=1 node loop/browser/rb.mjs job "$id" "echo $b64 | base64 -d > /tmp/rb/$id.src.$ext && $run /tmp/rb/$id.src.$ext > /tmp/rb/$id.out 2>&1; echo exit=\$?" "$secs" > /dev/null || true
MSYS_NO_PATHCONV=1 node loop/browser/rb.mjs read /tmp/rb/$id.out
