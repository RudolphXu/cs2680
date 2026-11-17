"""Model client: one chat call with exponential-backoff retries.

`chat()` returns a normalized ChatResult so the agent loop does not depend
on the OpenAI response classes (and tests can use a fake client).
"""

import time
import uuid

from . import config
from .logger import debug

# HTTP statuses where retrying the same request cannot help.
FATAL_STATUS = (400, 401, 403, 404, 422)


class LLMError(Exception):
    pass


class ToolCall(object):
    def __init__(self, id, name, arguments):
        self.id = id
        self.name = name
        self.arguments = arguments


class ChatResult(object):
    def __init__(self, content, tool_calls, prompt_tokens, completion_tokens, total_tokens):
        self.content = content
        self.tool_calls = tool_calls
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def assistant_message(self):
        """The assistant turn to append to the history (reasoning is not resent)."""
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        return msg


def backoff_delay(attempt):
    """attempt 0 -> 1s, 1 -> 2s, 2 -> 4s, ... capped."""
    return min(config.API_BACKOFF_BASE_S * (2 ** attempt), config.API_BACKOFF_MAX_S)


def normalize(response):
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("response has no choices")
    message = choices[0].message
    calls = []
    for tc in (getattr(message, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        calls.append(ToolCall(tc.id or "call_%s" % uuid.uuid4().hex[:12],
                              fn.name or "", fn.arguments or ""))
    usage = getattr(response, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    tt = getattr(usage, "total_tokens", 0) or (pt + ct)
    return ChatResult(getattr(message, "content", None), calls, pt, ct, tt)


def _status_code(exc):
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    return code


class LLMClient(object):
    def __init__(self, model_config, on_retry=None, sleep=time.sleep, client=None):
        self.cfg = model_config
        self.on_retry = on_retry          # callback(iteration, error_str, backoff_s)
        self.sleep = sleep
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=model_config.base_url, api_key=model_config.api_key or "missing",
                            timeout=config.API_TIMEOUT_S, max_retries=0)
        self.client = client

    def _request_kwargs(self, messages, tools):
        kwargs = {"model": self.cfg.model, "messages": messages, "tools": tools}
        if self.cfg.is_openrouter:
            kwargs["extra_body"] = {"provider": config.OPENROUTER_PROVIDER}
        return kwargs

    def chat(self, messages, tools, iteration, deadline=None):
        attempt = 0
        while True:
            try:
                response = self.client.chat.completions.create(**self._request_kwargs(messages, tools))
                return normalize(response)
            except Exception as exc:
                status = _status_code(exc)
                err = "%s: %s" % (type(exc).__name__, exc)
                if status in FATAL_STATUS:
                    raise LLMError("non-retryable API error (HTTP %s): %s" % (status, err))
                if attempt >= config.API_MAX_RETRIES:
                    raise LLMError("API failed after %d retries: %s" % (attempt, err))
                delay = backoff_delay(attempt)
                if deadline is not None and time.monotonic() + delay > deadline:
                    raise LLMError("API failing and time limit reached: %s" % err)
                debug("API error (attempt %d), retrying in %.0fs: %s" % (attempt + 1, delay, err))
                if self.on_retry is not None:
                    self.on_retry(iteration, err[:2000], delay)
                self.sleep(delay)
                attempt += 1
