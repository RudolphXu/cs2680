"""Web frontend for Claude Code — backend.

Step 1 (SPEC §9): replay mode only. The server reads a recorded .jsonl file
from samples/ and sends one event about every 0.3 s over Server-Sent Events
(SSE), imitating a live `claude -p ... --output-format stream-json` run.

Live mode (spawning the real `claude` process) comes in a later step.

Start with:  uvicorn server:app --reload --port 8000
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "samples"
STATIC_DIR = BASE_DIR / "static"

REPLAY_DELAY_SECONDS = 0.3

app = FastAPI(title="Claude Code Web Frontend")


def sse(data: dict, event: str | None = None) -> str:
    """Format one Server-Sent Event message.

    `json.dumps` never emits raw newlines, so the payload fits on one
    `data:` line, as SSE requires.
    """
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


def resolve_sample(name: str) -> Path:
    """Map a sample file name to a path, refusing anything outside samples/."""
    path = (SAMPLES_DIR / name).resolve()
    if path.parent != SAMPLES_DIR or path.suffix != ".jsonl" or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Sample not found: {name}")
    return path


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/samples")
def list_samples() -> list[str]:
    """Names of the .jsonl files available for replay."""
    return sorted(p.name for p in SAMPLES_DIR.glob("*.jsonl"))


@app.get("/api/replay")
async def replay(
    file: str = Query(..., description="A .jsonl file name inside samples/"),
    delay: float = Query(REPLAY_DELAY_SECONDS, ge=0, le=5),
) -> StreamingResponse:
    """Stream a recorded event file to the browser as SSE.

    Each line of the file becomes one `data:` message. After the last line
    the server sends an `end` event so the page knows the stream is over
    (otherwise the browser's EventSource would try to reconnect).
    """
    path = resolve_sample(file)

    async def stream():
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Never break the stream on a bad line; tell the page instead.
                    event = {"type": "_parse_error", "line": line_no, "raw": line[:2000]}
                yield sse(event)
                await asyncio.sleep(delay)
        yield sse({"reason": "replay finished"}, event="end")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
