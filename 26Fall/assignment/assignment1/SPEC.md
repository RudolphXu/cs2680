# SPEC: Web Frontend for Claude Code (CS2680 Assignment 1)

## 1. Goal

Build a local web page that drives Claude Code. The user types a prompt and picks a working directory. The server runs Claude Code in headless mode against that directory. The page shows what the agent is doing, live, while it works.

The page is a renderer for Claude Code's JSON event stream. It must not implement its own agent loop.

## 2. Architecture

```
Browser (index.html)  <-- SSE --  Python server (FastAPI)  -- subprocess -->  claude -p ...
  - prompt box                      - spawns claude                             - prints one JSON
  - renders events                  - reads stdout line by line                   event per line
                                    - forwards each event at once
                                    - keeps the session_id
```

- Backend: Python, FastAPI, Server-Sent Events (SSE).
- Frontend: a single `index.html` with inline CSS and JavaScript. No React or other frameworks. Small libraries loaded from a CDN are fine (for example, a markdown renderer).
- Dependencies managed with a venv and `requirements.txt`.
- Runs on localhost only.

## 3. Hard constraints

- Do NOT use the Claude Agent SDK.
- Do NOT use or set `ANTHROPIC_API_KEY`. The `claude` CLI uses the existing login.
- Do NOT use an agent framework or reimplement the agent loop. The server only spawns the `claude` command and relays its output.
- The agent's working directory is chosen by the user and is separate from this repository (for example, `~/projects/cs2680-sandbox/practice_asign1`).

## 4. How the server runs Claude Code

New run:

```
claude -p "<prompt>" --output-format stream-json --verbose --dangerously-skip-permissions
```

Follow-up run (resume the same conversation):

```
claude -p "<prompt>" --resume <session_id> --output-format stream-json --verbose --dangerously-skip-permissions
```

- Spawn with `cwd` set to the chosen working directory.
- Read stdout line by line. Parse each line as JSON and forward it to the browser immediately. Do not wait for the run to end.
- Capture stderr and the exit code. Use them to report failures.

## 5. Event stream format

Each line of stdout is one JSON object. Real examples are in `samples/`:

- `samples/events_hello.jsonl`: minimal run, no tools.
- `samples/events.jsonl`: bug-fix run with tool calls.
- `samples/events_subagent.jsonl`: run that spawns a subagent.

Event types that matter:

| Event | Key fields | Meaning |
|---|---|---|
| `type: "system"`, `subtype: "init"` | `session_id`, `cwd`, `model` | Run started |
| `type: "assistant"` | `message.content[]` | Content blocks from the model |
| content block `type: "text"` | `text` | Assistant text, usually markdown |
| content block `type: "tool_use"` | `id`, `name`, `input` | A tool call. Pending until its result arrives |
| `type: "user"` | `message.content[]` | Messages sent back to the model |
| content block `type: "tool_result"` | `tool_use_id`, `content`, `is_error` | Result of the tool call with matching id |
| `type: "result"` | `subtype`, `is_error`, `total_cost_usd`, `duration_ms`, `num_turns`, `session_id`, `result` | Run finished |
| any event | `parent_tool_use_id` | `null` for the main agent; otherwise the id of the tool call that spawned the subagent |

Notes:

- `tool_result.content` may be a string or a list of blocks. Handle both.
- Unknown event types (for example, `rate_limit_event`) must not break the page. Ignore them or show them quietly.
- A run has failed if: the process cannot start (for example, the directory does not exist), the process exits with a non-zero code without a `result` event, or the `result` event has `is_error: true` or a `subtype` other than `success`.

## 6. Required features

1. **Submit a prompt.** A text box for the prompt and a field for the working directory. A Run button starts a run. After a run finishes, another run can be started from the same page.

2. **Show the trajectory live.** Render each event as it arrives. Show assistant text rendered as markdown. Show every tool call with its name and its key input (file path for Read/Edit/Write, command for Bash, pattern for Grep/Glob, description for Task).

3. **Show tool results.** Attach each `tool_result` to its tool call using `tool_use_id`. Long results are cut down: show the first several lines and fold the rest behind a "N more lines" toggle.

4. **Show status at two levels.**
   - Per tool call: pending from `tool_use` until the matching `tool_result`; then success or error (`is_error: true` shown clearly as an error).
   - Per run: in progress, finished, or failed. A failed run (for example, a bad directory) shows a visible error message and never leaves the page waiting forever.

5. **Continue the conversation.** After a run finishes, a follow-up prompt resumes the same session using `--resume <session_id>`. The page shows that the session was resumed.

6. **Show the run's numbers.** When a run finishes, show cost in dollars, wall-clock duration, and number of turns, taken from the `result` event. Also show the session id.

7. **Show the trajectory as a tree.**
   - Nest subagent events under the tool call that spawned them (match `parent_tool_use_id` to the tool call `id`). Subagent sections are collapsible.
   - A run with no subagents still renders as a flat list.
   - Show an outline beside the dialogue that lists only the tool call names, like a table of contents. Clicking an entry scrolls to that call.
   - On a wide window the outline sits beside the dialogue (not as a strip fixed at the top). On a narrow window it may fold away.

## 7. Replay mode (for development)

The server has a replay mode that reads a `.jsonl` file from `samples/` instead of spawning `claude`. It sends one line about every 0.3 seconds to imitate a live run. The page should let the user choose live mode or replay mode, and pick the sample file in replay mode.

Most rendering work should be developed and tested in replay mode, because it is instant and free. Live runs are needed only to test true streaming, failure handling, and session resume.

## 8. Suggested file layout

```
assignment1/
├── SPEC.md
├── README.md          how to set up and start the server
├── requirements.txt
├── server.py          FastAPI app: live mode, replay mode, SSE endpoint
├── static/
│   └── index.html     the whole frontend
└── samples/           recorded event streams
```

## 9. Build order

1. Replay mode plus basic rendering: features 2, 3, 4 (per tool call), 6.
2. Tree view and outline: feature 7, tested with `events_subagent.jsonl`.
3. Live mode: feature 1, and feature 4 (per run, including failures).
4. Session resume: feature 5.
5. Use the page, find what is annoying, and improve it.

## 10. Optional extras (not required)

- A stop button that cancels a running agent.
- A diff view of files the agent changed.
- Several sessions in parallel tabs.
