import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, prompts  # noqa: E402
from src.agentic_loop import Agent  # noqa: E402
from src.config import ModelConfig  # noqa: E402
from src.llm import ChatResult, LLMClient, LLMError, ToolCall, backoff_delay  # noqa: E402
from src.logger import SCHEMA, TraceLogger  # noqa: E402
from src.tools import ToolBox  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_log_schema import check_file  # noqa: E402


def call(name, args, cid=None):
    raw = args if isinstance(args, str) else json.dumps(args)
    return ToolCall(cid or "id_%s_%d" % (name, id(raw)), name, raw)


def reply(*calls, content=""):
    return ChatResult(content, list(calls), 100, 10, 110)


class FakeLLM(object):
    """Returns scripted replies; records the messages it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, messages, tools, iteration, deadline=None):
        self.seen.append(copy.deepcopy(messages))
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class LoopTestBase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="madsloop_repo_")
        self.logroot = tempfile.mkdtemp(prefix="madsloop_logs_")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        with open(os.path.join(self.repo, "m.py"), "w") as fh:
            fh.write("def f():\n    return 1\n")
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.logroot, ignore_errors=True)

    def run_agent(self, script, **kw):
        self.llm = FakeLLM(script)
        self.logger = TraceLogger(True, self.logroot)
        self.agent = Agent(self.llm, ToolBox(self.repo), self.logger, "test-model", self.repo, **kw)
        reason = self.agent.run("Fix f", "Return 2", "")
        with open(self.logger.path) as fh:
            self.rows = [json.loads(l) for l in fh]
        self.assertEqual(check_file(self.logger.path), [])
        self.assert_pairing(self.agent.messages)
        return reason

    def assert_pairing(self, messages):
        pending = []
        for m in messages:
            if m["role"] == "assistant":
                self.assertEqual(pending, [], "previous tool calls unanswered")
                pending = [tc["id"] for tc in m.get("tool_calls", [])]
            elif m["role"] == "tool":
                self.assertIn(m["tool_call_id"], pending)
                pending.remove(m["tool_call_id"])
            else:
                self.assertEqual(pending, [], "user message between call and result")

    def events(self, name):
        return [r for r in self.rows if r["event"] == name]


class TestLoop(LoopTestBase):
    def test_happy_path_multiple_calls(self):
        edit = call("edit_file", {"path": "m.py", "old_string": "return 1", "new_string": "return 2"}, "c1")
        run = call("bash", {"command": "python3 -c 'import m; print(m.f())'"}, "c2")
        reason = self.run_agent([reply(edit, run), reply(call("done", {"summary": "ok"}, "c3"))])
        self.assertEqual(reason, "done")
        self.assertEqual([r["event"] for r in self.rows],
                         ["run_start", "api_request", "tool_call", "tool_result", "tool_call",
                          "tool_result", "api_request", "tool_call", "tool_result", "run_end"])
        self.assertEqual(self.rows[-1]["num_iterations"], 2)
        self.assertIn("exit code: 0\n2", self.events("tool_result")[1]["result"])
        # results go back in call order, each paired with its call id
        tool_msgs = [m for m in self.llm.seen[1] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["c1", "c2"])

    def test_user_message_sections(self):
        self.run_agent([reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
                        reply(call("done", {"summary": "x"}))])
        user = self.llm.seen[0][1]["content"]
        self.assertIn("# Problem statement\n\nFix f", user)
        self.assertIn("# Requirements\n\nReturn 2", user)
        self.assertNotIn("# Interface", user)  # empty -> omitted
        self.assertIn(self.repo, self.llm.seen[0][0]["content"])

    def test_logged_result_equals_model_input(self):
        big = "python3 -c \"print('x' * 50000)\""
        self.run_agent([reply(call("bash", {"command": big}, "b1")),
                        reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
                        reply(call("done", {"summary": "x"}))])
        logged = self.events("tool_result")[0]["result"]
        sent = [m for m in self.llm.seen[1] if m["role"] == "tool"][0]["content"]
        self.assertEqual(logged, sent)
        self.assertLessEqual(len(logged), config.TOOL_OUTPUT_MAX_CHARS + 100)
        self.assertIn("characters omitted", logged)

    def test_bad_json_unknown_tool_and_tool_error(self):
        reason = self.run_agent([
            reply(call("bash", '{"command": "ls"', "j1"),
                  call("grep_tool", {"q": "x"}, "j2"),
                  call("read_file", {"path": "missing.py"}, "j3")),
            reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
            reply(call("done", {"summary": "x"})),
        ])
        self.assertEqual(reason, "done")
        results = self.events("tool_result")[:3]
        self.assertTrue(all(r["is_error"] for r in results))
        self.assertIn("Invalid JSON", results[0]["result"])
        self.assertIn("Unknown tool", results[1]["result"])
        self.assertIn("not found", results[2]["result"])
        # raw string logged for unparseable arguments
        self.assertEqual(self.events("tool_call")[0]["arguments"], '{"command": "ls"')

    def test_no_tool_calls_reminds_then_stops(self):
        reason = self.run_agent([reply(content="thinking"), reply(content="hmm"), reply(content="bye")])
        self.assertEqual(reason, "no_tool_calls")
        self.assertEqual(self.rows[-1]["num_iterations"], 3)
        self.assertEqual(self.llm.seen[1][-1], {"role": "user", "content": prompts.NO_TOOL_CALL_REMINDER})

    def test_no_tool_streak_resets(self):
        reason = self.run_agent([
            reply(content="a"), reply(content="b"),
            reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
            reply(content="c"), reply(content="d"),
            reply(call("done", {"summary": "x"})),
        ])
        self.assertEqual(reason, "done")

    def test_done_rejected_on_empty_diff_then_accepted(self):
        script = [reply(call("done", {"summary": "nothing"})) for _ in range(config.MAX_DONE_REJECTIONS + 1)]
        reason = self.run_agent(script)
        self.assertEqual(reason, "done")
        results = self.events("tool_result")
        self.assertEqual(len(results), config.MAX_DONE_REJECTIONS + 1)
        for r in results[:-1]:
            self.assertTrue(r["is_error"])
            self.assertIn("rejected", r["result"])
        self.assertFalse(results[-1]["is_error"])

    def test_done_accepted_with_new_file_only(self):
        reason = self.run_agent([
            reply(call("write_file", {"path": "newmod.py", "content": "X = 1\n"})),
            reply(call("done", {"summary": "added"})),
        ])
        self.assertEqual(reason, "done")
        self.assertFalse(self.events("tool_result")[-1]["is_error"])

    def test_calls_after_accepted_done_are_skipped_but_paired(self):
        marker = os.path.join(self.repo, "should_not_exist")
        reason = self.run_agent([
            reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
            reply(call("done", {"summary": "x"}, "d1"), call("bash", {"command": "touch %s" % marker}, "d2")),
        ])
        self.assertEqual(reason, "done")
        self.assertFalse(os.path.exists(marker))
        self.assertIn("Skipped", self.events("tool_result")[-1]["result"])

    def test_max_iterations_with_warning(self):
        script = [reply(call("bash", {"command": "true"})) for _ in range(12)]
        reason = self.run_agent(script, max_iterations=12)
        self.assertEqual(reason, "max_iterations")
        self.assertEqual(self.rows[-1]["num_iterations"], 12)
        reminders = [m for m in self.agent.messages if m["role"] == "user" and "steps remain" in m["content"]]
        self.assertEqual(len(reminders), 1)

    def test_time_limit(self):
        ticks = iter(range(0, 10000, 400))  # each clock read advances 400s
        self.llm = FakeLLM([reply(call("bash", {"command": "true"})) for _ in range(20)])
        self.logger = TraceLogger(True, self.logroot)
        agent = Agent(self.llm, ToolBox(self.repo), self.logger, "m", self.repo,
                      time_limit_s=2700, clock=lambda: next(ticks))
        self.assertEqual(agent.run("p"), "time_limit")
        with open(self.logger.path) as fh:
            last = json.loads(fh.readlines()[-1])
        self.assertEqual(last["reason"], "time_limit")
        self.assertTrue(any("minutes remain" in m["content"] for m in agent.messages if m["role"] == "user"))

    def test_exception_still_writes_run_end(self):
        reason = self.run_agent([reply(call("bash", {"command": "true"})), LLMError("boom")])
        self.assertEqual(reason, "error")
        self.assertEqual(self.rows[-1], {**self.rows[-1], "event": "run_end", "reason": "error", "num_iterations": 2})

    def test_logging_disabled(self):
        llm = FakeLLM([reply(call("edit_file", {"path": "m.py", "old_string": "1", "new_string": "2"})),
                       reply(call("done", {"summary": "x"}))])
        cwd = os.getcwd()
        os.chdir(self.logroot)
        try:
            agent = Agent(llm, ToolBox(self.repo), TraceLogger(False), "m", self.repo)
            self.assertEqual(agent.run("p"), "done")
        finally:
            os.chdir(cwd)
        self.assertFalse(os.path.exists(os.path.join(self.logroot, "madsLoop_logs")))


# --------------------------------------------------------------------------
# LLM client retries
# --------------------------------------------------------------------------

class _Err(Exception):
    def __init__(self, msg, status=None):
        Exception.__init__(self, msg)
        self.status_code = status


class _Obj(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _response():
    fn = _Obj(name="bash", arguments='{"command": "ls"}')
    msg = _Obj(content=None, tool_calls=[_Obj(id="x1", function=fn)], reasoning_content="secret thoughts")
    usage = _Obj(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    return _Obj(choices=[_Obj(message=msg)], usage=usage)


class _FakeCompletions(object):
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(outcomes):
    comp = _FakeCompletions(outcomes)
    return _Obj(chat=_Obj(completions=comp)), comp


class TestLLMClient(unittest.TestCase):
    def make(self, outcomes, base_url="https://api.cs2680.com/v1"):
        client, comp = _client(outcomes)
        self.retries, self.sleeps = [], []
        llm = LLMClient(ModelConfig(base_url, "k", "qwen3.6-35b-a3b"),
                        on_retry=lambda *a: self.retries.append(a),
                        sleep=self.sleeps.append, client=client)
        return llm, comp

    def test_retry_with_backoff(self):
        llm, comp = self.make([_Err("conn reset"), _Err("rate", 429), _Err("bad gw", 502), _response()])
        res = llm.chat([], [], 4)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])
        self.assertEqual([r[0] for r in self.retries], [4, 4, 4])
        self.assertEqual([r[2] for r in self.retries], [1.0, 2.0, 4.0])
        self.assertEqual(res.tool_calls[0].name, "bash")
        self.assertEqual((res.prompt_tokens, res.completion_tokens, res.total_tokens), (5, 3, 8))

    def test_empty_choices_retried(self):
        llm, _ = self.make([_Obj(choices=[], usage=None), _response()])
        llm.chat([], [], 1)
        self.assertEqual(self.sleeps, [1.0])

    def test_fatal_status_not_retried(self):
        llm, _ = self.make([_Err("bad request", 400)])
        with self.assertRaises(LLMError):
            llm.chat([], [], 1)
        self.assertEqual(self.sleeps, [])

    def test_gives_up_after_max_retries(self):
        llm, _ = self.make([_Err("down", 503)] * (config.API_MAX_RETRIES + 1))
        with self.assertRaises(LLMError):
            llm.chat([], [], 1)
        self.assertEqual(len(self.sleeps), config.API_MAX_RETRIES)

    def test_backoff_capped(self):
        self.assertEqual(backoff_delay(0), 1.0)
        self.assertEqual(backoff_delay(3), 8.0)
        self.assertEqual(backoff_delay(20), config.API_BACKOFF_MAX_S)

    def test_provider_only_for_openrouter(self):
        llm, comp = self.make([_response()])
        llm.chat([], [], 1)
        self.assertNotIn("extra_body", comp.kwargs[0])
        self.assertEqual(comp.kwargs[0]["model"], "qwen3.6-35b-a3b")
        llm, comp = self.make([_response()], base_url="https://openrouter.ai/api/v1")
        llm.chat([], [], 1)
        self.assertEqual(comp.kwargs[0]["extra_body"], {"provider": config.OPENROUTER_PROVIDER})

    def test_reasoning_not_resent(self):
        llm, _ = self.make([_response()])
        msg = llm.chat([], [], 1).assistant_message()
        self.assertEqual(msg["content"], "")
        self.assertNotIn("reasoning_content", json.dumps(msg))
        self.assertEqual(msg["tool_calls"][0]["function"]["arguments"], '{"command": "ls"}')


if __name__ == "__main__":
    unittest.main()
