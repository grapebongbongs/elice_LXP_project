#!/usr/bin/env bash
# Run one candidate experiment in an Elice checkout. Never persists request/answer bodies; preserves experiment artifacts in the candidate branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$BASELINE_DIR/.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"
PUBLISH="$SCRIPT_DIR/publish_experiment_result.sh"
DASHBOARD="$SCRIPT_DIR/experiment_dashboard.py"

usage() {
  cat <<'EOF'
Usage:
  bash baseline/scripts/run_experiment.sh \
    --id 20261111-001 --scope guard --hypothesis "..." \
    [--champion-ref origin/main] [--champion-summary experiment-reports/champion.json] [--status-file /tmp/kosa-experiment-status.json]

Run this only from a clean, committed candidate branch. It uses one checkout and
one server at a time. It performs a real 64-request validation, ten candidate
runs, and ten champion runs only when the candidate is within the broad range.
EOF
}

EXPERIMENT_ID=""
SCOPE=""
HYPOTHESIS=""
CHAMPION_REF="origin/main"
CHAMPION_SUMMARY="experiment-reports/champion.json"
RUNS=10
DELTA=3
POTENTIAL_MARGIN=3
GUARD_REVIEW_MULTIPLIER=1.4
PORT="${EXPERIMENT_PORT:-8001}"
REMOTE="origin"
PUBLISH_RESULT=1
STATUS_FILE="${EXPERIMENT_STATUS_FILE:-/tmp/kosa-experiment-status.json}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --id) EXPERIMENT_ID="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --hypothesis) HYPOTHESIS="$2"; shift 2 ;;
    --champion-ref) CHAMPION_REF="$2"; shift 2 ;;
    --champion-summary) CHAMPION_SUMMARY="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --delta) DELTA="$2"; shift 2 ;;
    --potential-margin) POTENTIAL_MARGIN="$2"; shift 2 ;;
    --guard-review-multiplier) GUARD_REVIEW_MULTIPLIER="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --no-publish) PUBLISH_RESULT=0; shift ;;
    --status-file) STATUS_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$EXPERIMENT_ID" && -n "$SCOPE" && -n "$HYPOTHESIS" ]] || { usage >&2; exit 2; }
