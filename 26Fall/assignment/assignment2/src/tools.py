"""Tool schemas and executors.

Every executor returns (text, is_error). Executors never raise for
problems caused by the model (bad arguments, missing files, ...); those
become error results the model can react to.
"""

import json
import os
import re
import signal
import subprocess

from . import config

# --------------------------------------------------------------------------
# Schemas (OpenAI function-calling format)
# --------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command with bash in the repository directory and return its "
                "exit code and combined stdout/stderr. Use it to search (grep -rn, find, ls), "
                "inspect git state, and run scripts and tests. stdin is closed, so interactive "
                "commands fail; pagers are disabled. Default timeout %ds, max %ds. "
                "Long output is truncated in the middle, so filter it (grep, head, tail) "
                "where possible." % (config.BASH_DEFAULT_TIMEOUT, config.BASH_MAX_TIMEOUT)
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to run."},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default %d, max %d)."
                        % (config.BASH_DEFAULT_TIMEOUT, config.BASH_MAX_TIMEOUT),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file and return it with line numbers. Returns at most %d lines "
                "per call and reports the total line count; use start_line/end_line to read "
                "other parts. The line-number prefixes are NOT part of the file: never copy "
                "them into edit_file arguments." % config.READ_DEFAULT_MAX_LINES
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the repository root or absolute."},
                    "start_line": {"type": "integer", "description": "First line to return (1-based, default 1)."},
                    "end_line": {"type": "integer", "description": "Last line to return (inclusive)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact string in an existing file. old_string must match the file "
                "content exactly (including indentation and whitespace) and must occur exactly "
                "once; include enough surrounding lines to make it unique. Do not include line "
                "numbers from read_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the repository root or absolute."},
                    "old_string": {"type": "string", "description": "Exact text to replace (must be unique in the file)."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a file with the given content (parent directories are created). "
                "Intended for new files, e.g. reproduction scripts under /tmp or a new module "
                "the task requires. To change an existing file, use edit_file instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the repository root or absolute."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Declare that the task is finished. Call this only after the fix is in place "
                "and you have verified it by running the relevant code or tests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Short summary of what was changed and how it was verified."},
                },
                "required": ["summary"],
            },
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
_REQUIRED = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in TOOL_SCHEMAS}
_PROPS = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in TOOL_SCHEMAS}

MAX_LINE_CHARS = 2000
_LINE_NO_PREFIX = re.compile(r"^\s*\d+\t", re.M)


class ToolError(Exception):
    """A problem to report back to the model as an error result."""


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def parse_arguments(name, raw):
    """Parse and validate a tool call's raw JSON arguments.

    Returns a dict, or raises ToolError with a message for the model.
    """
    if isinstance(raw, dict):
        args = raw
    else:
        raw = raw or ""
        if not raw.strip():
            args = {}
        else:
            try:
                args = json.loads(raw)
            except ValueError as exc:
                raise ToolError("Invalid JSON in arguments for %s: %s. Send a single JSON object." % (name, exc))
    if not isinstance(args, dict):
        raise ToolError("Arguments for %s must be a JSON object." % name)
    if name not in _REQUIRED:
        return args
    missing = [k for k in _REQUIRED[name] if k not in args]
    if missing:
        raise ToolError("Missing required argument(s) for %s: %s." % (name, ", ".join(missing)))
    for key, spec in _PROPS[name].items():
        if key not in args or args[key] is None:
            continue
        want = spec.get("type")
        val = args[key]
        if want == "string" and not isinstance(val, str):
            if key in ("content", "new_string", "old_string") and isinstance(val, (int, float)):
                args[key] = str(val)
            else:
                raise ToolError("Argument %s of %s must be a string." % (key, name))
        elif want == "integer":
            if isinstance(val, bool):
                raise ToolError("Argument %s of %s must be an integer." % (key, name))
            if isinstance(val, str) and val.strip().lstrip("-").isdigit():
                args[key] = int(val)
            elif isinstance(val, float) and val.is_integer():
                args[key] = int(val)
            elif not isinstance(val, int):
                raise ToolError("Argument %s of %s must be an integer." % (key, name))
    return args


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _decode(data):
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace")


