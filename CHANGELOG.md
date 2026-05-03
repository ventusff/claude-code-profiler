# Changelog

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
