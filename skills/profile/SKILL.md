---
name: profile
description: Profile a Claude Code session window — measures wall time, API time, tool time, token cost, and per-bucket breakdowns (docker pull/build, dataset/checkpoint download, benchmark run, test, etc.). Invoked as `/profile start|status|mark|stop|reset`. Use when the user wants to measure cost or time of a chunk of work in this session.
allowed-tools: Bash
argument-hint: start [name] [--tag k=v]... [--note "..."] | status | mark <label> | stop [--format=table|markdown|json] [--export DIR] | reset
---

# /profile — Claude Code session profiler

The user is invoking the profiler. Read the user's argument list, then run **exactly one** Bash command that calls `scripts/cc_profiler.py` with the matching subcommand. Print the script's output verbatim to the user — do not paraphrase or summarize the table; users want to see real numbers.

## Resolving the script path

The script ships next to this skill. Use this **single-line** resolver — it always produces a `.../scripts/cc_profiler.py` path, never a bare directory:

```bash
SCRIPT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}/scripts/cc_profiler.py"
```

`${CLAUDE_PLUGIN_ROOT:-…}` picks the plugin root when running as an installed plugin, falls back to `CLAUDE_PROJECT_DIR` (project-mode skill / repo clone), and finally to `$PWD`. The `/scripts/cc_profiler.py` suffix is unconditional, so the resolved value is always a file path.

**Do not** invent fallbacks like `${SCRIPT:-$CLAUDE_PROJECT_DIR}` or `python3 "$CLAUDE_PROJECT_DIR" …` — those pass a directory to `python3` and produce `can't find '__main__' module in '…'`. If `$SCRIPT` doesn't exist, fail loudly instead of falling back.

## Subcommands

Always emit **one** Bash invocation in this exact shape (substitute the subcommand and args). The `[ -f … ]` guard prevents the directory-as-script bug:

```bash
SCRIPT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}/scripts/cc_profiler.py"
[ -f "$SCRIPT" ] || { echo "cc_profiler.py not found at $SCRIPT" >&2; exit 1; }
python3 "$SCRIPT" <subcommand> <args...>
```

Map the user's arguments to `<subcommand> <args...>`. Pass arguments through verbatim — do not interpret tags or notes yourself.

| User said | `<subcommand> <args...>` |
|---|---|
| `/profile start [name] [--tag k=v]... [--note "..."]` | `start [name] [--tag k=v]... [--note "..."]` |
| `/profile status` | `status` |
| `/profile mark <label>` | `mark <label>` |
| `/profile stop [--format=...] [--export DIR]` | `stop [--format=...] [--export DIR]` |
| `/profile reset` | `reset` |
| `/profile` (no args) | `status` (treat as status) |

If the subcommand is unrecognized, replace the last line with `python3 "$SCRIPT" --help` and show the usage.

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
