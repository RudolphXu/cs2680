#!/bin/bash
# Run madsLoop on a batch of tasks, N at a time, with resume and a deadline.
#
#   scripts/batch_run.sh [--until HH:MM] [--workers N] [--tasks "2 8 1 ..."] [--out DIR]
#   scripts/batch_run.sh --kill [--out DIR]       # stop now (see below)
#
# Per task, as soon as it finishes, everything goes to DIR/<idx>_<instance_id>/:
#   run_task.log  model_patch.diff  trace/  pro_eval/  eval.log  harness_commit  result.json
# result.json is written last (atomically): a task with result.json is done and
# is skipped on the next run. A task dir without it (interrupted) is renamed to
# *.aborted-<time> and the task is run again.
#
# Deadline: after --until (default 22:45 today) no new task starts; running
# tasks are allowed to finish.
# Safe stop:  touch DIR/STOP   -> no new tasks; running ones finish, then exit.
# Hard stop:  scripts/batch_run.sh --kill -> kills the scheduler and stops the
#             task containers; interrupted tasks are rerun next time.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.venv/bin:$PATH"
PY="$ROOT/.venv/bin/python"
# Test hook only: substitute a fake run_task.sh to exercise the scheduler.
RUN_TASK="${RUN_TASK:-evaluation_scripts/run_task.sh}"

OUT="$ROOT/run_logs/baseline"
UNTIL="22:45"
WORKERS=2
# Small images first so more tasks finish before the deadline.
TASKS="2 8 1 4 7 5 6 9"
KILL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --until) UNTIL="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --tasks) TASKS="$2"; shift 2 ;;
    --out) OUT="$(mkdir -p "$2" && cd "$2" && pwd)"; shift 2 ;;
    --kill) KILL=1; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT"
LOG="$OUT/batch.log"
PIDFILE="$OUT/batch.pid"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >&2; }

task_field() {  # task_field <idx> <field>
  "$PY" -c "import json,sys; t=list(json.load(open('evaluation_scripts/agent_task_input.json')).values())[int(sys.argv[1])]; print(t[sys.argv[2]])" "$1" "$2"
}

# ---------------------------------------------------------------- --kill
if [[ $KILL == 1 ]]; then
  if [[ -f "$PIDFILE" ]] && read -r SPID PGID < "$PIDFILE" && kill -0 "$SPID" 2>/dev/null; then
    log "kill requested: terminating scheduler $SPID and its process group $PGID"
    kill -TERM -- "-$PGID" 2>/dev/null || kill -TERM "$SPID" 2>/dev/null || true
  else
    echo "no running batch found ($PIDFILE)" >&2
  fi
  # docker run clients die with the group, but their containers keep running.
  for idx in $TASKS; do
    img="$(task_field "$idx" docker_image)"
    for c in $(docker ps -q --filter "ancestor=$img"); do
      log "stopping container $c ($img)"
      docker stop -t 10 "$c" >/dev/null || true
    done
  done
  rm -f "$PIDFILE"
  exit 0
fi

# ---------------------------------------------------------------- preflight
if [[ -f "$PIDFILE" ]] && read -r SPID _ < "$PIDFILE" && kill -0 "$SPID" 2>/dev/null; then
  echo "a batch is already running (pid $SPID)" >&2; exit 1
fi
if [[ -n "$(git status --porcelain -- src madsLoop.py docker_env.sh evaluation_scripts/run_task.sh)" ]]; then
  echo "refusing to run: uncommitted changes in the harness (src/, madsLoop.py, docker_env.sh, run_task.sh)" >&2
  exit 1
fi
if [[ -z "${MADSLOOP_API_KEY:-}${CS2680_API_KEY:-}" ]]; then
  echo "no API key (MADSLOOP_API_KEY / CS2680_API_KEY)" >&2; exit 1
fi
DEADLINE="$(date -d "today $UNTIL" +%s)" || { echo "bad --until $UNTIL" >&2; exit 2; }
if (( $(date +%s) >= DEADLINE )); then
  echo "deadline $UNTIL has already passed today; pass --until HH:MM" >&2; exit 1
