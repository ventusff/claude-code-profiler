# claude-code-profiler

**English** | [中文](README_cn.md)

> Slice a window out of a Claude Code session and measure wall time, API time, tool time, token cost, and per-bucket breakdowns (docker pull / build, dataset & checkpoint downloads, benchmark runs, tests, …).

`/profile start` opens a window, do work, `/profile stop` prints the report. Single-file Python, standard library only — no OpenTelemetry, no daemon, no Claude Code config changes required.

Useful when you want to:
- Pin down where a debug session or benchmark actually spent its time (API? tools? docker pull?)
- Know how many tokens a chunk of conversation burned and the estimated USD cost at current prices
- Drop "this run took X minutes, $Y, of which docker pull was N seconds" into an issue / PR / weekly report

---

## Usage — three lines, that's it

```
/profile start             # open a profiling window
…talk with Claude as you normally would: drive a benchmark, debug, run tools, whatever…
/profile stop              # close the window and print the report
```

That's the whole workflow. **No extra prompting mid-session, no special syntax to sprinkle into your messages, no "remember to log this".** On `stop`, the profiler scans the session transcript and aggregates everything that happened between `start` and `stop` into the table below. You can `start → stop → start → stop …` as many times as you like in one session — each is an independent window.

---

## What it computes

A typical `/profile stop` / `profile status` prints something like:

```
 ╭─ profile: run [60943b15]  51m34s wall
 ├─ time
 │  wall                  51m34s
 │  api time              15m21s
 │  tool time sum         13m24s
 │  tool time wall        13m24s  (critical path; sum > wall =
  parallel)
 │  user-thinking          30.1s
 │  idle/wait             22m12s
 ├─ api time by model
 │  claude-opus-4-7                  10m22s
 │  claude-sonnet-4-6                 4m59s
 │  (main)                            28.6s
 │  (subagent)                       14m53s
 ├─ tool time by bucket
 │  bash                              5m47s
 │  benchmark_run                     4m26s
 │  coding                            2m12s
 │  test                              58.0s
 │  mcp                                0.4s
 │  checkpoint_dl                      0.0s
 │  agent_dispatch                     0.0s
 ├─ top tools  (exact = solo bundle, ~ = N-way parallel split)
 │  Bash                 11m12s  n=85  ok=79  fail=6 (~17/85)
 │  Write                 1m35s  n=10  ok=9  fail=1
 │  Edit                  20.3s  n=9  ok=9  fail=0
 │  Read                  16.6s  n=38  ok=38  fail=0 (~11/38)
 │  mcp__plugin_nautilus_nautilus__lookup_benchmark     0.2s
 n=1  ok=1  fail=0 (~1/1)
 ├─ tokens
 │  input                    147
 │  output                 26.4k
 │  cache write           201.1k  (5m=188.3k, 1h=12.9k)
 │  cache read             8.37M  (hit_ratio=0.98)
 │  thinking blocks            3  (0 tok est)
 │  tool result (est)      69.5k
 ├─ tokens / cost by source
 │  subagent   $ 13.4388  in=137  out=22.9k  cw=188.3k
 │  mcp__plugin_nautilus_nautilus__lookup_benchmark     0.2s  n=1  ok=1  fail=0 (~1/1)
 ├─ tokens
 │  input                    147
 │  output                 26.4k
 │  cache write           201.1k  (5m=188.3k, 1h=12.9k)
 │  cache read             8.37M  (hit_ratio=0.98)
 │  thinking blocks            3  (0 tok est)
 │  tool result (est)      69.5k
 ├─ tokens / cost by source
 │  subagent   $ 13.4388  in=137  out=22.9k  cw=188.3k  cr=8.20M  calls=130
 │  main       $  1.0098  in=10  out=3.6k  cw=12.9k  cr=173.2k  calls=5
 ├─ tokens / cost by model
 │  claude-opus-4-7              $ 13.4222  in=96  out=20.8k  calls=86
 │  claude-sonnet-4-6            $  1.0264  in=51  out=5.6k  calls=49
 ├─ subagents (2 files)
 │  nautilus:policy-generator    $ 12.4124  in=86  out=17.2k  agents=1  calls=81
 │  nautilus:env-generator       $  1.0264  in=51  out=5.6k  agents=1  calls=49
 ├─ cost
 │  estimated         $  14.4486  (not billing truth)
 ├─ turns / errors
 │  assistant turns          135
 │    debug (w/tool)         131
 │    sidechain              130
 │  user prompts               6
 │  tool calls               146
 │  subagent calls             2  (2 agent files)
 │  api errors                 0
 │  retries                    0
 │  compactions                0
 │  stop reasons     tool_use=60, end_turn=4
 │  lines +885 / -39
 ╰─ artifacts under: /home/ventus/.local/state/claude-code-profiler/windows/20260503T112904Z__run__5162f
```

