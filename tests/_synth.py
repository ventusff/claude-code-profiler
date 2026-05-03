"""Helpers for building synthetic Claude transcript JSONLs.

Tests use these to construct parent-session transcripts and dispatched-subagent
transcripts at the file level, so we can drive `cc_profiler stop` without a
live Claude Code session and assert against deterministic counts.

A row is just a `dict` written one-per-line. The `Transcript` builder owns
timestamp generation (monotonically increasing) so callers don't have to.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Anchor far in the past so test rows fall after `start_ts=0` (which we patch
# the state to) but well before whatever `time.time()` is when stop runs.
DEFAULT_ANCHOR = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# Module-level counters so message/tool/uuid ids are unique across every
# Transcript instance in a single test run. Real Anthropic message ids are
# globally unique; if the synth allowed collisions, the profiler's streaming-
# merge dedup (`(source, message.id)`) would silently fold two distinct
# subagent rows into one and tests would be measuring the wrong thing.
_GLOBAL_UID = itertools.count(1)
_GLOBAL_MID = itertools.count(1)
_GLOBAL_TID = itertools.count(1)


@dataclass
class Transcript:
    """Builder for a single JSONL transcript file."""

    path: Path
    anchor: datetime = DEFAULT_ANCHOR
    _step: int = 0
    rows: list[dict] = field(default_factory=list)

    def _ts(self) -> str:
        self._step += 1
        return (self.anchor + timedelta(seconds=self._step)).isoformat().replace("+00:00", "Z")

    def _ts_at(self, offset_s: float) -> str:
        return (self.anchor + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")

    def _new_uuid(self) -> str:
        return f"u-{next(_GLOBAL_UID):06d}"

    def _new_mid(self) -> str:
        return f"msg_{next(_GLOBAL_MID):010d}"

    def _new_tid(self) -> str:
        return f"toolu_{next(_GLOBAL_TID):010d}"

    def assistant_text(
        self,
        text: str = "hi",
        *,
        model: str = "claude-opus-4-7",
        is_sidechain: bool = False,
        usage: dict | None = None,
        message_id: str | None = None,
    ) -> dict:
        row = {
            "type": "assistant",
            "uuid": self._new_uuid(),
            "timestamp": self._ts(),
            "isSidechain": is_sidechain,
            "message": {
                "id": message_id or self._new_mid(),
                "model": model,
                "content": [{"type": "text", "text": text}],
                "usage": usage or _default_usage(),
                "stop_reason": "end_turn",
            },
        }
        self.rows.append(row)
        return row

    def assistant_with_tools(
        self,
        tools: list[tuple[str, dict]],
        *,
        model: str = "claude-opus-4-7",
        is_sidechain: bool = False,
        usage: dict | None = None,
        bundle_dur_s: float = 1.0,
    ) -> tuple[dict, list[str]]:
        """Emit one assistant row with N `tool_use` blocks, plus matching tool_result
        user-rows so the bundle wall is well-defined.

        Returns the assistant row and the list of tool_use IDs emitted.
        """
        ts_assistant = self._ts()
        tool_ids: list[str] = []
        content: list[dict] = []
        for name, tinput in tools:
            tid = self._new_tid()
            tool_ids.append(tid)
            content.append({"type": "tool_use", "id": tid, "name": name, "input": tinput})

        row = {
            "type": "assistant",
            "uuid": self._new_uuid(),
            "timestamp": ts_assistant,
            "isSidechain": is_sidechain,
            "message": {
                "id": self._new_mid(),
                "model": model,
                "content": content,
                "usage": usage or _default_usage(),
                "stop_reason": "tool_use",
            },
        }
        self.rows.append(row)

        # Pair every tool_use with one tool_result user-row, sharing one ts so
        # the profiler treats them as one parallel bundle.
        result_ts = self._ts_at(self._step + bundle_dur_s)
        # Bump the internal counter past the bundle end so subsequent rows are later.
        self._step += int(bundle_dur_s) if bundle_dur_s >= 1 else 0
        result_row = {
            "type": "user",
            "uuid": self._new_uuid(),
            "timestamp": result_ts,
            "isSidechain": is_sidechain,
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": "ok"}
                    for tid in tool_ids
                ],
            },
        }
        self.rows.append(result_row)
        return row, tool_ids

    def assistant_streaming_chunk(
        self,
        message_id: str,
        text: str,
        *,
        model: str = "claude-opus-4-7",
        is_sidechain: bool = False,
        usage: dict | None = None,
    ) -> dict:
        """Emit a row that *shares* a `message.id` with another assistant row.

        Claude streams a single logical assistant turn into multiple JSONL rows
        (one per content block); the profiler must merge them by `message.id`.
        Use this to test that dedup behavior.
        """
        row = {
            "type": "assistant",
            "uuid": self._new_uuid(),
            "timestamp": self._ts(),
            "isSidechain": is_sidechain,
            "message": {
                "id": message_id,
                "model": model,
                "content": [{"type": "text", "text": text}],
                "usage": usage or _default_usage(),
                "stop_reason": "end_turn",
            },
        }
        self.rows.append(row)
        return row

    def user_prompt(self, text: str = "do something") -> dict:
        row = {
            "type": "user",
            "uuid": self._new_uuid(),
            "timestamp": self._ts(),
            "message": {"content": text},
        }
        self.rows.append(row)
        return row

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n")
        return self.path


def _default_usage() -> dict:
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def parent_path(claude_home: Path, cwd: Path, session_id: str) -> Path:
    """Compute the parent transcript path Claude Code would use."""
    slug = str(cwd.resolve()).replace("/", "-").replace("_", "-").replace(".", "-")
    return claude_home / "projects" / slug / f"{session_id}.jsonl"


def subagent_path(parent: Path, agent_id: str) -> Path:
    """Path for a subagent JSONL — `<parent_dir>/<parent_stem>/subagents/agent-<id>.jsonl`."""
    return parent.parent / parent.stem / "subagents" / f"agent-{agent_id}.jsonl"


def write_subagent_meta(sub_path: Path, agent_type: str, description: str = "") -> Path:
    """Drop the sidecar `agent-<id>.meta.json` next to a subagent JSONL."""
    meta_path = sub_path.with_suffix(".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"agentType": agent_type, "description": description}))
    return meta_path
