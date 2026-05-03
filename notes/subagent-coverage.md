# Subagent coverage (0.1.2 → 0.2.0)

## The bug

`/profile stop` was reading **only the parent transcript JSONL**
(`~/.claude/projects/<slug>/<session>.jsonl`). Modern Claude Code stores each
Task-tool dispatched subagent in its **own** file under
`<slug>/<session>/subagents/agent-<id>.jsonl`, with `isSidechain: true` rows.
Subagent activity never appears in the parent file.

Empirically (real session `d355ee64...` in claude-nautilus):

| metric          | v0.1.2 (parent only) | v0.2.0 (parent + subagents) |
|-----------------|----------------------|------------------------------|
| estimated cost  | **\$5.93**           | **\$14.27** (2.4×)          |
| sidechain turns | 0                    | 131                         |
| subagent files  | (not surfaced)       | 3                           |

The `is_sidechain`-aware code path that already existed was effectively dead —
no row in the file we read carried that flag.

## Why no double-count

Each transcript row corresponds to **one** API call with **one** `usage` block.
Parent and subagent are separate API calls in separate files, so summing
tokens across files is exact addition. The single documented double-count risk
is multi-row streaming for one logical assistant message (parallel tool blocks
share `message.id`); the existing message-id merge in `feed_transcript`
handles that, and we extended its key to `(source, message.id)` defensively.

## Time accounting

Tokens compose cleanly across files, time does not. The parent's `Agent` /
`Skill` tool-use bundle wall = subagent's full runtime. If we *also* walk the
subagent file's per-turn `api_time` and per-bundle `tool_time`, that interval
gets counted twice.

Resolution: `Aggregator.have_subagent_files` flag. When subagents were
discovered, we **suppress the parent's Agent/Skill bundle wall** (set
`tool_dur = 0` for any tool_use whose name is in `AGENT_TOOLS`). The subagent
file's decomposition replaces it. When no subagent files exist (legacy
sessions, or test transcripts), behavior is unchanged.

This rule applies to *any* Agent tool_use regardless of source — nested
subagents land in the same flat `<session>/subagents/` directory, so
suppression also handles a subagent that itself dispatches another subagent.

## What's new in the report

- `tokens_cost_by_query_source` — main vs subagent split (mirrors Anthropic's
  OTEL `query_source` attribute)
- `tokens_cost_by_model` — per-model breakdown (mirrors SDK `modelUsage`)
- `tokens_cost_by_agent_type` — keyed off `agentType` from `agent-*.meta.json`
- `cache_creation_5m_tokens` / `cache_creation_1h_tokens` / `cache_hit_ratio`
- `thinking_blocks` / `thinking_text_chars`
- `server_tool_use_counts` (web_search / web_fetch from API-side usage)
- `service_tier_counts`, `speed_counts`, `stop_reason_counts`
- `tool_time_by_bucket_by_source_s` — per-bucket time split by source
- `subagent_files`, `api_call_count_by_source`

## References

- Anthropic SDK cost-tracking docs (deduplicate by `message.id`):
  `code.claude.com/docs/en/agent-sdk/cost-tracking`
- OTEL metric `claude_code.token.usage` with `query_source` attribute:
  `code.claude.com/docs/en/monitoring-usage`
- GitHub `anthropics/claude-code` issues #10164 and #22625 confirm subagent
  tokens are excluded from the main session's `/usage` and `/context` numbers.