The same data is also available as `--format markdown` (paste-ready into an issue) or `--format json` (full structured fields).

---

## Install (recommended: as a Claude Code plugin)

This repo is a valid Claude Code plugin **and** a single-plugin marketplace. Three steps and `/profile` is available in **every** Claude Code session on your machine.

### Step 1 — Register this repo as a marketplace

```bash
# Option A: install straight from GitHub (once the repo is public)
claude plugin marketplace add https://github.com/ventusff/claude-code-profiler

# Option B: clone first, then register the local path
git clone https://github.com/ventusff/claude-code-profiler.git ~/src/claude-code-profiler
claude plugin marketplace add ~/src/claude-code-profiler
```

`claude plugin marketplace add` accepts a GitHub URL, a git URL, or a local path. It reads `.claude-plugin/marketplace.json` and registers this repo as a marketplace named **`claude-code-profiler`**.

### Step 2 — Install the plugin from that marketplace

```bash
claude plugin install claude-code-profiler@claude-code-profiler
```

The format is `<plugin-name>@<marketplace-name>`. The plugin name comes from `.claude-plugin/plugin.json`'s `name`, and the marketplace name from `.claude-plugin/marketplace.json`'s `name`. They both happen to be `claude-code-profiler` here.

The default install scope is `user` (`~/.claude/plugins/`), so **`/profile` works in any working directory you launch Claude Code from**. Override with `-s project` or `-s local` if you want it scoped tighter.

### Step 3 — Verify

```bash
claude plugin list
```

`claude-code-profiler` should appear. Open a fresh Claude Code session and type `/profile` — you should see `claude-code-profiler: no active window.`.

Day-to-day usage:

```
/profile start bench-run --tag run=1 --note "robocasa eval"
…do work…
/profile stop
```

### Update / uninstall

```bash
claude plugin update claude-code-profiler
claude plugin marketplace update claude-code-profiler   # pull latest commits from the source
claude plugin uninstall claude-code-profiler
```

### Load only for the current session (development)

If you're hacking on this repo and don't want to install it permanently:

```bash
cd /any/project
claude --plugin-dir /path/to/claude-code-profiler
```

Scoped to that one session; nothing is added to your global plugin list.

---

## Fallback: run the Python script directly

If you don't want to use the plugin mechanism — CI environments, machines without Claude Code installed, or you just want to sanity-check the numbers — `scripts/cc_profiler.py` is a single-file, stdlib-only Python 3.10+ script:

```bash
git clone https://github.com/ventusff/claude-code-profiler.git
cd claude-code-profiler

python3 scripts/cc_profiler.py start bench-run --tag run=1 --note "robocasa eval"
# … do work in the same Claude Code session on the same machine …
python3 scripts/cc_profiler.py stop --format table
```

Caveat: the script auto-discovers the active session's transcript path from the environment, so `start` and `stop` must run **on the same machine, from the same Claude Code session's cwd** (or with an explicit `--session …`). You lose the `/profile` slash-command ergonomics but every metric is identical.

---

## Subcommands

| Command | Behavior |
|---|---|
| `start [name] [--tag k=v]... [--note "..."]` | Open a window. Captures `session_id`, transcript path, cwd, git sha + dirty status, `CLAUDE_*/ANTHROPIC_*/OTEL_*` env snapshot, and the current model. Refuses if a window is already active. |
| `status` | Show how long the active window has been open and how many turns it has accumulated. |
| `mark <label>` | Append a timestamped label to the active window ("docker pull starts here", "benchmark begins"). Marks are emitted in the final report. |
| `stop [--format=table\|markdown\|json] [--export DIR]` | Scan the transcript over `[start_ts, now]`, compute metrics, render the report, persist artifacts, and clear the active pointer. |
| `reset` | Discard the active window without producing a report. |

Only one active window per machine at a time (deliberate v0 constraint; concurrent windows land in v1). Within one session you can `start → stop → start → stop …`; each is an independent window.

---

## How it works

Claude Code writes every session to `~/.claude/projects/<slug>/<session_id>.jsonl`. On `start`, `cc_profiler.py` records the session and the start timestamp; on `stop`, it scans the JSONL rows in that interval and aggregates by event type:

- **API time** = gap from the last `user`/tool_result row to the next `assistant` row, bucketed by the turn's `message.model`.
- **Tool time** = wall-clock from the parent `assistant` message to the last `tool_result` of the batch ("bundle wall"), classified by tool name + command (Bash matches regexes into `docker_pull / docker_build / dataset_dl / checkpoint_dl / benchmark_run / test / infra / download / bash`).
- **Tokens & cost** = each assistant turn's `usage` × the price for the turn's actual `message.model`. Mixed-model runs are billed per-turn, not averaged across the window.
- **Compaction, retry, API error, subagent, idle/wait** are detected from transcript markers.