def bash_env(environ=None):
    """Environment for the bash tool: no secrets, no harness deps on PYTHONPATH."""
    env = dict(os.environ if environ is None else environ)
    for key in config.SECRET_ENV_VARS:
        env.pop(key, None)
    pp = env.get("PYTHONPATH")
    if pp is not None:
        deps = os.path.normpath(config.HARNESS_DEPS_DIR)
        kept = [p for p in pp.split(os.pathsep) if p and os.path.normpath(p) != deps]
        if kept:
            env["PYTHONPATH"] = os.pathsep.join(kept)
        else:
            env.pop("PYTHONPATH")
    env["PAGER"] = "cat"
    env["GIT_PAGER"] = "cat"
    env["MANPAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("TERM", "dumb")
    return env


def run_git(workdir, args, timeout=60):
    proc = subprocess.run(
        ["git", "-C", workdir] + list(args),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, env=bash_env(),
    )
    return proc.returncode, _decode(proc.stdout)


def workdir_changes(workdir):
    """Return `git status --porcelain` output (tracked edits AND new files)."""
    try:
        code, out = run_git(workdir, ["status", "--porcelain", "--ignore-submodules"])
    except Exception:
        return ""
    if code != 0:
        return ""
    return out.strip()


def _number_lines(lines, start):
    out = []
    for i, line in enumerate(lines, start):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " [... line truncated]"
        out.append("%6d\t%s" % (i, line))
    return "\n".join(out)


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------

class ToolBox(object):
    def __init__(self, workdir):
        self.workdir = os.path.realpath(workdir)
        self._allowed_roots = [self.workdir, os.path.realpath("/tmp")]

    # -- paths ------------------------------------------------------------
    def resolve(self, path):
        if not path or not path.strip():
            raise ToolError("Empty path.")
        full = os.path.realpath(os.path.join(self.workdir, os.path.expanduser(path.strip())))
        for root in self._allowed_roots:
            try:
                if os.path.commonpath([full, root]) == root:
                    return full
            except ValueError:
                continue
        raise ToolError("Path %s is outside the allowed directories (%s and /tmp)." % (path, self.workdir))

    def display(self, full):
        if os.path.commonpath([full, self.workdir]) == self.workdir:
            return os.path.relpath(full, self.workdir)
        return full

    # -- dispatch ---------------------------------------------------------
    def execute(self, name, args):
        """Run tool `name` with parsed `args`; returns (text, is_error)."""
        fn = getattr(self, "tool_" + name, None) if name in TOOL_NAMES else None
        if fn is None:
            return ("Unknown tool %r. Available tools: %s." % (name, ", ".join(TOOL_NAMES)), True)
        try:
            return fn(**args)
        except ToolError as exc:
            return (str(exc), True)
        except TypeError as exc:
            return ("Bad arguments for %s: %s" % (name, exc), True)
        except Exception as exc:  # executor bug or OS error: report, never crash
            return ("%s failed: %s: %s" % (name, type(exc).__name__, exc), True)

    # -- bash -------------------------------------------------------------
    def tool_bash(self, command, timeout=None):
        if not command.strip():
            raise ToolError("Empty command.")
        if timeout is None:
            timeout = config.BASH_DEFAULT_TIMEOUT
        timeout = max(1, min(int(timeout), config.BASH_MAX_TIMEOUT))
        proc = subprocess.Popen(
            ["bash", "-c", command],
            cwd=self.workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=bash_env(),
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # A process escaped the group but still holds the pipe.
                out = b""
            text = _decode(out)
            return ("Command timed out after %ds; the process group was killed. "
                    "Output so far:\n%s" % (timeout, text), True)
        finally:
            _kill_group(proc)  # reap background children left in the group
        text = _decode(out)
        return ("exit code: %d\n%s" % (proc.returncode, text), False)

    # -- read_file ----------------------------------------------------------
    def tool_read_file(self, path, start_line=None, end_line=None):
        full = self.resolve(path)
        if os.path.isdir(full):
            raise ToolError("%s is a directory; use bash (ls, find) to list it." % path)
        if not os.path.exists(full):
            raise ToolError("File not found: %s" % path)
        with open(full, "rb") as fh:
            data = fh.read()
        if b"\x00" in data[:8192]:
            raise ToolError("%s looks like a binary file." % path)
        lines = _decode(data).splitlines()
        total = len(lines)
        if total == 0:
            return ("%s is empty." % self.display(full), False)
        start = 1 if start_line is None else max(1, start_line)
        if start > total:
            raise ToolError("start_line %d is past the end of %s (%d lines)." % (start, path, total))
        last = total if end_line is None else min(total, end_line)
        if last < start:
            raise ToolError("end_line must be >= start_line.")
        capped = False
        if last - start + 1 > config.READ_DEFAULT_MAX_LINES:
            last = start + config.READ_DEFAULT_MAX_LINES - 1
            capped = True
        body = _number_lines(lines[start - 1:last], start)
        header = "%s (lines %d-%d of %d)" % (self.display(full), start, last, total)
        footer = ""
        if capped or last < total:
            footer = "\n[Showing lines %d-%d of %d. Use start_line/end_line to read more.]" % (start, last, total)
        return ("%s\n%s%s" % (header, body, footer), False)

    # -- edit_file ----------------------------------------------------------
    def tool_edit_file(self, path, old_string, new_string):
        full = self.resolve(path)
        if not os.path.isfile(full):
            raise ToolError("File not found: %s (use write_file to create new files)." % path)
        if old_string == "":
            raise ToolError("old_string is empty. Give the exact text to replace, or use write_file for a new file.")
        if old_string == new_string:
            raise ToolError("old_string and new_string are identical; nothing to change.")
        with open(full, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            text = fh.read()

        # Files with CRLF endings: let LF-only arguments match.
        if "\r\n" in text and "\r\n" not in old_string and "\n" in old_string:
            if text.count(old_string.replace("\n", "\r\n")) >= 1:
                old_string = old_string.replace("\n", "\r\n")
                new_string = new_string.replace("\r\n", "\n").replace("\n", "\r\n")

        count = text.count(old_string)
        if count == 0:
            raise ToolError(self._not_found_message(path, text, old_string))
        if count > 1:
            offsets = []
            pos = text.find(old_string)
            while pos != -1:
                offsets.append(_line_of(text, pos))
                pos = text.find(old_string, pos + 1)
            raise ToolError(
                "old_string occurs %d times in %s (starting at lines %s). Include more surrounding "
                "lines so it matches exactly one place." % (count, path, ", ".join(str(n) for n in offsets)))

        pos = text.find(old_string)
        new_text = text[:pos] + new_string + text[pos + len(old_string):]
        with open(full, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.write(new_text)

        first = _line_of(new_text, pos)
        n_new = new_string.count("\n") + 1
        lines = new_text.splitlines()
        lo = max(1, first - 3)
        hi = min(len(lines), first + n_new + 2)
        snippet = _number_lines(lines[lo - 1:hi], lo)
        return ("Edited %s. Lines %d-%d after the edit:\n%s" % (self.display(full), lo, hi, snippet), False)

    @staticmethod
    def _not_found_message(path, text, old_string):
        msg = "old_string was not found in %s." % path
        if _LINE_NO_PREFIX.search(old_string):
            msg += (" It seems to contain line-number prefixes copied from read_file; "
                    "remove them and keep only the file's own text.")
            return msg
        # Point at candidate lines that match the first non-empty line loosely.
        first = next((l.strip() for l in old_string.splitlines() if l.strip()), "")
        hits = []
        if first:
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip() == first:
                    hits.append(i)
        if hits:
            msg += (" Its first line matches (ignoring indentation) at line(s) %s, so the "
                    "difference is probably whitespace/indentation or a later line. "
                    % ", ".join(str(h) for h in hits[:10]))
        msg += " Re-read the relevant lines with read_file and copy the text exactly."
        return msg

    # -- write_file ---------------------------------------------------------
    def tool_write_file(self, path, content):
        full = self.resolve(path)
        if os.path.isdir(full):
            raise ToolError("%s is a directory." % path)
        existed = os.path.exists(full)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        n = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        verb = "Overwrote existing file" if existed else "Created"
        return ("%s %s (%d lines)." % (verb, self.display(full), n), False)

    # -- done ---------------------------------------------------------------
    def tool_done(self, summary=""):
        # Acceptance is decided by the agent loop; this only acknowledges.
        return ("Finished: %s" % summary, False)


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
