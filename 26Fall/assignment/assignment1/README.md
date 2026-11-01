# Claude Code Web Frontend (CS2680 Assignment 1)

A local web page that shows Claude Code's JSON event stream. See `SPEC.md`.

Current progress: step 1 (replay mode plus basic rendering). Live mode is not built yet.

## Setup (once)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Start

```bash
.venv/bin/uvicorn server:app --reload --port 8000
```

Then open http://127.0.0.1:8000 , choose a sample file and click **Run**.
Stop the server with Ctrl+C.

## Files

- `server.py`: FastAPI backend. Serves the page, lists `samples/*.jsonl`, and replays a file over SSE (one line every 0.3 s).
- `static/index.html`: the whole frontend (HTML + CSS + JavaScript).
- `samples/`: recorded event streams used by replay mode.
- `requirements.txt`: Python dependencies.
