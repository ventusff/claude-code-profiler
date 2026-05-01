---
name: profile
description: Profile a Claude Code session window — measures wall time, API time, tool time, token cost, and per-bucket breakdowns (docker pull/build, dataset/checkpoint download, benchmark run, test, etc.). Invoked as `/profile start|status|mark|stop|reset`. Use when the user wants to measure cost or time of a chunk of work in this session.
allowed-tools: Bash
argument-hint: start [name] [--tag k=v]... [--note "..."] | status | mark <label> | stop [--format=table|markdown|json] [--export DIR] | reset
---

# /profile — Claude Code session profiler

The user is invoking the profiler. Read the user's argument list, then run **exactly one** Bash command that calls `scripts/cc_profiler.py` with the matching subcommand. Print the script's output verbatim to the user — do not paraphrase or summarize the table; users want to see real numbers.

## Resolving the script path

**Important — read this carefully, do not improvise.**

`${CLAUDE_PLUGIN_ROOT}` is **not reliably injected** into the bash commands you emit from this skill. We learned this the hard way: writing `${CLAUDE_PLUGIN_ROOT:-…}` collapses to the fallback because the variable is empty at bash-execution time, and the fallback then mis-resolves to a directory. Do **not** reference `CLAUDE_PLUGIN_ROOT` here.

Instead, locate the script by globbing the plugin cache (where Claude Code installs the plugin) and falling back to the dev repo. Use this exact 3-line block — copy it verbatim, do not rewrite:

```bash
SCRIPT="$(ls -1d ~/.claude/plugins/cache/*/claude-code-profiler/*/scripts/cc_profiler.py 2>/dev/null | sort -V | tail -1)"
[ -n "$SCRIPT" ] || SCRIPT="${CLAUDE_PROJECT_DIR:-$PWD}/scripts/cc_profiler.py"
[ -f "$SCRIPT" ] || { echo "cc_profiler.py not found at '$SCRIPT'. Install the plugin or run from the repo." >&2; exit 1; }
```

How it resolves:
1. **Plugin install (preferred)** — glob picks the highest-versioned `cc_profiler.py` under `~/.claude/plugins/cache/<marketplace>/claude-code-profiler/<version>/scripts/`. `sort -V | tail -1` ensures we always run the newest installed version, ignoring stale older versions kept around during the update grace window.
2. **Dev mode fallback** — if no plugin install is found, use `$CLAUDE_PROJECT_DIR/scripts/cc_profiler.py` (or `$PWD` if neither var is set). This is for working directly inside a clone of the source repo.
3. **Fail-fast** — if neither path resolves to a real file, exit 1 with a clear message. **Never** fall through to `python3 "$CLAUDE_PROJECT_DIR" …` or any other variable that holds a directory; that produces `can't find '__main__' module in '…'`.

## Subcommands

Always emit **one** Bash invocation in this exact shape — the resolver block followed by the python call. Do not edit the resolver:

```bash
SCRIPT="$(ls -1d ~/.claude/plugins/cache/*/claude-code-profiler/*/scripts/cc_profiler.py 2>/dev/null | sort -V | tail -1)"
[ -n "$SCRIPT" ] || SCRIPT="${CLAUDE_PROJECT_DIR:-$PWD}/scripts/cc_profiler.py"
[ -f "$SCRIPT" ] || { echo "cc_profiler.py not found at '$SCRIPT'. Install the plugin or run from the repo." >&2; exit 1; }
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
