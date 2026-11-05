# Claude Code Web Frontend (CS2680 Assignment 1)

A local web page that drives Claude Code in headless mode and shows its JSON event stream live. See `SPEC.md`.

Current progress: steps 1–3 (replay mode, rendering, tree view and outline, live mode with failure handling). Session resume (step 4) is not built yet.

## Requirements

- Python 3.10+
- Claude Code installed and logged in (`claude` on your PATH). No `ANTHROPIC_API_KEY` is needed or used; the server even removes it from the environment it gives `claude`.

## Setup (once)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Start

```bash
.venv/bin/uvicorn server:app --reload --port 8000
```

Open http://127.0.0.1:8000 . Stop the server with Ctrl+C.

- **Live**: type a prompt and an absolute working directory (`~` is fine), then click **Run** (or press ⌘/Ctrl+Enter). The server runs
  `claude -p "<prompt>" --output-format stream-json --verbose --dangerously-skip-permissions` in that directory.
  Because of `--dangerously-skip-permissions`, the agent can change files there without asking. Use a sandbox directory.
- **Replay**: pick a file from `samples/` to replay it at about one event every 0.3 s. Free and instant; use it for UI work.

## Files

- `server.py`: FastAPI backend. Serves the page; `/api/run` spawns `claude` and forwards each stdout line over SSE; `/api/replay` replays a sample file; `/api/samples` lists the sample files.
- `static/index.html`: the whole frontend (HTML + CSS + JavaScript).
- `samples/`: recorded event streams used by replay mode.
- `requirements.txt`: Python dependencies.
