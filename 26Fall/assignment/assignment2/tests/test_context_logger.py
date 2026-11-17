import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config  # noqa: E402
from src.config import load_model_config  # noqa: E402
from src.context import maybe_compact, truncate  # noqa: E402
from src.logger import SCHEMA, TraceLogger  # noqa: E402


class TestTruncate(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(truncate("abc", 10), "abc")
        self.assertEqual(truncate(None, 10), "")

    def test_keeps_head_and_tail(self):
        text = "H" * 600 + "M" * 1000 + "T" * 600
        out = truncate(text, 1000)
        self.assertTrue(out.startswith("H" * 500))
        self.assertTrue(out.endswith("T" * 500))
        self.assertIn("1200 characters omitted", out)
        self.assertNotIn("M", out)


def _history(turns):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(turns):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c%d" % i}]})
        msgs.append({"role": "tool", "tool_call_id": "c%d" % i, "content": "out%d" % i})
    return msgs


class TestCompact(unittest.TestCase):
    def test_below_threshold_untouched(self):
        msgs = _history(10)
        before = json.dumps(msgs)
        self.assertEqual(maybe_compact(msgs, 500, threshold=1000, keep_recent_turns=3), 0)
        self.assertEqual(json.dumps(msgs), before)

    def test_compacts_old_keeps_recent(self):
        msgs = _history(10)
        n = maybe_compact(msgs, 5000, threshold=1000, keep_recent_turns=3)
        self.assertEqual(n, 7)
        tool_contents = [m["content"] for m in msgs if m["role"] == "tool"]
        self.assertEqual(tool_contents[:7], [config.COMPACTED_PLACEHOLDER] * 7)
        self.assertEqual(tool_contents[7:], ["out7", "out8", "out9"])
        # system / user / assistant messages untouched
        self.assertEqual(msgs[1]["content"], "u")
        # idempotent
        self.assertEqual(maybe_compact(msgs, 5000, threshold=1000, keep_recent_turns=3), 0)

    def test_few_turns_untouched(self):
        msgs = _history(2)
        self.assertEqual(maybe_compact(msgs, 5000, threshold=1000, keep_recent_turns=3), 0)


class TestConfig(unittest.TestCase):
    def test_defaults_fall_back_to_course_server(self):
        cfg = load_model_config({"CS2680_API_KEY": "k"})
        self.assertEqual(cfg.base_url, "https://api.cs2680.com/v1")
        self.assertEqual(cfg.model, "qwen3.6-35b-a3b")
        self.assertEqual(cfg.api_key, "k")
        self.assertFalse(cfg.is_openrouter)

    def test_overrides(self):
        cfg = load_model_config({"MADSLOOP_BASE_URL": "https://openrouter.ai/api/v1",
                                 "MADSLOOP_API_KEY": "m", "MADSLOOP_MODEL": "x",
                                 "CS2680_API_KEY": "k"})
        self.assertEqual((cfg.api_key, cfg.model), ("m", "x"))
        self.assertTrue(cfg.is_openrouter)

    def test_empty_values_fall_back(self):
        cfg = load_model_config({"MADSLOOP_BASE_URL": "", "MADSLOOP_MODEL": "", "MADSLOOP_API_KEY": "",
                                 "CS2680_API_KEY": "k"})
        self.assertEqual((cfg.base_url, cfg.model, cfg.api_key),
                         (config.DEFAULT_BASE_URL, config.DEFAULT_MODEL, "k"))


class TestLogger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="madsloop_log_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_disabled_writes_nothing(self):
        log = TraceLogger(False, self.dir)
        log.run_start("m", "/app")
        log.close()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "madsLoop_logs")))

    def test_schema_exact(self):
        log = TraceLogger(True, self.dir)
        log.run_start("m", "/app")
        log.api_request(1, 10, 2, 12)
        log.api_retry(1, "boom", 1.0)
        log.tool_call(1, "bash", {"command": "ls"})
        log.tool_result(1, "bash", "x", False)
        log.run_end("done", 1)
        log.close()
        with open(log.path) as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual([r["event"] for r in rows],
                         ["run_start", "api_request", "api_retry", "tool_call", "tool_result", "run_end"])
        for r in rows:
            self.assertEqual(set(r), {"timestamp", "event"} | set(SCHEMA[r["event"]]))
            self.assertIn("+00:00", r["timestamp"])

    def test_extra_field_rejected(self):
        log = TraceLogger(True, self.dir)
        with self.assertRaises(ValueError):
            log._write("run_end", reason="done", num_iterations=1, cost=3)
        log.close()

    def test_new_file_per_run(self):
        a = TraceLogger(True, self.dir)
        b = TraceLogger(True, self.dir)
        a.close()
        b.close()
        self.assertNotEqual(a.path, b.path)


if __name__ == "__main__":
    unittest.main()
