---
name: profile
description: Profile a Claude Code session window — measures wall time, API time, tool time, token cost, and per-bucket breakdowns (docker pull/build, dataset/checkpoint download, benchmark run, test, etc.). Invoked as `/profile start|status|mark|stop|reset`. Use when the user wants to measure cost or time of a chunk of work in this session.
allowed-tools: Bash
argument-hint: start [name] [--tag k=v]... [--note "..."] | status | mark <label> | stop [--format=table|markdown|json] [--export DIR] | reset
---

# /profile — Claude Code session profiler

The user is invoking the profiler. Read the user's argument list, then run **exactly one** Bash command that calls `scripts/cc_profiler.py` with the matching subcommand. Print the script's output verbatim to the user — do not paraphrase or summarize the table; users want to see real numbers.

## Resolving the script path

The script ships next to this skill inside the plugin. Pick the first existing path:

1. Plugin install (preferred): `${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py`
2. Project-mode dev (this repo cloned and used via `--plugin-dir .`, or as a project skill): `${CLAUDE_PROJECT_DIR}/scripts/cc_profiler.py`
3. Installed as a binary on PATH: `claude-code-profiler`

Robust one-liner:

```bash
SCRIPT="${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py}"
[ -n "$SCRIPT" ] && [ -f "$SCRIPT" ] || SCRIPT="${CLAUDE_PROJECT_DIR:-$PWD}/scripts/cc_profiler.py"
[ -f "$SCRIPT" ] || SCRIPT="$(command -v claude-code-profiler || true)"
```

## Subcommands

Map the user's arguments to the script as follows. Pass arguments through verbatim — do not interpret tags or notes yourself.

| User said | You run |
|---|---|
| `/profile start [name] [--tag k=v]... [--note "..."]` | `python3 SCRIPT start [name] [--tag k=v]... [--note "..."]` |
| `/profile status` | `python3 SCRIPT status` |
| `/profile mark <label>` | `python3 SCRIPT mark <label>` |
| `/profile stop [--format=...] [--export DIR]` | `python3 SCRIPT stop [--format=...] [--export DIR]` |
| `/profile reset` | `python3 SCRIPT reset` |
| `/profile` (no args) | `python3 SCRIPT status` (treat as status) |

If the subcommand is unrecognized, run `python3 SCRIPT --help` and show the usage.

## What the profiler does

- **start** captures: session id, transcript path, cwd, git sha + dirty status, env (CLAUDE_*/ANTHROPIC_*/OTEL_*), model from latest assistant turn. Refuses if a window is already active.
- **status** shows elapsed time, marks, and a live count of turns since start.
- **mark** appends a `(timestamp, label)` to the active window — useful for "docker pull starts here" or "benchmark begins" boundaries.
- **stop** scans the transcript JSONL between start and now, computes all metrics, prints a table (or json/markdown), and writes artifacts under `~/.local/state/claude-code-profiler/windows/<window_id>/`:
  - `profile.json` — full structured report
  - `profile.md` — readable markdown
  - `events.jsonl` — normalized event stream
  - `transcript.snippet.jsonl` — raw rows in window (for replay)
  - `state.json` — frozen start-time metadata
- **reset** discards the active window pointer without producing a report.

## Notes for you (the assistant)

- Costs are **estimated** from a static price table (see top of `cc_profiler.py`). Always pass that disclaimer through to the user.
- Per-tool times are **approximate** in transcript-only mode (parallel tool bundles are split evenly across tools). The profile.json marks this with `tool_time_by_tool_approx: true`. If a user asks for exact per-tool timing, mention they can wire hooks; the profiler ingests `events.from_hooks.jsonl` automatically.
- After running the script, do not add your own commentary unless the user asked a question — let the table speak.
- If the user invokes `/profile stop` and the window has been short (<5s) or has zero turns, still print whatever the script outputs; that's a real signal the user can act on.

## Optional: hook-augmented timing

For exact per-tool start/end times, the user can wire these hooks in `.claude/settings.json` (each pipes the Claude-emitted JSON payload to the profiler's `_hook` subcommand, which is inert when no window is active). Use `${CLAUDE_PLUGIN_ROOT}` when this plugin is installed; swap to `${CLAUDE_PROJECT_DIR}` if running from a clone:

```json
{
  "hooks": {
    "PreToolUse":  [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PreToolUse"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PostToolUse"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook UserPromptSubmit"}]}],
    "Stop":        [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook Stop"}]}],
    "SessionEnd":  [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook SessionEnd"}]}]
  }
}
```

Don't add these unprompted — only suggest them if the user asks for finer-grained tool timing or asks how to extend the profiler.
