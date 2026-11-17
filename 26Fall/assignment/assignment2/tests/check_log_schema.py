#!/usr/bin/env python3
"""Validate madsLoop trace logs against the handout schema.

    python tests/check_log_schema.py madsLoop_logs/            # every *.jsonl below
    python tests/check_log_schema.py madsLoop_logs/x/run_1.jsonl

Checks: valid JSON per line; exactly the allowed fields per event; ISO-8601
timestamps; run_start first and run_end last (exactly once each); iteration
numbers non-decreasing; every tool_call followed by its tool_result.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.logger import SCHEMA  # noqa: E402

RUN_END_REASONS = {"done", "no_tool_calls", "error", "max_iterations", "time_limit"}


def check_file(path):
    errors = []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            try:
                rows.append((n, json.loads(line)))
            except ValueError as exc:
                errors.append("line %d: invalid JSON (%s)" % (n, exc))
    if not rows:
        return errors + ["empty log"]

    last_iter = 0
    open_call = None
    for n, r in rows:
        ev = r.get("event")
        if ev not in SCHEMA:
            errors.append("line %d: unknown event %r" % (n, ev))
            continue
        want = {"timestamp", "event"} | set(SCHEMA[ev])
        if set(r) != want:
            errors.append("line %d (%s): fields %s, expected %s" % (n, ev, sorted(r), sorted(want)))
        try:
            datetime.fromisoformat(r.get("timestamp", ""))
        except (TypeError, ValueError):
            errors.append("line %d: bad timestamp %r" % (n, r.get("timestamp")))
        it = r.get("iteration")
        if it is not None:
            if not isinstance(it, int) or it < last_iter:
                errors.append("line %d: iteration %r goes backwards" % (n, it))
            else:
                last_iter = it
        if ev == "tool_call":
            if open_call is not None:
                errors.append("line %d: tool_call before previous tool_result" % n)
            open_call = r.get("tool_name")
        elif ev == "tool_result":
            if open_call != r.get("tool_name"):
                errors.append("line %d: tool_result without matching tool_call" % n)
            if not isinstance(r.get("is_error"), bool):
                errors.append("line %d: is_error must be bool" % n)
            open_call = None
        elif ev == "api_request":
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if not isinstance(r.get(k), int):
                    errors.append("line %d: %s must be int" % (n, k))
        elif ev == "run_end" and r.get("reason") not in RUN_END_REASONS:
            errors.append("line %d: unexpected reason %r" % (n, r.get("reason")))

    events = [r.get("event") for _, r in rows]
    if events[0] != "run_start" or events.count("run_start") != 1:
        errors.append("run_start must be the first line and appear once")
    if events[-1] != "run_end" or events.count("run_end") != 1:
        errors.append("run_end must be the last line and appear once")
    # num_iterations counts iterations started; the last one may have died in
    # the API call before logging anything, hence last_iter or last_iter + 1.
    num = rows[-1][1].get("num_iterations")
    if rows[-1][1].get("event") == "run_end" and num not in (last_iter, last_iter + 1):
        errors.append("num_iterations %r inconsistent with last iteration %d" % (num, last_iter))
    return errors


def main(argv):
    paths = []
    for arg in argv or ["madsLoop_logs"]:
        if os.path.isdir(arg):
            for root, _, files in os.walk(arg):
                paths += [os.path.join(root, f) for f in sorted(files) if f.endswith(".jsonl")]
        else:
            paths.append(arg)
    bad = 0
    for p in paths:
        errs = check_file(p)
        print("%s %s" % ("OK  " if not errs else "FAIL", p))
        for e in errs:
            print("     " + e)
        bad += bool(errs)
    print("%d/%d files valid" % (len(paths) - bad, len(paths)))
    return 1 if bad or not paths else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
