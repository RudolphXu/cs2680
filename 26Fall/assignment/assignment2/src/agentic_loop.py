"""The agent loop: model proposes tool calls, the harness executes them."""

import time
import traceback

from . import config, prompts
from .context import maybe_compact, truncate
from .logger import debug
from .tools import TOOL_SCHEMAS, ToolError, parse_arguments, workdir_changes


class Agent(object):
    def __init__(self, llm, toolbox, logger, model_id, workdir,
                 max_iterations=None, time_limit_s=None, clock=time.monotonic):
        self.llm = llm
        self.tools = toolbox
        self.log = logger
        self.model_id = model_id
        self.workdir = workdir
        self.max_iterations = config.MAX_ITERATIONS if max_iterations is None else max_iterations
        self.time_limit_s = config.TIME_LIMIT_S if time_limit_s is None else time_limit_s
        self.clock = clock
        self.messages = []
        self.iteration = 0
        self.done_rejections = 0

    # ------------------------------------------------------------------
    def run(self, problem_statement, requirements="", interface=""):
        """Run to completion; returns the run_end reason."""
        reason = "error"
        self.log.run_start(self.model_id, self.workdir)
        try:
            self.messages = [
                {"role": "system", "content": prompts.build_system_prompt(self.workdir)},
                {"role": "user", "content": prompts.build_user_message(
                    problem_statement, requirements, interface)},
            ]
            reason = self._loop()
        except BaseException as exc:  # includes KeyboardInterrupt; run_end still written
            debug("run aborted: %s\n%s" % (exc, traceback.format_exc()))
            reason = "error"
            if not isinstance(exc, Exception):
                raise
        finally:
            self.log.run_end(reason, self.iteration)
            self.log.close()
            debug("run_end reason=%s iterations=%d" % (reason, self.iteration))
        return reason

    # ------------------------------------------------------------------
    def _loop(self):
        start = self.clock()
        deadline_mono = time.monotonic() + self.time_limit_s
        no_tool_streak = 0
        last_prompt_tokens = 0
        warned_iter = warned_time = False

        while True:
            elapsed = self.clock() - start
            if self.iteration >= self.max_iterations:
                return "max_iterations"
            if elapsed >= self.time_limit_s:
                return "time_limit"

            left = self.max_iterations - self.iteration
            if not warned_iter and left <= config.NEAR_LIMIT_WARNING:
                warned_iter = True
                self._remind(prompts.NEAR_ITERATION_LIMIT.format(left=left))
            remaining = self.time_limit_s - elapsed
            if not warned_time and remaining <= 5 * 60:
                warned_time = True
                self._remind(prompts.NEAR_TIME_LIMIT.format(minutes=max(1, int(remaining // 60))))

            self.iteration += 1
            n = maybe_compact(self.messages, last_prompt_tokens)
            if n:
                debug("compacted %d old tool outputs (prompt was %d tokens)" % (n, last_prompt_tokens))

            result = self.llm.chat(self.messages, TOOL_SCHEMAS, self.iteration, deadline=deadline_mono)
            last_prompt_tokens = result.prompt_tokens
            self.log.api_request(self.iteration, result.prompt_tokens,
                                 result.completion_tokens, result.total_tokens)
            self.messages.append(result.assistant_message())

            if not result.tool_calls:
                no_tool_streak += 1
                debug("no tool calls (%d in a row)" % no_tool_streak)
                if no_tool_streak >= config.MAX_NO_TOOL_REPLIES:
                    return "no_tool_calls"
                self._remind(prompts.NO_TOOL_CALL_REMINDER)
                continue
            no_tool_streak = 0

            finished = False
            for call in result.tool_calls:
                if finished:
                    # done was accepted earlier in this reply: do not execute later
                    # calls, but log and pair them so the trace stays complete.
                    self.log.tool_call(self.iteration, call.name, call.arguments)
                    text = "Skipped: the task was already finished by done."
                    self.log.tool_result(self.iteration, call.name, text, True)
                    self._tool_message(call.id, text)
                    continue
                finished = self._handle_call(call)
            if finished:
                return "done"

    # ------------------------------------------------------------------
    def _handle_call(self, call):
        """Execute one tool call; returns True when an accepted done ends the run."""
        accepted = False
        try:
            args = parse_arguments(call.name, call.arguments)
            logged_args = args
        except ToolError as exc:
            args = None
            logged_args = call.arguments  # raw string, exactly as the model sent it
            parse_error = str(exc)
        self.log.tool_call(self.iteration, call.name, logged_args)
        debug("iter %d tool %s %s" % (self.iteration, call.name, str(logged_args)[:200]))

        if args is None:
            text, is_error = parse_error, True
        elif call.name == "done":
            text, is_error, accepted = self._handle_done(args)
        else:
            text, is_error = self.tools.execute(call.name, args)

        text = truncate(text)
        self.log.tool_result(self.iteration, call.name, text, is_error)
        self._tool_message(call.id, text)
        return accepted

    def _handle_done(self, args):
        changes = workdir_changes(self.workdir)
        if not changes and self.done_rejections < config.MAX_DONE_REJECTIONS:
            self.done_rejections += 1
            debug("done rejected (empty diff), %d/%d" % (self.done_rejections, config.MAX_DONE_REJECTIONS))
            return prompts.DONE_REJECTED_EMPTY, True, False
        text, is_error = self.tools.execute("done", args)
        return text, is_error, True

    def _tool_message(self, call_id, text):
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": text})

    def _remind(self, text):
        self.messages.append({"role": "user", "content": text})
