# claude-code-profiler

**English** | [中文](README_cn.md)

> Profile a slice of a Claude Code session and measure wall time, API time, tool time, token cost, and per-bucket breakdowns (docker pull / build, dataset & checkpoint downloads, benchmark runs, tests, …).

`/profile start` begins a profile, do work, `/profile stop` prints the report. Single-file Python, standard library only — no OpenTelemetry, no daemon, no Claude Code config changes required.

Useful when you want to:
- Pin down where a debug session or benchmark actually spent its time (API? tools? docker pull?)
- Know how many tokens a chunk of conversation burned and the estimated USD cost at current prices
- Drop "this run took X minutes, $Y, of which docker pull was N seconds" into an issue / PR / weekly report

---

## Usage — three lines, that's it

```
/profile start             # begin a profile
…talk with Claude as you normally would: drive a benchmark, debug, run tools, whatever…
/profile stop              # end the profile and print the report
```

That's the whole workflow. **No extra prompting mid-session, no special syntax to sprinkle into your messages, no "remember to log this".** On `stop`, the profiler scans the session transcript and aggregates everything that happened between `start` and `stop` into the table below. You can `start → stop → start → stop …` as many times as you like in one session — each is an independent profile.

Throughout this README, **profile** (the noun) means one such start→stop measurement. We're using the standard term in profiler tooling — cProfile, Go pprof, and Linux `perf` all call the captured measurement period a "profile" — so the name lines up with the slash command (`/profile`), the tool (`claude-code-profiler`), and the artifacts it produces (`profile.json`, `profile.md`).

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

`claude-code-profiler` should appear. Open a fresh Claude Code session and type `/profile` — you should see `claude-code-profiler: no active profile.`.

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
| `start [name] [--tag k=v]... [--note "..."]` | Begin a profile. Captures `session_id`, transcript path, cwd, git sha + dirty status, `CLAUDE_*/ANTHROPIC_*/OTEL_*` env snapshot, and the current model. Refuses if a profile is already active in this session. |
| `status` | Show how long the active profile has been running and how many turns it has accumulated. |
| `mark <label>` | Append a timestamped label to the active profile ("docker pull starts here", "benchmark begins"). Marks are emitted in the final report. |
| `stop [--format=table\|markdown\|json] [--export DIR]` | Scan the transcript over `[start_ts, now]`, compute metrics, render the report, persist artifacts, and clear the active pointer. |
| `reset` | Discard the active profile without producing a report. |

One active profile per Claude Code session. The active-profile pointer is keyed by session — concurrent Claude Code sessions on the same host each get their own and don't interfere (regression-tested in `tests/test_concurrent_sessions.py`). Within one session, `start → stop → start → stop …` is fine; two simultaneous profiles in the same session are not.

---

## How it works

### Where Claude Code stores a session

Claude Code persists every session as JSONL under `~/.claude/projects/<slug>/`:

```
<slug>/
├── <session_id>.jsonl              # parent thread (isSidechain: false)
└── <session_id>/
    └── subagents/
        ├── agent-<aid>.jsonl       # one file per Task-tool dispatch
        ├── agent-<aid>.meta.json   #   carries `agentType`, `description`
        └── …
```

Subagents are **never inlined into the parent file** — they live in their own JSONL with `isSidechain: true` rows. A parent-only scan (the obvious thing to write, and what most homemade analyzers do) silently skips the entire subagent body. On a real session of ours, the parent file alone reported \$5.93; parent + 3 subagent files together cost \$14.27, a 2.4× undercount. See `notes/subagent-coverage.md`.

`/profile stop` walks the parent **and every sibling subagent file**, sorts the union by timestamp, and aggregates the rows into the metrics below.

### What gets aggregated

- **Tokens & cost** — each assistant row's `usage` block, priced against that turn's actual `message.model`. Mixed-model runs (Opus + Sonnet, or main + Haiku-using subagents) are billed per turn, not averaged. Per-source / per-model / per-`agentType` splits sum to the total exactly.
- **API time** — gap from the previous user / tool_result / compaction row to the next assistant row. Partitioned into `main` and `subagent` buckets by source file, plus a per-`message.model` slice.
- **Tool time** — wall-clock from a parent assistant message to the last `tool_result` of its batch ("bundle wall"). Classified by tool name; Bash commands are regex-classified into `docker_pull` / `docker_build` / `dataset_dl` / `checkpoint_dl` / `benchmark_run` / `test` / `infra` / `download` / `bash`. Same `(main, subagent)` split as API time.
- **Cache** — `cache_creation_input_tokens` reported in total **and** split into 5m vs 1h TTL from `usage.cache_creation`, plus a `cache_hit_ratio = cache_read / (input + cache_creation + cache_read)` so you can see at a glance whether prompt caching is paying for itself.
- **Other counters** — `thinking` blocks (extended-thinking output), Anthropic-side `web_search` / `web_fetch` from `usage.server_tool_use`, `stop_reason` distribution, retries (duplicate `requestId`), API errors (`stop_reason ∈ {error, refusal}`), compactions (`type: summary` / `subtype: compact*`), and edit-line deltas inferred from Edit / Write / MultiEdit input.

