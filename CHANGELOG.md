# Changelog

## 0.3.0

### Added — `retro` subcommand

Profile a slice of the current session retroactively, without ever calling
`start`. The motivating use case: you already did the work, didn't think to
profile it, and now want the numbers anyway.

```
/profile retro                        # entire session so far
/profile retro --since 30m            # last 30 minutes
/profile retro --since last-prompt    # since your most recent message
/profile retro --since 2026-05-04T11:26:45Z --until now
```

`--since` and `--until` accept ISO-8601 timestamps, epoch seconds, relative
durations (`30m`, `1h30m`, `2d12h`, `45s`), or one of the literals: `now`,
`first` (first transcript timestamp — default for `--since`), or
`last-prompt` (most recent user message — skips tool-result rows and
sidechain user turns). Default for `--until` is `now`.

Retro emits the same artifact bundle as `stop` (`profile.json`, `profile.md`,
`events.jsonl`, `transcript.snippet.jsonl`, `state.json`) into a new
`windows/<id>/`. `state.json` and the report carry `mode: "retroactive"` so
bundles are self-describing.

`retro` never touches the active-profile pointer, so it's safe to run
alongside an in-progress `/profile start`.

### Refactored

- Extracted the aggregation + artifact-emission body of `cmd_stop` into
  `_aggregate_and_emit(state, end_ts, prices, args)`. Both `cmd_stop` and
  `cmd_retro` reuse it, so the metrics, formats, and on-disk layout stay
  identical between the two paths.

## 0.2.2

### Changed — terminology
- **"window" → "profile"** in all user-facing prose and CLI output strings.
  "Profile" is the standard term in profiler tooling (cProfile, Go pprof, Linux
  `perf`) and lines up with the slash command (`/profile`), the tool name, and
  the artifact files (`profile.json`, `profile.md`). Status output now reads
  `no active profile.` instead of `no active window.`; equivalent rewording in
  every other CLI message and `--help` line.
- Internal data structure / on-disk contract unchanged: `state.json` and
  `profile.json` still carry `window_id` / `window_dir` fields, the artifact
  parent directory is still `windows/<id>/`, and the test fixture is still
  named `built_window`. Renaming those would break state written by 0.1.x and
  0.2.0/0.2.1 installs; the README footnote in the Output artifacts section
  flags this divergence.

### Changed — docs
- Major README rewrite (English + Chinese) from "How it works" onwards. The
  new "How it works" section explains where Claude Code stores parent +
  subagent transcripts on disk, what each metric is and how it's aggregated,
  and the two correctness rules (`(source, agent_id, message.id)` dedup;
  Agent/Skill/Task bundle-wall suppression) that keep totals honest. New
  "Reading the report" subsection demystifies non-obvious labels (`tool time
  sum` vs `wall`, `(main)`/`(subagent)` rows, `agents=N calls=N`). New "Tests"
  section. Limitations refreshed (overlap-induced time over-count, 1h-cache
  cost approximation). Roadmap rewritten as current / next / later.
- Stale "Only one active ... per machine at a time" guidance corrected to
  per-Claude-Code-session, with the regression-test reference.
- README_cn.md fully resynced — same 13 sections, same example output (with
  0.2.0 fields), same terminology callout.

### Fixed
- One test assertion in `tests/test_concurrent_sessions.py` updated to match
  the new CLI string (`"no active profile"`).

## 0.2.1

### Fixed
- **Cross-file message-id collision** — `feed_transcript`'s streaming-merge
  dedup key now includes `_qs_agent_id` alongside `(source, message.id)`. Real
  Anthropic message ids are globally unique so this is defensive, but if two
  subagent files ever shared an id (fuzzed input, replayed fixtures, future
  schema drift) the previous key would silently fold two distinct API calls
  into one and undercount turns / tokens / cost.

### Added — tests
- `tests/test_turn_counting.py` (11 tests) locks the relationship between
  transcript rows and reported counters: `turns`, `sidechain_turns`,
  `debug_turns`, `user_prompts`, `tool_calls`, `subagent_calls`,
  `subagent_files`, `api_call_count_by_source`, plus message-id dedup,
  legacy-`isSidechain` parent rows, and the cross-file collision regression.
- `tests/_synth.py` — JSONL transcript builders (parent + subagent files,
  text/tool-use/streaming-chunk rows). Module-level globally-unique id
  counters mirror real Anthropic message-id semantics.
- `tests/conftest.py` — `built_window` fixture: starts a window, patches
  `start_ts=0` so test rows with anchored past timestamps fall in-window,
  exposes `parent` / `sub(agent_id)` builders + `stop()`.

## 0.2.0

### Fixed
- **Subagent coverage** — `/profile stop` now reads parent transcript +
  every sibling subagent transcript under `<session>/subagents/agent-*.jsonl`.
  The previous version saw only the parent and undercounted cost / tokens /
  turns by the size of the subagent work; in measured sessions this was
  routinely 2-4× off (\$5.93 → \$14.27 on one real session).
  See `notes/subagent-coverage.md` for design notes.

- **Time double-counting** — when subagent files are walked, the parent's
  `Agent`/`Skill` tool-use bundle wall is suppressed so the same interval is
  not counted both as a parent bundle AND as the subagent's decomposed
  `api_time` + `tool_time`. The suppressed bundle is still recorded for tool
  count purposes; only its time is zeroed.

- **Parent-transcript discovery** — `discover_transcript()` now restricts
  the glob to the project root, so a large subagent JSONL can no longer
  masquerade as the parent.

### Added — new report fields
- `tokens_cost_by_query_source` — main vs subagent token / cost split.
- `tokens_cost_by_model` — per-model breakdown (mirrors Anthropic's
  `modelUsage`).
- `tokens_cost_by_agent_type` — keyed off `agentType` from
  `agent-<id>.meta.json`.
- `cache_creation_5m_tokens` / `cache_creation_1h_tokens` / `cache_hit_ratio`.
- `thinking_blocks` / `thinking_text_chars` for extended-thinking output.
- `server_tool_use_counts` — Anthropic-hosted `web_search` / `web_fetch`
  invocation counts from `usage.server_tool_use`.
- `service_tier_counts`, `speed_counts` — request-tier and fast/standard
  distribution.
- `stop_reason_counts` — distribution of `stop_reason` values across turns.
- `tool_time_by_bucket_by_source_s` — bucket time split by source.
- `subagent_files`, `api_call_count_by_source`.
- Table and markdown renderers updated to surface the new breakdowns.

### Changed
- Internal: `Aggregator` gained `have_subagent_files` (drives time
  suppression), `_bump_by_model` / `_bump_by_source` / `_bump_by_agent_type`
  helpers, and a richer dataclass.
- `_bump_bucket(bucket, dur, source="main")` — sources are tracked alongside
  bucket time to feed the new `bucket_time_by_source` field.

### Compatibility
- `profile.json` is additive — every prior field is still present with the
  same shape; new fields can be ignored by older consumers.
- The CLI surface (`start` / `status` / `mark` / `stop` / `reset`) and the
  active-window state file are unchanged.
- Existing concurrent-session tests pass without modification.

## 0.1.2

- Fix: stop relying on `${CLAUDE_PLUGIN_ROOT}` in skill bash; resolve script
  path by globbing the plugin cache.

## 0.1.1

- Bump to ship the script-path resolver fix to installed users.

## 0.1.0

- Initial release.
