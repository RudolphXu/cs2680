"""Trace logging (strict handout schema) and stderr debug output."""

import json
import os
import sys
from datetime import datetime, timezone

# Exactly the fields the handout allows per event (besides timestamp/event).
SCHEMA = {
    "run_start": ("model_id", "workdir"),
    "api_request": ("iteration", "prompt_tokens", "completion_tokens", "total_tokens"),
    "api_retry": ("iteration", "error", "backoff_s"),
    "tool_call": ("iteration", "tool_name", "arguments"),
    "tool_result": ("iteration", "tool_name", "result", "is_error"),
    "run_end": ("reason", "num_iterations"),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def debug(msg):
    """Developer output; never goes into the trace."""
    try:
        sys.stderr.write("[madsLoop] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


class TraceLogger(object):
    """Writes one JSONL file per run into <log_root>/madsLoop_logs/ when enabled."""

    def __init__(self, enabled, log_root=None):
        self.enabled = enabled
        self.path = None
        self._fh = None
        if not enabled:
            return
        log_dir = os.path.join(log_root or os.getcwd(), "madsLoop_logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = os.path.join(log_dir, "run_%s.jsonl" % stamp)
        self._fh = open(self.path, "a", encoding="utf-8")

    def _write(self, event, **fields):
        if not self.enabled or self._fh is None:
            return
        expected = SCHEMA[event]
        if set(fields) != set(expected):
            raise ValueError("bad fields for %s: %s" % (event, sorted(fields)))
        record = {"timestamp": now_iso(), "event": event}
        for key in expected:
            record[key] = fields[key]
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def run_start(self, model_id, workdir):
        self._write("run_start", model_id=model_id, workdir=workdir)

    def api_request(self, iteration, prompt_tokens, completion_tokens, total_tokens):
        self._write("api_request", iteration=iteration, prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens, total_tokens=total_tokens)

    def api_retry(self, iteration, error, backoff_s):
        self._write("api_retry", iteration=iteration, error=error, backoff_s=backoff_s)

    def tool_call(self, iteration, tool_name, arguments):
        self._write("tool_call", iteration=iteration, tool_name=tool_name, arguments=arguments)

    def tool_result(self, iteration, tool_name, result, is_error):
        self._write("tool_result", iteration=iteration, tool_name=tool_name,
                    result=result, is_error=is_error)

    def run_end(self, reason, num_iterations):
        self._write("run_end", reason=reason, num_iterations=num_iterations)

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
