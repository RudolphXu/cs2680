# Claude Code Web Frontend (CS2680 Assignment 1)

A local web page that drives Claude Code in headless mode and shows its JSON event stream live. See `SPEC.md`.

Current progress: steps 1–4 (replay mode, rendering, tree view and outline, live mode with failure handling, session resume).

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
- **Follow-ups**: after a live run ends, the box below the conversation says "继续会话 <session_id>". The next prompt runs with
  `--resume <session_id>` and is added below the earlier runs, marked as resumed. The working directory is locked during a
  conversation, because Claude Code stores sessions per directory. Click **开始新会话** to clear the page and start over.
- **Replay**: pick a file from `samples/` to replay it at about one event every 0.3 s. Free and instant; use it for UI work.
- **Outline (left)**: a flowchart of the tool calls, one coloured node per call (colour = kind of tool). Agent/Task calls sent
  in the same assistant message fork into side-by-side columns, one per subagent, and join back when all of them are done
  (dashed lines = still running). Folding a subagent in the dialogue folds its column too. Click a node to jump to that call.
  On a narrow window the outline becomes a drawer opened by the **目录** button.

## Files

- `server.py`: FastAPI backend. Serves the page; `/api/run` spawns `claude` and forwards each stdout line over SSE; `/api/replay` replays a sample file; `/api/samples` lists the sample files.
- `static/index.html`: the whole frontend (HTML + CSS + JavaScript).
- `samples/`: recorded event streams used by replay mode.
- `requirements.txt`: Python dependencies.
