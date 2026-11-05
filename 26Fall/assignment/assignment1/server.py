"""Web frontend for Claude Code — backend.

Two ways to produce an event stream, both sent to the browser as
Server-Sent Events (SSE):

- Live mode (/api/run): spawns `claude -p ... --output-format stream-json`
  in the chosen working directory and forwards each stdout line at once.
- Replay mode (/api/replay): reads a recorded .jsonl file from samples/ and
  sends one line about every 0.3 s, imitating a live run.

Besides plain `data:` messages (one per Claude Code event) the server sends
two named SSE events of its own:

- `run_error`  the run could not start (bad directory, `claude` not found).
               data: {"message": ...}
- `end`        the stream is over. Live mode adds the exit code and stderr.
               data: {"exit_code": int | null, "stderr": str}

Start with:  .venv/bin/uvicorn server:app --reload --port 8000
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "samples"
STATIC_DIR = BASE_DIR / "static"

REPLAY_DELAY_SECONDS = 0.3

# Name or path of the Claude Code CLI. Override with the CLAUDE_BIN
# environment variable (useful for testing with a fake script).
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

# asyncio refuses lines longer than its read limit (64 KiB by default).
# A single event can hold a whole file's contents, so allow much more.
MAX_LINE_BYTES = 64 * 1024 * 1024

# How much of stderr to send to the page when a run fails.
STDERR_TAIL_CHARS = 4000

app = FastAPI(title="Claude Code Web Frontend")


def sse(data: dict, event: str | None = None) -> str:
    """Format one Server-Sent Event message.

    `json.dumps` never emits raw newlines, so the payload fits on one
    `data:` line, as SSE requires.
    """
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


def parse_line(line: str, line_no: int) -> dict:
    """Parse one line of stream-json. A bad line becomes a `_parse_error` event."""
    try:
        event = json.loads(line)
        if isinstance(event, dict):
            return event
    except json.JSONDecodeError:
        pass
    return {"type": "_parse_error", "line": line_no, "raw": line[:2000]}


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------


def claude_env() -> dict:
    """Environment for the child process.

    SPEC §3: never use ANTHROPIC_API_KEY; the CLI must use the existing
    login. Remove the variable in case the shell that started the server has it.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


@app.get("/api/run")
async def run(
    prompt: str = Query(..., min_length=1),
    cwd: str = Query(..., min_length=1, description="The agent's working directory"),
    resume: str | None = Query(
        None,
        # Must not start with "-", or claude would read it as another option.
        pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,99}$",
        description="session_id of an earlier run to continue (SPEC §6.5)",
    ),
) -> StreamingResponse:
    """Run Claude Code once and stream its events (SPEC §4).

    With `resume`, this is a follow-up run: `--resume <session_id>` continues
    that conversation. Claude Code stores sessions per project directory, so
    the follow-up must use the same `cwd` as the run it continues.
    """

    async def stream():
        # --- 1. check the working directory ---------------------------------
        workdir = Path(cwd.strip()).expanduser()
        if not workdir.is_absolute():
            yield sse({"message": f"工作目录必须是绝对路径（可以用 ~ 开头）：{cwd}"}, event="run_error")
            yield sse({"exit_code": None, "stderr": ""}, event="end")
            return
        if not workdir.is_dir():
            reason = "不是目录" if workdir.exists() else "不存在"
            yield sse({"message": f"工作目录{reason}：{workdir}"}, event="run_error")
            yield sse({"exit_code": None, "stderr": ""}, event="end")
            return

        # --- 2. start the process -------------------------------------------
        claude_path = shutil.which(CLAUDE_BIN)
        if claude_path is None:
            yield sse({"message": f"找不到 `{CLAUDE_BIN}` 命令。请确认 Claude Code 已安装并在 PATH 中。"}, event="run_error")
            yield sse({"exit_code": None, "stderr": ""}, event="end")
            return

        args = [claude_path, "-p", prompt]
        if resume:
            args += ["--resume", resume]
        args += [
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(workdir),
                env=claude_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE_BYTES,
            )
        except OSError as exc:
            yield sse({"message": f"无法启动 claude 进程：{exc}"}, event="run_error")
            yield sse({"exit_code": None, "stderr": ""}, event="end")
            return

        # Read stderr in the background so a chatty stderr can never block the process.
        stderr_chunks: list[bytes] = []

        async def drain_stderr():
            while chunk := await proc.stderr.read(65536):
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())

        # --- 3. forward stdout line by line, as soon as each line arrives -----
        try:
            line_no = 0
            while True:
                try:
                    raw = await proc.stdout.readline()
                except ValueError:
                    # A line longer than MAX_LINE_BYTES: skip it instead of dying.
                    line_no += 1
                    yield sse({"type": "_parse_error", "line": line_no, "raw": "（这一行太长，已跳过）"})
                    continue
                if not raw:
                    break  # stdout closed: the process is finishing
                line_no += 1
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    yield sse(parse_line(line, line_no))

            exit_code = await proc.wait()
            await stderr_task
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            yield sse({"exit_code": exit_code, "stderr": stderr[-STDERR_TAIL_CHARS:]}, event="end")
        finally:
            # Reached early if the browser disconnects (page closed, new run started).
            # Don't leave an orphaned agent running in the background.
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            stderr_task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


def resolve_sample(name: str) -> Path:
    """Map a sample file name to a path, refusing anything outside samples/."""
    path = (SAMPLES_DIR / name).resolve()
    if path.parent != SAMPLES_DIR or path.suffix != ".jsonl" or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Sample not found: {name}")
    return path


@app.get("/api/samples")
def list_samples() -> list[str]:
    """Names of the .jsonl files available for replay."""
    return sorted(p.name for p in SAMPLES_DIR.glob("*.jsonl"))


@app.get("/api/replay")
async def replay(
    file: str = Query(..., description="A .jsonl file name inside samples/"),
    delay: float = Query(REPLAY_DELAY_SECONDS, ge=0, le=5),
) -> StreamingResponse:
    """Stream a recorded event file to the browser as SSE."""
    path = resolve_sample(file)

    async def stream():
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                yield sse(parse_line(line, line_no))
                await asyncio.sleep(delay)
        yield sse({"exit_code": None, "stderr": ""}, event="end")

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
