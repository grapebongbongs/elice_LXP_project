#!/usr/bin/env bash
# Run one candidate experiment in an Elice checkout. Never persists request/answer bodies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$BASELINE_DIR/.." && pwd)"
METRICS="$SCRIPT_DIR/experiment_metrics.py"
PUBLISH="$SCRIPT_DIR/publish_experiment_result.sh"

usage() {
  cat <<'EOF'
Usage:
  bash baseline/scripts/run_experiment.sh \
    --id 20261111-001 --scope guard --hypothesis "..." \
    [--champion-ref origin/main] [--champion-summary experiment-reports/champion.json]

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
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$EXPERIMENT_ID" && -n "$SCOPE" && -n "$HYPOTHESIS" ]] || { usage >&2; exit 2; }
[[ "$SCOPE" == "tutor" || "$SCOPE" == "guard" || "$SCOPE" == "common" ]] || { echo "Invalid scope" >&2; exit 2; }
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo "--runs must be positive" >&2; exit 2; }

cd "$ROOT_DIR"
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi
[[ -n "${SERVER_TOKEN:-}" && -n "${OPENAI_API_KEY:-}" && -n "${OPENAI_BASE_URL:-}" && -n "${OPENAI_MODEL:-}" ]] || { echo "SERVER_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL must be set" >&2; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "Candidate checkout must be clean" >&2; exit 2; }
ORIGINAL_BRANCH="$(git branch --show-current)"
[[ -n "$ORIGINAL_BRANCH" && "$ORIGINAL_BRANCH" != "main" && "$ORIGINAL_BRANCH" != "champion" ]] || { echo "Run from a named candidate branch" >&2; exit 2; }
ORIGINAL_HEAD="$(git rev-parse HEAD)"
BASE_COMMIT="$(git rev-parse "$CHAMPION_REF")"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kosa-experiment.XXXXXX")"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
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
  for _ in $(seq 1 40); do
    if curl --fail --silent "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then echo "Server stopped during startup" >&2; return 1; fi
    sleep 0.5
  done
  echo "Server did not become healthy within 20 seconds" >&2
  return 1
}

run_load_test() {
  start_server "$TMP_DIR/load-server.log"
  if ! python3 "$SCRIPT_DIR/load_test.py" --url "http://127.0.0.1:${PORT}" --token "$SERVER_TOKEN" --n 64 >"$TMP_DIR/load-test.log" 2>&1; then
    stop_server; echo "64-concurrency validation failed" >&2; return 1
  fi
  stop_server
  python3 "$METRICS" parse-load --input "$TMP_DIR/load-test.log" --output "$TMP_DIR/load-test.json"
  python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["passed"] else 1)' "$TMP_DIR/load-test.json"
}

run_score_series() {
  local label="$1"
  local runs_file="$TMP_DIR/${label}-runs.jsonl"
  : >"$runs_file"
  start_server "$TMP_DIR/${label}-server.log"
  for i in $(seq 1 "$RUNS"); do
    local score_log="$TMP_DIR/${label}-${i}.log"
    if ! python3 "$ROOT_DIR/harness/score_local.py" \
      --tutor-url "http://127.0.0.1:${PORT}/tutor" \
      --guard-url "http://127.0.0.1:${PORT}/guard" \
      --token "$SERVER_TOKEN" --public "$ROOT_DIR/public" --kb "$ROOT_DIR/kb" --workers 8 >"$score_log" 2>&1; then
      stop_server; echo "${label} score run ${i} failed" >&2; return 1
    fi
    if ! python3 "$METRICS" parse-score --input "$score_log" --output "$runs_file"; then
      stop_server; return 1
    fi
  done
  stop_server
  python3 "$METRICS" summarize --runs "$runs_file" --expected-runs "$RUNS" --output "$TMP_DIR/${label}-summary.json"
}

restore_candidate() {
  if [[ "$(git rev-parse HEAD)" != "$ORIGINAL_HEAD" ]]; then git switch "$ORIGINAL_BRANCH" >/dev/null; fi
}

echo "[experiment] candidate ${ORIGINAL_HEAD}: validating 64 concurrent requests"
run_load_test
echo "[experiment] candidate: running ${RUNS} public-set evaluations"
run_score_series candidate

DECISION_FILE="$TMP_DIR/decision.json"
CHAMPION_FILE=""
if [[ -f "$CHAMPION_SUMMARY" ]]; then
  python3 "$METRICS" precheck --candidate "$TMP_DIR/candidate-summary.json" --champion "$CHAMPION_SUMMARY" \
    --potential-margin "$POTENTIAL_MARGIN" --output "$TMP_DIR/precheck.json"
  SHOULD_REEVALUATE="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["reevaluate_champion"]).lower())' "$TMP_DIR/precheck.json")"
  if [[ "$SHOULD_REEVALUATE" == "true" ]]; then
    echo "[experiment] within broad range: re-evaluating ${CHAMPION_REF}"
    git switch --detach "$CHAMPION_REF" >/dev/null
    if ! run_score_series champion; then restore_candidate; exit 1; fi
    restore_candidate
    CHAMPION_FILE="$TMP_DIR/champion-summary.json"
    python3 "$METRICS" decide --candidate "$TMP_DIR/candidate-summary.json" --champion "$CHAMPION_FILE" \
      --delta "$DELTA" --guard-review-multiplier "$GUARD_REVIEW_MULTIPLIER" --output "$DECISION_FILE"
  else
    python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); json.dump({"decision":"keep_champion_without_retest","precheck":p}, open(sys.argv[2], "w"), ensure_ascii=False, indent=2); open(sys.argv[2], "a").write("\n")' "$TMP_DIR/precheck.json" "$DECISION_FILE"
  fi
else
  python3 -c 'import json,sys; json.dump({"decision":"bootstrap_needs_review","reason":"champion_summary_missing"}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2); open(sys.argv[1], "a").write("\n")' "$DECISION_FILE"
fi

REPORT_ARGS=(report --experiment-id "$EXPERIMENT_ID" --base-commit "$BASE_COMMIT" --candidate-commit "$ORIGINAL_HEAD" \
  --scope "$SCOPE" --hypothesis "$HYPOTHESIS" --load-test "$TMP_DIR/load-test.json" \
  --candidate "$TMP_DIR/candidate-summary.json" --decision "$DECISION_FILE" --output "$TMP_DIR/report.json")
if [[ -n "$CHAMPION_FILE" ]]; then REPORT_ARGS+=(--champion "$CHAMPION_FILE"); fi
python3 "$METRICS" "${REPORT_ARGS[@]}"

echo "[experiment] sanitized result"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps({"candidate": r["candidate_summary"], "decision": r["decision"]}, ensure_ascii=False, indent=2))' "$TMP_DIR/report.json"
if [[ "$PUBLISH_RESULT" == "1" ]]; then "$PUBLISH" --report "$TMP_DIR/report.json" --remote "$REMOTE"; fi
echo "[experiment] complete"