### Two correctness rules that keep the totals honest

**Dedup by `(source, agent_id, message.id)`.** Anthropic's SDK [explicitly documents](https://code.claude.com/docs/en/agent-sdk/cost-tracking) that one logical assistant turn can be streamed as several JSONL rows that share a single `message.id` and an identical `usage` block (one row per content block — text, thinking, parallel tool_uses). Summing per-row inflates tokens 2-6× on real sessions. The profiler keys its merge on `(source_file, agent_id, message.id)` so the dedup also survives any cross-file collision.

**Suppress the parent's Agent / Skill / Task bundle wall.** The parent's bundle wall on a Task tool_use equals the *entire* subagent runtime as observed from the outside. The subagent file's own per-turn `api_time` plus per-bundle tool times already cover that interval — counting both would double the time. When subagent files are present, the parent's Agent bundle is recorded for COUNT purposes (so `subagent calls` is right) but its time contribution is zeroed. This applies recursively: nested subagents land in the same flat `subagents/` dir and get the same treatment.

Tokens are not subject to the time double-count risk: every transcript row is one independent API call with its own `usage`, so summing across files is exact addition.

### Reading the report

A few fields aren't obvious from their labels:

- `tool time sum` vs `tool time wall` — `sum` adds bundle walls per-bucket; `wall` is the critical-path sum across distinct parent messages. They coincide in transcript-only mode (no per-tool starts/ends to discriminate); they diverge once hooks are wired.
- `(main)` / `(subagent)` rows under `api time by model` partition the model totals above by source file. The model lines and the source lines slice the same total; they don't add to each other.
- `tokens / cost by agent type` — `agents=N` is the count of distinct `agent-<aid>.jsonl` files of that type; `calls=N` is API calls inside them. `agents=2 calls=100` means two separate dispatches that each turned over ~50 times.
- `subagent calls (N agent files)` — the parenthetical is the count of `agent-*.jsonl` files merged in. If it's 0 but you expected subagents, the discovery couldn't see them; check `~/.claude/projects/<slug>/<session_id>/subagents/`.

### Precision limit on parallel tool calls

When an assistant message dispatches N `tool_use` blocks at once (e.g. several parallel `Read`s), the Anthropic protocol requires all `tool_result`s to come back together — so they **share a single `user` row's timestamp** in the transcript:

- **N=1** (solo tool): per-tool time = the bundle's wall time. **Exact.**
- **N>1** (parallel bundle): per-tool time can only be approximated as `bundle_wall / N`. Profile rows are marked with `~` and counted under `tool_approx_count_by_tool`.

To recover **real** per-tool start/end times for parallel bundles you need PreToolUse / PostToolUse hooks — see below.

---

## Optional: hook-augmented per-tool timing

Add this to `.claude/settings.json` (project-level or user-level) and the profiler will ingest hook events alongside the transcript, overriding the parallel-bundle approximations:

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

`${CLAUDE_PLUGIN_ROOT}` resolves automatically when this plugin is installed. If you're running from a clone instead, swap it for `${CLAUDE_PROJECT_DIR}` or an absolute path.

Each hook appends a line to the active profile's `events.from_hooks.jsonl`. **When no profile is active, every hook exits silently** — they never interfere with normal sessions, so it's safe to leave them installed.

---

## Output artifacts

Each `stop` writes to `$XDG_STATE_HOME/claude-code-profiler/windows/<profile_id>/` (default: `~/.local/state/claude-code-profiler/windows/<profile_id>/`):

```
profile.json              # full structured report — every metric, every breakdown, sorted keys
profile.md                # paste-ready Markdown report for issues / PRs
events.jsonl              # normalized event stream (turn_end / tool_end / mark / compaction / …)
transcript.snippet.jsonl  # raw transcript rows from the profile's interval (for replay / re-computation)
state.json                # start-time metadata frozen at `start` (git sha, env snapshot, start ts)
events.from_hooks.jsonl   # hook-emitted events (only present when hooks are wired)
```

`profile.json` is the source of truth — every field surfaced in the table or markdown comes from there, plus a few that don't fit on one screen (`tool_time_by_bucket_by_source_s`, `api_call_count_by_source`, `tokens_cost_by_agent_type`, …).

`stop --export DIR` additionally copies the bundle to `DIR/<profile_id>/` so you can archive it next to a benchmark run directory.

> The on-disk parent directory is named `windows/` for backward compatibility with state written by 0.1.x; the JSON `state.json` and `profile.json` keys still use `window_id` / `window_dir` for the same reason. We say "profile" in prose now and have no plan to break that on-disk contract.