fi
COMMIT="$(git rev-parse --short HEAD)"
echo "$$ $(ps -o pgid= $$ | tr -d " ")" > "$PIDFILE"
rm -f "$OUT/STOP"
trap 'log "scheduler received a signal; exiting"; rm -f "$PIDFILE"; exit 130' TERM INT
log "batch start: commit=$COMMIT tasks=[$TASKS] workers=$WORKERS until=$UNTIL out=$OUT"

# ---------------------------------------------------------------- one task
run_one() {
  local idx="$1" iid dir rc t0
  iid="$(task_field "$idx" instance_id)"
  dir="$OUT/${idx}_${iid}"
  if [[ -d "$dir" ]]; then
    mv "$dir" "$dir.aborted-$(date +%Y%m%d-%H%M%S)"
    log "task $idx: previous attempt was incomplete; moved aside"
  fi
  mkdir -p "$dir"
  echo "$COMMIT" > "$dir/harness_commit"
  rm -rf "madsLoop_logs/$iid" "model_patch_$iid.diff"
  t0=$(date +%s)
  log "task $idx: start ($iid)"

  bash "$RUN_TASK" "$idx" > "$dir/run_task.log" 2>&1
  rc=$?
  [[ -f "model_patch_$iid.diff" ]] && mv "model_patch_$iid.diff" "$dir/model_patch.diff"
  [[ -f "$dir/model_patch.diff" ]] || : > "$dir/model_patch.diff"
  mkdir -p "$dir/trace"
  [[ -d "madsLoop_logs/$iid" ]] && cp -r "madsLoop_logs/$iid/." "$dir/trace/"
  log "task $idx: agent finished in $(( ($(date +%s) - t0) / 60 ))m (run_task.sh exit $rc, patch $(wc -c < "$dir/model_patch.diff") bytes)"

  if [[ -s "$dir/model_patch.diff" ]]; then
    "$PY" - "$dir" "$iid" <<'PYEOF'
import json, sys
d, iid = sys.argv[1], sys.argv[2]
patch = open(d + "/model_patch.diff").read()
json.dump([{"instance_id": iid, "model_patch": patch, "prefix": "madsLoop"}],
          open(d + "/patches.json", "w"), indent=2)
PYEOF
    # One evaluation at a time; evaluate.sh is unchanged, pointed at this dir.
    flock "$OUT/.eval.lock" env MADSLOOP_REPO="$dir" SWEBENCH_PRO_OS="$ROOT/SWE-bench_Pro-os" \
      NUM_WORKERS=1 bash evaluation_scripts/evaluate.sh > "$dir/eval.log" 2>&1 \
      || log "task $idx: evaluate.sh exited non-zero (see eval.log)"
  fi

  local line
  line="$("$PY" scripts/task_report.py task "$dir" "$idx" "$iid" 2>>"$dir/eval.log")" \
    || line="task_report failed (see eval.log)"
  flock "$OUT/.summary.lock" "$PY" scripts/task_report.py summary "$OUT"
  log "task $idx: done in $(( ($(date +%s) - t0) / 60 ))m: $line"
}

# ---------------------------------------------------------------- scheduler
stopped=""
for idx in $TASKS; do
  iid="$(task_field "$idx" instance_id)"
  if [[ -f "$OUT/${idx}_${iid}/result.json" ]]; then
    log "task $idx: already has result.json; skipping"
    continue
  fi
  while (( $(jobs -rp | wc -l) >= WORKERS )); do wait -n 2>/dev/null || true; done
  if [[ -f "$OUT/STOP" ]]; then stopped="STOP file"; break; fi
  if (( $(date +%s) >= DEADLINE )); then stopped="deadline $UNTIL"; break; fi
  run_one "$idx" &
  sleep 5  # stagger container start-up / image checks
done
[[ -n "$stopped" ]] && log "not starting more tasks ($stopped); waiting for running tasks"
wait
"$PY" scripts/task_report.py summary "$OUT"
log "batch end: $(grep -o '已完成 [0-9]* 个任务，RESOLVED [0-9]* 个' "$OUT/summary.md")"
rm -f "$PIDFILE"