Full metric definitions live in `notes/plan.md` under "Metric definitions".

### Precision limit on parallel tool calls

When an assistant message dispatches N `tool_use` blocks at once (e.g. several parallel `Read`s), the Anthropic protocol requires all `tool_result`s to come back together — so they **share a single `user` row's timestamp** in the transcript. Therefore:

- **N=1** (solo tool): per-tool time = the bundle's wall time. **Exact.**
- **N>1** (parallel bundle): per-tool time can only be approximated as `bundle_wall / N`. The profile marks these with `~` and counts them in `tool_approx_count_by_tool`.

To recover **real** per-tool start/end times for parallel bundles you need the PreToolUse / PostToolUse hooks (see below).

---

## Optional: hook-augmented per-tool timing

Add this to `.claude/settings.json` (project-level or user-level) and the profiler will ingest hook events alongside the transcript, overriding the approximations. Use `${CLAUDE_PLUGIN_ROOT}` if you installed via the plugin flow:

```json
{
  "hooks": {
    "PreToolUse":       [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PreToolUse"}]}],
    "PostToolUse":      [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PostToolUse"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook UserPromptSubmit"}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook Stop"}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook SessionEnd"}]}]
  }
}
```

If you're running from a clone instead of a plugin install, swap `${CLAUDE_PLUGIN_ROOT}` for `${CLAUDE_PROJECT_DIR}` or an absolute path.

Each hook receives the JSON payload Claude pushes and appends a line to the active window's `events.from_hooks.jsonl`. **When no window is active, every hook exits silently** — they never interfere with normal sessions.

---

## Output artifacts

Each `stop` writes to `$XDG_STATE_HOME/claude-code-profiler/windows/<window_id>/` (default: `~/.local/state/claude-code-profiler/windows/<window_id>/`):

```
profile.json              # full structured report — all metrics + per-turn breakdown
profile.md                # paste-ready Markdown report for issues / PRs
events.jsonl              # normalized event stream (turn_start / tool_start / mark / compaction / error / …)
transcript.snippet.jsonl  # raw transcript rows from the window (for replay / re-computation)
state.json                # start-time metadata frozen at `start` (git sha, env snapshot, start ts)
```

`stop --export DIR` additionally copies the bundle to `DIR/<window_id>/` so you can archive it next to a benchmark run directory.

---

## Price table

A static 2026-04 USD/million-token price table ships with the script, covering Opus 4 / 4.7, Sonnet 4 / 4.5 / 4.6, and Haiku 3.5 / 4.5. Matching is `model.startswith(prefix)`; unknown models report cost as `null` and emit a warning.

```
claude-opus-4-7  : (input 15.00, output 75.00, cache_write_5m 18.75, cache_read 1.50)
claude-sonnet-4-6: ( 3.00, 15.00, 3.75, 0.30)
claude-haiku-4-5 : ( 1.00,  5.00, 1.25, 0.10)
…
```

Override at call time with `stop --prices "opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3"`. 1h cache writes are priced at 2× the 5m rate.

> ⚠ This is an **estimate**, not Anthropic's billing source of truth. Fine for "which chunk burned the most / how do these models compare", not as the basis for an invoice.

---

## Known limitations

- **No OTel** (deliberately scoped out of v0). If you already run Claude Code with OpenTelemetry, the profiler ignores it; v1 will read OTel.
- **Time outside the transcript isn't counted**: a 5-minute coffee break gets bucketed as `idle/wait` (any user-side gap above the 300s default threshold).
- **Per-tool time for parallel bundles is approximate** in transcript-only mode (see above). Wire up hooks for exact numbers.
- **One active window at a time** per machine.

---

## Repo layout

```
claude-code-profiler/
├── .claude-plugin/
│   ├── plugin.json             # Claude Code plugin manifest
│   └── marketplace.json        # single-plugin marketplace manifest
├── skills/
│   └── profile/
│       └── SKILL.md            # /profile dispatcher
├── scripts/
│   └── cc_profiler.py          # single-file implementation, stdlib only
├── notes/                      # design / decision records (gitignored)
│   └── plan.md
├── README.md                   # English
└── README_cn.md                # 中文
```

---

## Roadmap

- **v0** (shipped): transcript-only, single window, five subcommands, table / markdown / json output, distributed as a proper Claude Code plugin.
- **v1**: hook-augmented per-tool timing, optional OTel ingestion, concurrent windows, `update-config` one-shot to wire hooks.
- **v2**: batch run launcher (ported from SWE-Skills-Bench-dev) + cross-run aggregation (CSV / JSON).
- **v3**: Web/HTML report (if anyone asks).
