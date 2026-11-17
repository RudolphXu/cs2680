"""madsLoop.py end to end against a local fake OpenAI-compatible server.

Exercises the real openai client (request format, tool-call parsing, usage),
the CLI, and the stdout-stays-empty contract run_task.sh relies on.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import madsLoop  # noqa: E402
from check_log_schema import check_file  # noqa: E402


def completion(tool_calls=None, content=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {"id": "call_%d" % i, "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(tool_calls)
        ]
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "qwen3.6-35b-a3b",
        "choices": [{"index": 0, "message": msg,
                     "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
    }


class FakeServer(object):
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body,
                                       "auth": self.headers.get("Authorization")})
                item = outer.script.pop(0)
                status, payload = item if isinstance(item, tuple) else (200, item)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestDecodeField(unittest.TestCase):
    def test_double_encoded(self):
        self.assertEqual(madsLoop.decode_field('"# Title:\\n\\nBody \\"q\\""'), '# Title:\n\nBody "q"')

    def test_plain_unchanged(self):
        self.assertEqual(madsLoop.decode_field("plain text"), "plain text")
        self.assertEqual(madsLoop.decode_field(""), "")
        self.assertEqual(madsLoop.decode_field('"not closed'), '"not closed')

    def test_non_string_json_unchanged(self):
        self.assertEqual(madsLoop.decode_field('"a" and "b"'), '"a" and "b"')

    def test_real_task_fields(self):
        path = os.path.join(ROOT, "evaluation_scripts", "agent_task_input.json")
        task = list(json.load(open(path)).values())[0]
        ps = madsLoop.decode_field(task["problem_statement"])
        self.assertTrue(ps.startswith("# Title:\n"))
        self.assertIn("is_python_identifier", madsLoop.decode_field(task["requirements"]))
        self.assertEqual(madsLoop.decode_field(task["interface"]), "No new interfaces are introduced")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="madsloop_cli_repo_")
        self.cwd = tempfile.mkdtemp(prefix="madsloop_cli_cwd_")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        with open(os.path.join(self.repo, "m.py"), "w") as fh:
            fh.write("def f():\n    return 1\n")
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.cwd, ignore_errors=True)

    def run_cli(self, server, extra=(), log=True):
        env = dict(os.environ)
        env.update({"MADSLOOP_BASE_URL": server.url, "MADSLOOP_API_KEY": "test-key",
                    "MADSLOOP_MODEL": "", "PYTHONDONTWRITEBYTECODE": "1"})
        req = os.path.join(self.cwd, "req.txt")
        with open(req, "w") as fh:
            fh.write(json.dumps("f must return 2"))
        cmd = [sys.executable, os.path.join(ROOT, "madsLoop.py"), "-p", json.dumps("Fix f")]
        cmd += ["--requirements-file", req] + list(extra)
        if log:
            cmd.append("--log")
        cmd.append(self.repo)
        return subprocess.run(cmd, cwd=self.cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120)

    def logs(self):
        d = os.path.join(self.cwd, "madsLoop_logs")
        return [os.path.join(d, f) for f in sorted(os.listdir(d))] if os.path.isdir(d) else []

    def test_end_to_end(self):
        server = FakeServer([
            (500, {"error": {"message": "temporary"}}),
            completion([("edit_file", {"path": "m.py", "old_string": "return 1", "new_string": "return 2"})]),
            completion([("done", {"summary": "fixed"})]),
        ])
        try:
            proc = self.run_cli(server)
        finally:
            server.close()
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stdout, b"", "stdout must stay empty (reserved for the patch)")
        with open(os.path.join(self.repo, "m.py")) as fh:
            self.assertIn("return 2", fh.read())

        first = server.requests[0]
        self.assertEqual(first["path"], "/v1/chat/completions")
        self.assertEqual(first["auth"], "Bearer test-key")
        self.assertEqual(first["body"]["model"], "qwen3.6-35b-a3b")  # empty env -> default
        self.assertNotIn("provider", first["body"])
        self.assertEqual([t["function"]["name"] for t in first["body"]["tools"]],
                         ["bash", "read_file", "edit_file", "write_file", "done"])
        user = first["body"]["messages"][1]["content"]
        self.assertIn("# Problem statement\n\nFix f", user)
        self.assertIn("# Requirements\n\nf must return 2", user)

        logs = self.logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(check_file(logs[0]), [])
        rows = [json.loads(l) for l in open(logs[0])]
        self.assertEqual([r["event"] for r in rows],
                         ["run_start", "api_retry", "api_request", "tool_call", "tool_result",
                          "api_request", "tool_call", "tool_result", "run_end"])
        self.assertEqual(rows[0]["workdir"], self.repo)
        self.assertEqual(rows[-1]["reason"], "done")

    def test_no_log_flag(self):
        server = FakeServer([completion(content="a"), completion(content="b"), completion(content="c")])
        try:
            proc = self.run_cli(server, log=False)
        finally:
            server.close()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.logs(), [])

    def test_fatal_api_error_exit_code(self):
        server = FakeServer([(401, {"error": {"message": "bad key"}})])
        try:
            proc = self.run_cli(server)
        finally:
            server.close()
        self.assertEqual(proc.returncode, 1)
        rows = [json.loads(l) for l in open(self.logs()[0])]
        self.assertEqual(rows[-1]["reason"], "error")

    def test_bad_workdir(self):
        self.assertEqual(madsLoop.main(["-p", "x", "/nonexistent/dir"]), madsLoop.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