---

## Price table

A static 2026-04 USD/million-token price table ships with the script, covering Opus 4 / 4.7, Sonnet 4 / 4.5 / 4.6, and Haiku 3.5 / 4.5. Matching is `model.startswith(prefix)`; unknown models report cost as `null` and surface the model id under `cost_uncovered_models` so you know what's missing.

```
claude-opus-4-7  : (input 15.00, output 75.00, cache_write_5m 18.75, cache_read 1.50)
claude-sonnet-4-6: ( 3.00, 15.00,  3.75, 0.30)
claude-haiku-4-5 : ( 1.00,  5.00,  1.25, 0.10)
…
```

Override at call time with `stop --prices "opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3"`. 1h cache writes (`cache_creation.ephemeral_1h_input_tokens`) are billed at 2× the 5m rate, applied automatically when present.

> ⚠ This is an **estimate**, not Anthropic's billing source of truth. Use it for "which chunk burned the most" / "how do these models compare", not for invoicing. The authoritative source is the [Usage page in the Claude Console](https://platform.claude.com/usage).

---

## Limitations

- **No OTel ingestion** (deliberately scoped out for now). If you already run Claude Code with OpenTelemetry the profiler simply ignores it. OTel ingestion is a candidate for a future release, primarily so the `query_source` (main / subagent / auxiliary) attribute on `claude_code.token.usage` events can be cross-checked against the file-source attribution.
- **Time outside the transcript isn't accounted.** A 5-minute coffee break appears as `idle/wait` (any user-side gap above the 300s default threshold). The profiler can't tell the difference between you walking away and a long human review of a plan.
- **Parallel-bundle per-tool time is approximate** (`bundle_wall / N`) in transcript-only mode. Wire up hooks for exact per-tool numbers — totals are unaffected either way.
- **Time can over-count when parent and subagent activity overlap closely.** In measured sessions the discrepancy between `api_time + tool_time + idle` and `wall` is on the order of 5-10%. Tokens and cost are unaffected — those are per-API-call sums, not gap measurements.
- **1h cache write tokens aren't split per-model in the cost calculation.** The 2× surcharge is allocated across models in proportion to each model's share of total `cache_creation_input_tokens`. Exact when only one model produces 1h writes (the common case); approximate otherwise.
- **One active profile per Claude Code session.** Different concurrent Claude Code sessions on the same host each get their own active-profile pointer (regression-tested in `tests/test_concurrent_sessions.py`); within one session, `start → stop → start → stop …` is fine but two simultaneous profiles are not.

---

## Tests

```bash
uv run pytest                                          # all tests
uv run pytest tests/test_turn_counting.py -v           # subagent / dedup / counter regression suite
uv run pytest tests/test_concurrent_sessions.py -v     # active-pointer isolation across sessions
```

`tests/_synth.py` builds synthetic JSONL transcripts (parent + subagent files; text, tool_use, and streaming-chunk row types) so individual aggregator behaviors can be locked in without a live Claude Code session. The `built_window` fixture in `tests/conftest.py` (named for the legacy on-disk term) starts a profile with `start_ts=0` patched in so anchored past timestamps fall inside the profile's interval.

---

## Repo layout

```
claude-code-profiler/
├── .claude-plugin/
│   ├── plugin.json             # Claude Code plugin manifest
│   └── marketplace.json        # single-plugin marketplace manifest
├── skills/
│   └── profile/
│       └── SKILL.md            # /profile dispatcher (resolves the script path, then exec's it)
├── scripts/
│   └── cc_profiler.py          # single-file implementation, stdlib only
├── tests/
│   ├── _synth.py               # transcript builders for unit tests
│   ├── conftest.py             # built_window fixture
│   ├── test_concurrent_sessions.py
│   └── test_turn_counting.py
├── notes/
│   └── subagent-coverage.md    # 0.2.0 design notes (committed)
│                               # other notes/*.md are local scratch (gitignored)
├── CHANGELOG.md
├── README.md                   # English (this file)
└── README_cn.md                # 中文
```

---

## Roadmap

- **0.2.x (current)** — transcript-only with full subagent coverage, dedup-correct token totals, per-source / per-model / per-`agentType` breakdowns, finer Bash classification, and a regression suite. Distributed as a Claude Code plugin via the bundled single-plugin marketplace.
- **Next** — hook-augmented per-tool timing wired by default (so parallel bundles get exact per-tool durations), optional OTel ingestion when `CLAUDE_CODE_ENABLE_TELEMETRY=1`, concurrent profiles within one Claude Code session, and an `update-config` skill that wires the hooks for you.
- **Later** — batch run launcher + cross-run aggregation (CSV / JSON) for benchmarking workflows; HTML report.