[[ "$SCOPE" == "tutor" || "$SCOPE" == "guard" || "$SCOPE" == "common" ]] || { echo "Invalid scope" >&2; exit 2; }
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo "--runs must be positive" >&2; exit 2; }
[[ "$EXPERIMENT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "--id must use only letters, digits, dot, underscore, or hyphen" >&2; exit 2; }

missing_dependency=0
for module in fastapi uvicorn openai httpx; do
  python3 -c "import $module" >/dev/null 2>&1 || { missing_dependency=1; break; }
done
if [[ "$missing_dependency" == "1" ]]; then
  echo "[experiment] installing missing Python dependencies"
  python3 -m pip install -r "$BASELINE_DIR/requirements.txt"
fi

cd "$ROOT_DIR"
ENV_FILE="$ROOT_DIR/.env"
if [[ ! -f "$ENV_FILE" && -f "$(dirname "$ROOT_DIR")/.env" && -f "$(dirname "$ROOT_DIR")/SPEC.md" ]]; then
  ENV_FILE="$(dirname "$ROOT_DIR")/.env"
fi
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi
if [[ -z "${SERVER_TOKEN:-}" && -n "${TEAM_TOKEN:-}" ]]; then SERVER_TOKEN="$TEAM_TOKEN"; fi
[[ -n "${SERVER_TOKEN:-}" && -n "${OPENAI_API_KEY:-}" && -n "${OPENAI_BASE_URL:-}" && -n "${OPENAI_MODEL:-}" ]] || { echo "SERVER_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL must be set" >&2; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "Candidate checkout must be clean" >&2; exit 2; }
ORIGINAL_BRANCH="$(git branch --show-current)"
[[ -n "$ORIGINAL_BRANCH" && "$ORIGINAL_BRANCH" != "main" && "$ORIGINAL_BRANCH" != "champion" ]] || { echo "Run from a named candidate branch" >&2; exit 2; }
ORIGINAL_HEAD="$(git rev-parse HEAD)"
BASE_COMMIT="$(git rev-parse "$CHAMPION_REF")"
RESULT_DIR="$ROOT_DIR/exp-result-${EXPERIMENT_ID}"
[[ ! -e "$RESULT_DIR" ]] || { echo "Result directory already exists: $RESULT_DIR" >&2; exit 2; }
mkdir -p "$RESULT_DIR/logs"

status_update() {
  python3 "$DASHBOARD" update --status-file "$STATUS_FILE" "$@" >/dev/null 2>&1 || echo "[experiment] dashboard update failed" >&2
}
status_update --reset --experiment-id "$EXPERIMENT_ID" --scope "$SCOPE" --runs-expected "$RUNS" --phase preparing --server stopped
SERVER_PID=""
cleanup() {
  local exit_code=$?
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  SERVER_PID=""
  if [[ "$exit_code" -eq 0 ]]; then
    status_update --phase complete --server stopped --finished success
  else
    status_update --phase failed --server stopped --finished failure
  fi
  return "$exit_code"
}
trap cleanup EXIT INT TERM

stop_server() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  SERVER_PID=""
}

start_server() {
  local server_log="$1"
  COMPETITION_DIR="$ROOT_DIR" PORT="$PORT" LLM_MOCK=0 bash "$BASELINE_DIR/run.sh" >"$server_log" 2>&1 &
  SERVER_PID=$!
  status_update --server starting
  for _ in $(seq 1 40); do
    if curl --fail --silent "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      status_update --server running
      return 0
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then echo "Server stopped during startup" >&2; return 1; fi
    sleep 0.5
  done
  echo "Server did not become healthy within 20 seconds" >&2
  return 1
}

run_load_test() {
  status_update --phase load_test --server stopped --load-state running
  start_server "$RESULT_DIR/logs/load-server.log"
  if ! python3 "$SCRIPT_DIR/load_test.py" --url "http://127.0.0.1:${PORT}" --token "$SERVER_TOKEN" --n 64 >"$RESULT_DIR/logs/load-test.log" 2>&1; then
    stop_server
    status_update --phase load_test_failed --server stopped --load-state failed
    echo "64-concurrency validation failed" >&2
    return 1
  fi
  stop_server
  python3 "$METRICS" parse-load --input "$RESULT_DIR/logs/load-test.log" --output "$RESULT_DIR/load-test.json"
  status_update --phase load_test_complete --server stopped --load-state passed --load-file "$RESULT_DIR/load-test.json"
  python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["passed"] else 1)' "$RESULT_DIR/load-test.json"
}

run_score_series() {
  local label="$1"
  local runs_file="$RESULT_DIR/${label}-runs.jsonl"
  : >"$runs_file"
  status_update --phase "${label}_scoring" --active-series "$label" --current-run 1 --server stopped
  start_server "$RESULT_DIR/logs/${label}-server.log"
  for i in $(seq 1 "$RUNS"); do
    local score_log="$RESULT_DIR/logs/${label}-${i}.log"
    status_update --phase "${label}_scoring" --active-series "$label" --current-run "$i" --server running
    if ! python3 "$ROOT_DIR/harness/score_local.py" \
      --tutor-url "http://127.0.0.1:${PORT}/tutor" \
      --guard-url "http://127.0.0.1:${PORT}/guard" \
      --token "$SERVER_TOKEN" --public "$ROOT_DIR/public" --kb "$ROOT_DIR/kb" --workers 8 >"$score_log" 2>&1; then
      stop_server
      status_update --phase "${label}_failed" --server stopped
      echo "${label} score run ${i} failed" >&2
      return 1
    fi
    if ! python3 "$METRICS" parse-score --input "$score_log" --output "$runs_file"; then
      stop_server
      status_update --phase "${label}_failed" --server stopped
      return 1
    fi
    status_update --phase "${label}_scoring" --active-series "$label" --current-run "$i" --server running --score-file "$runs_file"
    if ! python3 -c 'import json,sys; row=json.loads(open(sys.argv[1], encoding="utf-8").read().splitlines()[-1]); sys.exit(0 if not (row["invalid"] or row["judge_errors"]) else 1)' "$runs_file"; then
      stop_server
      status_update --phase "${label}_failed" --server stopped
      echo "${label} score run ${i} has format or judge errors" >&2
      return 1
    fi
  done
  stop_server
  python3 "$METRICS" summarize --runs "$runs_file" --expected-runs "$RUNS" --output "$RESULT_DIR/${label}-summary.json"
  status_update --phase "${label}_complete" --active-series "$label" --current-run "$RUNS" --server stopped
}

restore_candidate() {
  if [[ "$(git rev-parse HEAD)" != "$ORIGINAL_HEAD" ]]; then git switch "$ORIGINAL_BRANCH" >/dev/null; fi
}

echo "[experiment] candidate ${ORIGINAL_HEAD}: validating 64 concurrent requests"
run_load_test
echo "[experiment] candidate: running ${RUNS} public-set evaluations"
run_score_series candidate

DECISION_FILE="$RESULT_DIR/decision.json"
CHAMPION_FILE=""
if [[ -f "$CHAMPION_SUMMARY" ]]; then
  python3 "$METRICS" precheck --candidate "$RESULT_DIR/candidate-summary.json" --champion "$CHAMPION_SUMMARY" \
    --potential-margin "$POTENTIAL_MARGIN" --output "$RESULT_DIR/precheck.json"
  SHOULD_REEVALUATE="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["reevaluate_champion"]).lower())' "$RESULT_DIR/precheck.json")"
  if [[ "$SHOULD_REEVALUATE" == "true" ]]; then
    echo "[experiment] within broad range: re-evaluating ${CHAMPION_REF}"
    git switch --detach "$CHAMPION_REF" >/dev/null
    if ! run_score_series champion; then restore_candidate; exit 1; fi
    restore_candidate
    CHAMPION_FILE="$RESULT_DIR/champion-summary.json"
    python3 "$METRICS" decide --candidate "$RESULT_DIR/candidate-summary.json" --champion "$CHAMPION_FILE" \
      --delta "$DELTA" --guard-review-multiplier "$GUARD_REVIEW_MULTIPLIER" --output "$DECISION_FILE"
  else
    python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); json.dump({"decision":"keep_champion_without_retest","precheck":p}, open(sys.argv[2], "w"), ensure_ascii=False, indent=2); open(sys.argv[2], "a").write("\n")' "$RESULT_DIR/precheck.json" "$DECISION_FILE"
  fi
else
  python3 -c 'import json,sys; json.dump({"decision":"bootstrap_needs_review","reason":"champion_summary_missing"}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2); open(sys.argv[1], "a").write("\n")' "$DECISION_FILE"
fi

REPORT_ARGS=(report --experiment-id "$EXPERIMENT_ID" --base-commit "$BASE_COMMIT" --candidate-commit "$ORIGINAL_HEAD" \
  --scope "$SCOPE" --hypothesis "$HYPOTHESIS" --load-test "$RESULT_DIR/load-test.json" \
  --candidate "$RESULT_DIR/candidate-summary.json" --decision "$DECISION_FILE" --output "$RESULT_DIR/report.json")
if [[ -n "$CHAMPION_FILE" ]]; then REPORT_ARGS+=(--champion "$CHAMPION_FILE"); fi
python3 "$METRICS" "${REPORT_ARGS[@]}"

echo "[experiment] sanitized result"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps({"candidate": r["candidate_summary"], "decision": r["decision"]}, ensure_ascii=False, indent=2))' "$RESULT_DIR/report.json"
if [[ "$PUBLISH_RESULT" == "1" ]]; then "$PUBLISH" --report "$RESULT_DIR/report.json" --result-dir "$RESULT_DIR" --remote "$REMOTE"; fi
echo "[experiment] complete"
