#!/usr/bin/env python3
"""claude-code-profiler — profile a Claude Code session window.

Usage:
  claude-code-profiler start [name] [--tag k=v]... [--note "..."]
  claude-code-profiler status
  claude-code-profiler mark <label>
  claude-code-profiler stop  [--format=table|markdown|json] [--export <dir>]
  claude-code-profiler reset
  claude-code-profiler _hook <event_name>     # invoked from settings.json hooks; reads JSON stdin

The profiler is transcript-driven (Claude Code JSONL under ~/.claude/projects/<slug>/).
Hook events are an optional richer signal — when configured, they augment the report.
OpenTelemetry support is intentionally not wired in v0 (the user does not run with OTel).

State directory: $XDG_STATE_HOME/claude-code-profiler  (default ~/.local/state/claude-code-profiler).
Architecture & rationale: see notes/plan.md in the repo.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_HOME / "projects"


def state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "claude-code-profiler"


def windows_dir() -> Path:
    return state_root() / "windows"


def _read_proc_status_ppid(pid: int) -> int | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    m = re.search(r"^PPid:\s+(\d+)", status, re.M)
    return int(m.group(1)) if m else None


def _read_proc_comm(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip() or None
    except (FileNotFoundError, PermissionError, OSError):
        return None


def find_session_key() -> str:
    """Return a stable identifier for the current Claude Code session.

    Two concurrent Claude sessions on the same host must each have their own
    "active window" pointer — otherwise one session's `/profile start` blocks
    the other. We discover the session by walking up the process tree to the
    nearest ancestor named `claude` and using its PID.

    Resolution order:
      1. `CLAUDE_PROFILER_SESSION_KEY` env var — explicit override (used by tests
         and by anyone who wants to pin behavior).
      2. `CLAUDE_SESSION_ID` env var — set by Claude Code in some contexts.
      3. Walk parent process tree for an ancestor whose comm == "claude" (Linux).
      4. Fallback: a single shared `standalone` key — preserves prior behavior
         when the script is run outside Claude Code (e.g. local debugging).
    """
    override = os.environ.get("CLAUDE_PROFILER_SESSION_KEY")
    if override:
        return f"key-{override}"
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return f"sid-{sid}"
    # Linux /proc walk
    try:
        pid = os.getppid()
        seen: set[int] = set()
        while pid and pid > 1 and pid not in seen:
            seen.add(pid)
            comm = _read_proc_comm(pid)
            if comm == "claude":
                return f"pid-{pid}"
            ppid = _read_proc_status_ppid(pid)
            if ppid is None:
                break
            pid = ppid
    except Exception:
        pass
    return "standalone"


def active_dir() -> Path:
    return state_root() / "active"


def active_pointer(session_key: str | None = None) -> Path:
    """Return the per-session active-window pointer path.

    `session_key=None` means "auto-detect from environment / process tree".
    """
    key = session_key if session_key is not None else find_session_key()
    # Sanitize to a safe filename component (keys can include UUIDs etc.).
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key) or "standalone"
    return active_dir() / f"{safe}.json"


# ---------------------------------------------------------------------------
# Pricing table (USD per 1M tokens) — as of 2026-04. Override via --prices.
# Tuple = (input, output, cache_write_5m, cache_read).
# 1h cache writes (`ephemeral_1h_input_tokens`) are billed at 2x cache_write_5m;
# we apply the 2x multiplier when 1h tokens are present.
# ---------------------------------------------------------------------------

DEFAULT_PRICES: list[tuple[str, tuple[float, float, float, float]]] = [
    ("claude-opus-4-7",   (15.00, 75.00, 18.75, 1.50)),
    ("claude-opus-4",     (15.00, 75.00, 18.75, 1.50)),
    ("claude-sonnet-4-6", ( 3.00, 15.00,  3.75, 0.30)),
    ("claude-sonnet-4-5", ( 3.00, 15.00,  3.75, 0.30)),
    ("claude-sonnet-4",   ( 3.00, 15.00,  3.75, 0.30)),
    ("claude-haiku-4-5",  ( 1.00,  5.00,  1.25, 0.10)),
    ("claude-haiku-3-5",  ( 0.80,  4.00,  1.00, 0.08)),
]


def parse_prices_override(spec: str | None) -> list[tuple[str, tuple[float, float, float, float]]]:
    """`opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3` → list of (prefix, quad).

    Same format as claude-nautilus/shared/scripts/audit_summarize.py for compatibility.
    """
    if not spec:
        return []
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        prefix, _, vals = chunk.partition(":")
        nums = [float(x) for x in vals.split(",")]
        if len(nums) != 4:
            raise SystemExit(f"--prices: expected 4 numbers per model, got {chunk!r}")
        out.append((prefix.strip(), (nums[0], nums[1], nums[2], nums[3])))
    return out


def lookup_price(model: str, table) -> tuple[float, float, float, float] | None:
    if not model:
        return None
    for prefix, quad in table:
        if model.startswith(prefix) or prefix in model:
            return quad
    return None


def turn_cost(usage: dict, model: str, table) -> float | None:
    quad = lookup_price(model, table)
    if quad is None:
        return None
    p_in, p_out, p_cw, p_cr = quad
    inp = int(usage.get("input_tokens") or 0)
    outp = int(usage.get("output_tokens") or 0)
    cw_5m = int(usage.get("cache_creation_input_tokens") or 0)
    # 1h cache writes are billed 2x. Detect via cache_creation.ephemeral_1h_input_tokens.
    cw_1h = 0
    cc = usage.get("cache_creation") or {}
    if isinstance(cc, dict):
        cw_1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
        # ephemeral_5m_input_tokens is the 5m portion already counted in cw_5m;
        # cache_creation_input_tokens = 5m + 1h, so subtract 1h portion to avoid double-charge.
        cw_5m_only = max(0, cw_5m - cw_1h)
    else:
        cw_5m_only = cw_5m
    cr = int(usage.get("cache_read_input_tokens") or 0)
    return (
        inp * p_in / 1e6
        + outp * p_out / 1e6
        + cw_5m_only * p_cw / 1e6
        + cw_1h * p_cw * 2.0 / 1e6
        + cr * p_cr / 1e6
    )


# ---------------------------------------------------------------------------
# Bash classification — extends audit_timing.py's regexes with finer-grained
# robotics-relevant buckets. Order matters: first match wins.
# ---------------------------------------------------------------------------

# Most specific first; first hit defines the bucket.
BASH_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("docker_pull",        re.compile(r"\bdocker\s+pull\b")),
    ("docker_build",       re.compile(r"\bdocker\s+(build|compose\s+(build|up\s+--build))\b")),
    ("checkpoint_dl",      re.compile(
        r"(wget|curl)\b[^\n;|&]*\.(ckpt|pt|pth|safetensors|bin)\b"
        r"|huggingface-cli\s+download\s+\S+\s+(--include\s+\*\.(ckpt|pt|safetensors)|[^\n]*model)"
        r"|\bhf\s+download\b[^\n]*model"
        r"|\bcheckpoints?/"
    )),
    ("dataset_dl",         re.compile(
        r"\bhuggingface-cli\s+download\b"
        r"|\bhf\s+download\b"
        r"|\b(wget|curl)\b[^\n;|&]*\b(datasets?/|data/|/dataset/|kaggle|webdataset)"
        r"|\baws\s+s3\s+(cp|sync)\b[^\n]*data"
        r"|\bgsutil\s+(cp|rsync)\b[^\n]*data"
    )),
    ("benchmark_run",      re.compile(
        r"python\d?\s+[^\n;|&]*(run_eval\.py|run_demo\.py|train_rl_policy|"
        r"libero_eval|robocasa_eval|robotwin_eval|benchmark)"
    )),
    ("test",               re.compile(
        r"\bpytest\b|python\d?\s+-m\s+(unittest|pytest)\b|"
        r"docker\s+(exec|run)\b[^\n]*\bpython\d?\b[^\n]*\b(test|smoke|eval)"
    )),
    ("infra",              re.compile(
        r"\bdocker\s+(push|tag|save|load|import|export)\b"
        r"|\b(apt|apt-get)\s+(install|update|upgrade|dist-upgrade)\b"
        r"|\b(pip3?|uv\s+pip|uv\s+sync|poetry)\s+(install|sync|add)\b"
        r"|\b(npm|yarn|pnpm)\s+(install|ci|add)\b"
        r"|\bcargo\s+(build|install)\b"
        r"|\bconda\s+(create|install|env\s+create|env\s+update)\b"
        r"|\bmake\b(?!.*\btest)"
    )),
    ("download",           re.compile(
        r"\b(wget|curl)\b[^\n]*\b(https?|ftp)://"
        r"|\bgit\s+clone\b|\bgit\s+lfs\s+(pull|fetch|clone)\b"
        r"|\brsync\b[^\n]*::"
    )),
]


def classify_bash(cmd: str) -> str:
    cmd = cmd or ""
    for label, pat in BASH_RULES:
        if pat.search(cmd):
            return label
    return "bash"  # generic uncategorized Bash


# Non-Bash tool buckets — mirrors audit_timing.py.
CODING_TOOLS = {"Read", "Edit", "Write", "Grep", "Glob", "MultiEdit",
                "NotebookEdit", "ToolSearch", "TaskCreate", "TaskUpdate",
                "TaskList", "TaskGet", "TaskOutput", "TaskStop"}
WEB_TOOLS = {"WebFetch", "WebSearch"}
AGENT_TOOLS = {"Agent", "Skill"}


def classify_tool(name: str, tool_input: dict) -> str:
    """Return a bucket name for a tool_use."""
    if name == "Bash":
        return classify_bash(tool_input.get("command", ""))
    if name in CODING_TOOLS:
        return "coding"
    if name in WEB_TOOLS:
        return "web"
    if name in AGENT_TOOLS:
        return "agent_dispatch"
    if name.startswith("mcp__"):
        return "mcp"
    return "other"


# ---------------------------------------------------------------------------
# Timestamp & transcript helpers
# ---------------------------------------------------------------------------

def parse_ts(v: Any) -> float | None:
    """Parse ISO-8601 string or epoch number → epoch float seconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            s = v.replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except (ValueError, TypeError):
            try:
                return float(v)
            except ValueError:
                return None
    return None


def fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def cwd_to_project_slug(cwd: Path) -> str:
    """Mirror Claude Code's slug convention: replace `/`, `_`, `.` with `-`.

    Examples (verified against ~/.claude/projects):
      /home/ventus/vortex_ws/chaser/roboharness/claude-nautilus
        → -home-ventus-vortex-ws-chaser-roboharness-claude-nautilus
      /home/ventus/vortex_ws/utils/claude-code-profiler
        → -home-ventus-vortex-ws-utils-claude-code-profiler
    """
    s = str(cwd.resolve())
    return s.replace("/", "-").replace("_", "-").replace(".", "-")


def discover_transcript(cwd: Path, prefer_session: str | None = None) -> tuple[Path, str] | None:
    """Find the active transcript JSONL for `cwd`. Returns (path, session_id) or None."""
    slug = cwd_to_project_slug(cwd)
    project_dir = PROJECTS_DIR / slug
    if not project_dir.is_dir():
        return None
    candidates = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if prefer_session:
        for p in candidates:
            if p.stem == prefer_session:
                return p, prefer_session
    if not candidates:
        return None
    p = candidates[0]
    return p, p.stem


def iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Aggregator — transcript scan → metrics
# ---------------------------------------------------------------------------

@dataclass
class Aggregator:
    start_ts: float
    end_ts: float
    prices: list
    idle_threshold: float = 300.0  # seconds of user-side gap that count as idle, not "user-thinking"

    # Per-bucket time accumulators (seconds)
    bucket_time: dict[str, float] = field(default_factory=dict)
    bucket_count: dict[str, int] = field(default_factory=dict)

    # Tool-level aggregation
    per_tool_time: dict[str, float] = field(default_factory=dict)
    per_tool_count: dict[str, int] = field(default_factory=dict)
    per_tool_success: dict[str, int] = field(default_factory=dict)
    per_tool_failure: dict[str, int] = field(default_factory=dict)
    # Number of times this tool was the SOLE tool in its parent message (exact dur)
    # vs. one of several parallel tools (dur = bundle_wall / N, approximate).
    per_tool_exact_count: dict[str, int] = field(default_factory=dict)
    per_tool_approx_count: dict[str, int] = field(default_factory=dict)

    # API time
    api_time_sum: float = 0.0
    api_time_by_model: dict[str, float] = field(default_factory=dict)
    api_time_by_query_source: dict[str, float] = field(default_factory=dict)  # main/subagent
    api_call_count: int = 0
    api_error_count: int = 0
    retry_count: int = 0
    seen_request_ids: set[str] = field(default_factory=set)

    # User/idle
    active_time_user: float = 0.0
    idle_time: float = 0.0
    user_prompts: int = 0
    assistant_turns: int = 0
    sidechain_turns: int = 0
    debug_turns: int = 0
    tool_calls: int = 0
    subagent_calls: int = 0

    # Tokens & cost
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    tool_result_tokens_est: int = 0
    cost_usd: float = 0.0
    cost_uncovered_models: set[str] = field(default_factory=set)

    # Compaction
    compaction_count: int = 0
    compaction_time: float = 0.0
    pre_compaction_tokens: int = 0
    post_compaction_tokens: int = 0
    _last_input_tokens_before_compaction: int = 0
    _expecting_post_compaction: bool = False

    # Critical-path accumulator (set by feed_transcript at finalization)
    tool_time_critical_path: float = 0.0

    # Edits
    lines_added: int = 0
    lines_removed: int = 0

    # Models seen
    models: set[str] = field(default_factory=set)

    # Normalized event stream we'll persist as events.jsonl
    events: list[dict] = field(default_factory=list)

    # Raw transcript rows (for snippet artifact)
    raw_rows: list[dict] = field(default_factory=list)

    def _bump_bucket(self, bucket: str, dur: float) -> None:
        self.bucket_time[bucket] = self.bucket_time.get(bucket, 0.0) + dur
        self.bucket_count[bucket] = self.bucket_count.get(bucket, 0) + 1

    def _record_tool(self, name: str, dur: float, ok: bool, exact: bool) -> None:
        self.per_tool_time[name] = self.per_tool_time.get(name, 0.0) + dur
        self.per_tool_count[name] = self.per_tool_count.get(name, 0) + 1
        if ok:
            self.per_tool_success[name] = self.per_tool_success.get(name, 0) + 1
        else:
            self.per_tool_failure[name] = self.per_tool_failure.get(name, 0) + 1
        if exact:
            self.per_tool_exact_count[name] = self.per_tool_exact_count.get(name, 0) + 1
        else:
            self.per_tool_approx_count[name] = self.per_tool_approx_count.get(name, 0) + 1

    def feed_transcript(self, rows: list[dict]) -> None:
        """Process all transcript rows in [start_ts, end_ts].

        Claude streams a single logical assistant message into multiple JSONL rows,
        one per content block (text / thinking / tool_use). All such rows share the
        same `message.id`, the same `usage` block, and the same `requestId`. We must
        merge them before aggregating, otherwise turn counts and tokens are inflated
        by 2-6x.
        """
        # 1) Filter to window.
        windowed: list[dict] = []
        for r in rows:
            ts = parse_ts(r.get("timestamp"))
            if ts is None:
                continue
            if ts < self.start_ts or ts > self.end_ts:
                continue
            windowed.append(r)
        windowed.sort(key=lambda r: parse_ts(r.get("timestamp")) or 0.0)
        self.raw_rows = windowed

        # 2) Merge multi-row assistant messages by message.id. User rows pass through.
        merged: list[dict] = []
        asst_by_mid: dict[str, dict] = {}
        for r in windowed:
            if r.get("type") != "assistant":
                merged.append(r)
                continue
            msg = r.get("message") or {}
            mid = msg.get("id") or r.get("uuid") or ""
            if mid in asst_by_mid:
                existing = asst_by_mid[mid]
                # Append content blocks from this row to the merged content.
                existing_msg = existing.setdefault("message", {})
                existing_content = existing_msg.setdefault("content", [])
                for c in (msg.get("content") or []):
                    existing_content.append(c)
                # Earliest ts wins (start of streaming).
                this_ts = parse_ts(r.get("timestamp")) or 0.0
                exist_ts = parse_ts(existing.get("timestamp")) or 0.0
                if this_ts < exist_ts:
                    existing["timestamp"] = r.get("timestamp")
                continue
            # First row for this message id — copy so we don't mutate input.
            copy = json.loads(json.dumps(r))
            asst_by_mid[mid] = copy
            merged.append(copy)

        merged.sort(key=lambda r: parse_ts(r.get("timestamp")) or 0.0)
        windowed = merged

        # 3) Build tool_use_id → parent assistant info from merged messages.
        tool_use_idx: dict[str, dict] = {}
        for r in windowed:
            if r.get("type") != "assistant":
                continue
            for c in (r.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_use_idx[c.get("id", "")] = {
                        "ts": parse_ts(r.get("timestamp")),
                        "name": c.get("name", ""),
                        "input": c.get("input") or {},
                        "msg_uuid": r.get("uuid", ""),
                        "is_sidechain": bool(r.get("isSidechain")),
                    }

        # Pre-pass: index tool_results by tool_use_id → its user-row ts.
        # Anthropic's harness emits all parallel tool_results to the API
        # simultaneously, so they typically share a single user-row timestamp.
        # When that's true, individual durations are not recoverable from the
        # transcript — we fall back to bundle wall-time / N (approximate).
        # We still find each result's ts for solo-tool exact accounting.
        result_by_tuid: dict[str, tuple[dict, float]] = {}  # tuid → (tool_result, user_ts)
        for r in windowed:
            if r.get("type") != "user":
                continue
            user_ts = parse_ts(r.get("timestamp"))
            if user_ts is None:
                continue
            content = (r.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    tuid = c.get("tool_use_id", "")
                    if tuid and tuid not in result_by_tuid:
                        result_by_tuid[tuid] = (c, user_ts)

        last_ts: float | None = None
        last_kind: str | None = None
        # parent_msg_uuid → bundle wall-clock (for tool_time_critical_path).
        parent_bundle_wall: dict[str, float] = {}

        for r in windowed:
            ts = parse_ts(r.get("timestamp"))
            if ts is None:
                continue
            t = r.get("type")
            msg = r.get("message") or {}

            # ---- Compaction markers ---------------------------------------
            # Claude transcripts use `type:"summary"` (for /compact) or rows
            # with `subtype` containing "compact". We treat both as boundaries.
            if t == "summary" or "compact" in str(r.get("subtype", "")):
                self.compaction_count += 1
                if last_ts is not None:
                    self.compaction_time += max(0.0, ts - last_ts)
                self._expecting_post_compaction = True
                self.events.append({
                    "ts": fmt_ts(ts), "kind": "compaction",
                    "msg_uuid": r.get("uuid", ""),
                })
                last_ts, last_kind = ts, "compaction"
                continue

            if t == "assistant":
                self.assistant_turns += 1
                is_side = bool(r.get("isSidechain"))
                if is_side:
                    self.sidechain_turns += 1
                model = msg.get("model") or ""
                if model:
                    self.models.add(model)

                # api_time: gap between previous user_prompt|tool_result|compaction and this assistant
                if last_ts is not None and last_kind in ("user_prompt", "tool_result", "compaction", "start"):
                    delta = max(0.0, ts - last_ts)
                    self.api_time_sum += delta
                    self.api_time_by_model[model] = self.api_time_by_model.get(model, 0.0) + delta
                    src = "subagent" if is_side else "main"
                    self.api_time_by_query_source[src] = self.api_time_by_query_source.get(src, 0.0) + delta
                    self.events.append({
                        "ts": fmt_ts(ts), "kind": "turn_end", "dur_s": round(delta, 3),
                        "model": model, "is_sidechain": is_side,
                        "msg_uuid": r.get("uuid", ""),
                    })

                # usage / cost
                u = msg.get("usage") or {}
                if u:
                    self.api_call_count += 1
                    self.input_tokens          += int(u.get("input_tokens") or 0)
                    self.output_tokens         += int(u.get("output_tokens") or 0)
                    self.cache_creation_tokens += int(u.get("cache_creation_input_tokens") or 0)
                    self.cache_read_tokens     += int(u.get("cache_read_input_tokens") or 0)
                    c = turn_cost(u, model, self.prices)
                    if c is None and model:
                        self.cost_uncovered_models.add(model)
                    elif c is not None:
                        self.cost_usd += c

                    if self._expecting_post_compaction:
                        self.post_compaction_tokens = int(u.get("input_tokens") or 0)
                        self._expecting_post_compaction = False
                    self._last_input_tokens_before_compaction = int(u.get("input_tokens") or 0)

                # request_id / retry detection
                rid = r.get("requestId") or msg.get("id") or ""
                if rid:
                    if rid in self.seen_request_ids:
                        self.retry_count += 1
                    else:
                        self.seen_request_ids.add(rid)

                # error detection
                stop = msg.get("stop_reason") or ""
                if stop in ("error", "refusal"):
                    self.api_error_count += 1

                # tool_use enumeration + per-tool aggregation.
                # Per-tool dur is the bundle wall-clock split evenly across N parallel tools;
                # solo tools (N=1) get the exact bundle wall as their dur.
                tool_uses = [c for c in (msg.get("content") or [])
                             if isinstance(c, dict) and c.get("type") == "tool_use"]
                if tool_uses:
                    # Find the bundle wall: max user_ts of any of this parent's tool_results.
                    bundle_user_ts: float | None = None
                    matched_results: list[tuple[dict, dict]] = []  # (tool_use, tool_result)
                    for tu in tool_uses:
                        pair = result_by_tuid.get(tu.get("id", ""))
                        if pair is None:
                            matched_results.append((tu, {}))  # unresolved (in-flight at window edge)
                            continue
                        tr, urts = pair
                        if bundle_user_ts is None or urts > bundle_user_ts:
                            bundle_user_ts = urts
                        matched_results.append((tu, tr))

                    if bundle_user_ts is not None:
                        bundle_wall = max(0.0, bundle_user_ts - ts)
                        parent_bundle_wall[r.get("uuid", "")] = bundle_wall
                        n = len(tool_uses)
                        per_share = bundle_wall / max(1, n)
                        is_solo = (n == 1)
                        for tu, tr in matched_results:
                            name = tu.get("name") or ""
                            tin = tu.get("input") or {}
                            ok = bool(tr) and not bool(tr.get("is_error", False))
                            bucket = classify_tool(name, tin)
                            self._bump_bucket(bucket, per_share)
                            self._record_tool(name, per_share, ok, exact=is_solo)

                            txt = tr.get("content") if tr else ""
                            if isinstance(txt, list):
                                txt = json.dumps(txt)
                            elif not isinstance(txt, str):
                                txt = str(txt or "")
                            self.tool_result_tokens_est += len(txt) // 4

                            if name in ("Edit", "Write", "MultiEdit") and ok:
                                added, removed = guess_edit_lines(name, tin)
                                self.lines_added += added
                                self.lines_removed += removed

                            if name in AGENT_TOOLS:
                                self.subagent_calls += 1

                            self.events.append({
                                "ts": fmt_ts(bundle_user_ts),
                                "kind": "tool_end",
                                "tool": name,
                                "bucket": bucket,
                                "dur_s": round(per_share, 3),
                                "exact": is_solo,
                                "ok": ok,
                                "tool_use_id": tu.get("id", ""),
                                "parent_msg_uuid": r.get("uuid", ""),
                            })

                        self.debug_turns += 1
                        self.tool_calls += len(tool_uses)

                last_ts, last_kind = ts, "assistant"
                continue

            if t == "user":
                content = msg.get("content")
                tool_results: list[dict] = []
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            tool_results.append(c)

                if tool_results:
                    # Per-tool aggregation already happened at the assistant-message stage
                    # above (we have tool_results pre-indexed). Here we only update the
                    # last_kind transition so api_time on the next assistant turn is right.
                    last_ts, last_kind = ts, "tool_result"
                else:
                    # Real user prompt
                    self.user_prompts += 1
                    if last_ts is not None and last_kind == "assistant":
                        gap = max(0.0, ts - last_ts)
                        if gap > self.idle_threshold:
                            self.idle_time += gap
                        else:
                            self.active_time_user += gap
                    self.events.append({
                        "ts": fmt_ts(ts), "kind": "user_prompt",
                        "msg_uuid": r.get("uuid", ""),
                    })
                    last_ts, last_kind = ts, "user_prompt"
                continue

            # other event types: permission-mode, file-history-snapshot, etc.
            # They're not first-class for our metrics but we keep raw rows.

        # tool_time_critical_path = sum of bundle wall-clocks across parent messages.
        # In transcript-only mode, sum and critical_path are equal (we attribute
        # bundle_wall / N to each tool, so totals reconcile to bundle_wall per parent).
        self.tool_time_critical_path = sum(parent_bundle_wall.values())

    def feed_marks(self, marks: list[dict]) -> None:
        for m in marks:
            self.events.append({
                "ts": fmt_ts(float(m["ts"])), "kind": "mark", "label": m["label"],
            })

    def feed_hook_events(self, hook_events: list[dict]) -> None:
        """Hook-emitted events override transcript-derived per-tool durations when
        present. v0 simply appends them to the event stream verbatim."""
        for ev in hook_events:
            self.events.append({"source": "hook", **ev})


def pick_bucket(buckets: list[str]) -> str:
    """Pick the highest-priority bucket present in a bundle of parallel tools."""
    priority = (
        "agent_dispatch", "checkpoint_dl", "dataset_dl", "docker_pull", "docker_build",
        "benchmark_run", "test", "download", "infra", "web", "mcp", "bash", "coding", "other",
    )
    seen = set(buckets)
    for b in priority:
        if b in seen:
            return b
    return buckets[0] if buckets else "other"


def guess_edit_lines(tool_name: str, tin: dict) -> tuple[int, int]:
    """Best-effort line count for Edit/Write/MultiEdit. Returns (added, removed)."""
    if tool_name == "Write":
        content = tin.get("content") or ""
        return content.count("\n"), 0
    if tool_name == "Edit":
        new = (tin.get("new_string") or "").count("\n")
        old = (tin.get("old_string") or "").count("\n")
        return new, old
    if tool_name == "MultiEdit":
        added = removed = 0
        for e in tin.get("edits") or []:
            added += (e.get("new_string") or "").count("\n")
            removed += (e.get("old_string") or "").count("\n")
        return added, removed
    return 0, 0


# ---------------------------------------------------------------------------
# Window state (persisted .json)
# ---------------------------------------------------------------------------

def make_window_id(name: str, start_ts: float) -> str:
    safe = re.sub(r"[^a-z0-9-]+", "-", (name or "run").lower()).strip("-") or "run"
    stamp = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}__{safe}__{uuid.uuid4().hex[:6]}"


def git_metadata(cwd: Path) -> dict:
    out = {"sha": None, "dirty": None, "branch": None}
    if not (cwd / ".git").exists() and not shutil.which("git"):
        return out
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd,
                                      stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
        out["sha"] = sha or None
    except Exception:
        pass
    try:
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=cwd,
                                         stderr=subprocess.DEVNULL, text=True, timeout=2)
        out["dirty"] = bool(status.strip())
    except Exception:
        pass
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd,
                                         stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
        out["branch"] = branch or None
    except Exception:
        pass
    return out


def env_snapshot() -> dict:
    keep = {}
    for k, v in os.environ.items():
        if k.startswith(("CLAUDE_", "ANTHROPIC_", "OTEL_")):
            # Redact anything that looks like a key/token.
            if any(s in k for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                keep[k] = "<redacted>"
            else:
                keep[k] = v
    return keep


def load_active(session_key: str | None = None) -> dict | None:
    p = active_pointer(session_key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_active(state: dict, session_key: str | None = None) -> None:
    p = active_pointer(session_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def clear_active(session_key: str | None = None) -> None:
    p = active_pointer(session_key)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def report_dict(state: dict, agg: Aggregator, end_ts: float) -> dict:
    wall = max(0.0, end_ts - state["start_ts"])
    active_cli = agg.api_time_sum + sum(agg.bucket_time.values())
    # Active total = api_time + tool_time + user-thinking; idle separate.
    return {
        "version": 1,
        "name": state.get("name"),
        "tags": state.get("tags") or {},
        "note": state.get("note"),
        "marks": state.get("marks") or [],
        "session_id": state.get("session_id"),
        "transcript_path": state.get("transcript_path"),
        "cwd": state.get("cwd"),
        "git": state.get("git"),
        "env": state.get("env"),
        "started_at": fmt_ts(state["start_ts"]),
        "stopped_at": fmt_ts(end_ts),
        "wall_time_s": round(wall, 3),
        "active_time_user_s": round(agg.active_time_user, 3),
        "active_time_cli_s": round(active_cli, 3),
        "idle_or_wait_time_s": round(agg.idle_time, 3),
        "api_time_sum_s": round(agg.api_time_sum, 3),
        "api_time_critical_path_s": round(agg.api_time_sum, 3),  # transcript-only: equal
        "api_time_by_model_s": {k: round(v, 3) for k, v in agg.api_time_by_model.items()},
        "api_time_by_query_source_s": {k: round(v, 3) for k, v in agg.api_time_by_query_source.items()},
        "api_call_count": agg.api_call_count,
        "api_error_count": agg.api_error_count,
        "retry_count": agg.retry_count,
        "tool_time_sum_s": round(sum(agg.bucket_time.values()), 3),
        "tool_time_critical_path_s": round(agg.tool_time_critical_path, 3),
        "tool_time_by_bucket_s": {k: round(v, 3) for k, v in agg.bucket_time.items()},
        "tool_time_by_tool_s": {k: round(v, 3) for k, v in agg.per_tool_time.items()},
        "tool_time_by_tool_note": (
            "Solo tools (1 tool_use per assistant message) get their exact bundle "
            "wall-time. Parallel tools (N>1 in one message) share bundle_wall / N — "
            "individual durations of parallel tools are not recoverable from the "
            "transcript. Hook events would give per-tool start/end."
        ),
        "tool_count_by_tool": dict(agg.per_tool_count),
        "tool_exact_count_by_tool": dict(agg.per_tool_exact_count),
        "tool_approx_count_by_tool": dict(agg.per_tool_approx_count),
        "tool_success_count_by_tool": dict(agg.per_tool_success),
        "tool_failure_count_by_tool": dict(agg.per_tool_failure),
        "bash_time_s": round(agg.bucket_time.get("bash", 0.0), 3),
        "docker_pull_time_s": round(agg.bucket_time.get("docker_pull", 0.0), 3),
        "docker_build_time_s": round(agg.bucket_time.get("docker_build", 0.0), 3),
        "dataset_download_time_s": round(agg.bucket_time.get("dataset_dl", 0.0), 3),
        "checkpoint_download_time_s": round(agg.bucket_time.get("checkpoint_dl", 0.0), 3),
        "benchmark_run_time_s": round(agg.bucket_time.get("benchmark_run", 0.0), 3),
        "test_time_s": round(agg.bucket_time.get("test", 0.0), 3),
        "tool_result_tokens_est": agg.tool_result_tokens_est,
        "total_input_tokens": agg.input_tokens,
        "total_output_tokens": agg.output_tokens,
        "cache_read_tokens": agg.cache_read_tokens,
        "cache_creation_tokens": agg.cache_creation_tokens,
        "estimated_token_cost_usd": round(agg.cost_usd, 4),
        "cost_uncovered_models": sorted(agg.cost_uncovered_models),
        "cost_disclaimer": "estimated from per-turn usage and a static price table; not a billing source of truth",
        "models": sorted(agg.models),
        "turns": agg.assistant_turns,
        "user_prompts": agg.user_prompts,
        "debug_turns": agg.debug_turns,
        "tool_calls": agg.tool_calls,
        "subagent_calls": agg.subagent_calls,
        "sidechain_turns": agg.sidechain_turns,
        "compaction_count": agg.compaction_count,
        "compaction_time_s": round(agg.compaction_time, 3),
        "pre_compaction_tokens": agg._last_input_tokens_before_compaction,
        "post_compaction_tokens": agg.post_compaction_tokens,
        "lines_added": agg.lines_added,
        "lines_removed": agg.lines_removed,
    }


def fmt_dur(s: float) -> str:
    s = float(s or 0.0)
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{int(s // 60)}m{int(s % 60):02d}s"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h}h{m:02d}m"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1e6:.2f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}k"
    return str(n)


def render_table(rep: dict) -> str:
    L = []
    L.append(f"╭─ profile: {rep.get('name') or '(unnamed)'} "
             f"[{rep.get('session_id','')[:8]}]  {fmt_dur(rep['wall_time_s'])} wall")
    if rep.get("note"):
        L.append(f"│  note: {rep['note']}")
    if rep.get("marks"):
        L.append(f"│  marks: {len(rep['marks'])}")
    L.append("├─ time")
    L.append(f"│  wall              {fmt_dur(rep['wall_time_s']):>10}")
    L.append(f"│  api time          {fmt_dur(rep['api_time_sum_s']):>10}")
    L.append(f"│  tool time sum     {fmt_dur(rep['tool_time_sum_s']):>10}")
    L.append(f"│  tool time wall    {fmt_dur(rep['tool_time_critical_path_s']):>10}  (critical path; sum > wall = parallel)")
    L.append(f"│  user-thinking     {fmt_dur(rep['active_time_user_s']):>10}")
    L.append(f"│  idle/wait         {fmt_dur(rep['idle_or_wait_time_s']):>10}")
    if rep["api_time_by_model_s"]:
        L.append("├─ api time by model")
        for m, v in sorted(rep["api_time_by_model_s"].items(), key=lambda x: -x[1]):
            L.append(f"│  {m:<28} {fmt_dur(v):>10}")
    if rep["api_time_by_query_source_s"]:
        for src, v in rep["api_time_by_query_source_s"].items():
            L.append(f"│  ({src}){' '*(26-len(src))} {fmt_dur(v):>10}")
    if rep["tool_time_by_bucket_s"]:
        L.append("├─ tool time by bucket")
        for b, v in sorted(rep["tool_time_by_bucket_s"].items(), key=lambda x: -x[1]):
            count = sum(1 for _ in rep["tool_count_by_tool"])  # bucket count not tracked separately
            L.append(f"│  {b:<28} {fmt_dur(v):>10}")
    # top-5 tools
    tt = rep["tool_time_by_tool_s"]
    if tt:
        L.append("├─ top tools  (exact = solo bundle, ~ = N-way parallel split)")
        for name, v in sorted(tt.items(), key=lambda x: -x[1])[:5]:
            cnt = rep["tool_count_by_tool"].get(name, 0)
            ex = rep["tool_exact_count_by_tool"].get(name, 0)
            ap = rep["tool_approx_count_by_tool"].get(name, 0)
            ok = rep["tool_success_count_by_tool"].get(name, 0)
            fail = rep["tool_failure_count_by_tool"].get(name, 0)
            tag = "" if ap == 0 else f" (~{ap}/{cnt})"
            L.append(f"│  {name:<18} {fmt_dur(v):>8}  n={cnt}  ok={ok}  fail={fail}{tag}")
    L.append("├─ tokens")
    L.append(f"│  input             {fmt_tok(rep['total_input_tokens']):>10}")
    L.append(f"│  output            {fmt_tok(rep['total_output_tokens']):>10}")
    L.append(f"│  cache write       {fmt_tok(rep['cache_creation_tokens']):>10}")
    L.append(f"│  cache read        {fmt_tok(rep['cache_read_tokens']):>10}")
    L.append(f"│  tool result (est) {fmt_tok(rep['tool_result_tokens_est']):>10}")
    L.append("├─ cost")
    cost = rep["estimated_token_cost_usd"]
    L.append(f"│  estimated         ${cost:>9.4f}  (not billing truth)")
    if rep["cost_uncovered_models"]:
        L.append(f"│  ! uncovered models: {', '.join(rep['cost_uncovered_models'])}")
    L.append("├─ turns / errors")
    L.append(f"│  assistant turns  {rep['turns']:>11d}")
    L.append(f"│    debug (w/tool) {rep['debug_turns']:>11d}")
    L.append(f"│    sidechain      {rep['sidechain_turns']:>11d}")
    L.append(f"│  user prompts     {rep['user_prompts']:>11d}")
    L.append(f"│  tool calls       {rep['tool_calls']:>11d}")
    L.append(f"│  subagent calls   {rep['subagent_calls']:>11d}")
    L.append(f"│  api errors       {rep['api_error_count']:>11d}")
    L.append(f"│  retries          {rep['retry_count']:>11d}")
    L.append(f"│  compactions      {rep['compaction_count']:>11d}")
    if rep["compaction_count"]:
        L.append(f"│    pre-tokens     {fmt_tok(rep['pre_compaction_tokens']):>11}")
        L.append(f"│    post-tokens    {fmt_tok(rep['post_compaction_tokens']):>11}")
    if rep["lines_added"] or rep["lines_removed"]:
        L.append(f"│  lines +{rep['lines_added']} / -{rep['lines_removed']}")
    L.append(f"╰─ artifacts under: {rep.get('_window_dir', '?')}")
    return "\n".join(L)


def render_markdown(rep: dict) -> str:
    L = [f"# Profile — {rep.get('name') or '(unnamed)'}", ""]
    L.append(f"- **Session**: `{rep.get('session_id','')}`  ")
    L.append(f"- **Started**: {rep['started_at']}  ")
    L.append(f"- **Stopped**: {rep['stopped_at']}  ")
    L.append(f"- **Wall**: {fmt_dur(rep['wall_time_s'])}  ")
    if rep.get("note"):
        L.append(f"- **Note**: {rep['note']}")
    if rep.get("git", {}).get("sha"):
        g = rep["git"]
        dirty = " *(dirty)*" if g.get("dirty") else ""
        L.append(f"- **Git**: `{g.get('sha','')[:8]}` on `{g.get('branch','?')}`{dirty}")
    L.append("")
    L.append("## Time")
    L.append("| metric | value |")
    L.append("|---|---|")
    L.append(f"| wall | {fmt_dur(rep['wall_time_s'])} |")
    L.append(f"| api time (sum / critical path) | {fmt_dur(rep['api_time_sum_s'])} / {fmt_dur(rep['api_time_critical_path_s'])} |")
    L.append(f"| tool time (sum / critical path) | {fmt_dur(rep['tool_time_sum_s'])} / {fmt_dur(rep['tool_time_critical_path_s'])} |")
    L.append(f"| user-thinking | {fmt_dur(rep['active_time_user_s'])} |")
    L.append(f"| idle/wait | {fmt_dur(rep['idle_or_wait_time_s'])} |")
    if rep["api_time_by_model_s"]:
        L.append("\n## API time by model")
        L.append("| model | time |")
        L.append("|---|---|")
        for m, v in sorted(rep["api_time_by_model_s"].items(), key=lambda x: -x[1]):
            L.append(f"| `{m}` | {fmt_dur(v)} |")
    if rep["tool_time_by_bucket_s"]:
        L.append("\n## Tool time by bucket")
        L.append("| bucket | time |")
        L.append("|---|---|")
        for b, v in sorted(rep["tool_time_by_bucket_s"].items(), key=lambda x: -x[1]):
            L.append(f"| {b} | {fmt_dur(v)} |")
    L.append("\n## Tools")
    L.append("| tool | time | n | exact | parallel | ok | fail |")
    L.append("|---|---|---|---|---|---|---|")
    for name, v in sorted(rep["tool_time_by_tool_s"].items(), key=lambda x: -x[1]):
        L.append(f"| {name} | {fmt_dur(v)} | "
                 f"{rep['tool_count_by_tool'].get(name,0)} | "
                 f"{rep['tool_exact_count_by_tool'].get(name,0)} | "
                 f"{rep['tool_approx_count_by_tool'].get(name,0)} | "
                 f"{rep['tool_success_count_by_tool'].get(name,0)} | "
                 f"{rep['tool_failure_count_by_tool'].get(name,0)} |")
    L.append("\n*`exact` = solo tool calls (1 tool per assistant message; dur is exact). "
             "`parallel` = part of an N-way parallel batch (dur ≈ bundle_wall / N; "
             "individual durations not recoverable from transcript). Wire hooks for exact per-tool times.*")
    L.append("\n## Tokens & cost")
    L.append("| | tokens |")
    L.append("|---|---|")
    L.append(f"| input | {rep['total_input_tokens']:,} |")
    L.append(f"| output | {rep['total_output_tokens']:,} |")
    L.append(f"| cache write | {rep['cache_creation_tokens']:,} |")
    L.append(f"| cache read | {rep['cache_read_tokens']:,} |")
    L.append(f"| tool result (est) | {rep['tool_result_tokens_est']:,} |")
    L.append(f"\n**Estimated cost**: `${rep['estimated_token_cost_usd']:.4f}` "
             f"_(not billing truth)_")
    if rep["cost_uncovered_models"]:
        L.append(f"\n> ⚠ Uncovered models (no price table entry): "
                 f"{', '.join('`' + m + '`' for m in rep['cost_uncovered_models'])}")
    L.append(f"\n## Turns")
    L.append(f"- assistant turns: {rep['turns']}  (debug: {rep['debug_turns']}, sidechain: {rep['sidechain_turns']})")
    L.append(f"- user prompts: {rep['user_prompts']}")
    L.append(f"- tool calls: {rep['tool_calls']}  (subagent: {rep['subagent_calls']})")
    L.append(f"- api errors: {rep['api_error_count']}, retries: {rep['retry_count']}")
    L.append(f"- compactions: {rep['compaction_count']}")
    if rep['compaction_count']:
        L.append(f"  - pre-compaction tokens: {rep['pre_compaction_tokens']:,}")
        L.append(f"  - post-compaction tokens: {rep['post_compaction_tokens']:,}")
    if rep['lines_added'] or rep['lines_removed']:
        L.append(f"- lines +{rep['lines_added']} / -{rep['lines_removed']}")
    if rep.get("marks"):
        L.append("\n## Marks")
        for m in rep["marks"]:
            L.append(f"- `{fmt_ts(float(m['ts']))}` — {m['label']}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_start(args) -> int:
    if load_active() is not None:
        sys.stderr.write("claude-code-profiler: a window is already active. "
                         "Use `claude-code-profiler stop` or `claude-code-profiler reset` first.\n")
        return 1

    cwd = Path.cwd()
    start_ts = time.time()
    name = args.name or "run"
    window_id = make_window_id(name, start_ts)

    # transcript discovery
    sess_env = os.environ.get("CLAUDE_SESSION_ID")
    found = discover_transcript(cwd, prefer_session=sess_env)
    if found is None:
        sys.stderr.write(
            f"claude-code-profiler: no transcript found under {PROJECTS_DIR}/{cwd_to_project_slug(cwd)}/.\n"
            "Are you running from inside a Claude Code session in this cwd?\n"
        )
        return 2
    transcript_path, session_id = found

    # initial model — peek the most recent assistant turn
    model = ""
    for r in iter_jsonl(transcript_path):
        if r.get("type") == "assistant":
            model = ((r.get("message") or {}).get("model")) or model

    win_dir = windows_dir() / window_id
    win_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "window_id": window_id,
        "name": name,
        "tags": dict(t.split("=", 1) for t in (args.tag or []) if "=" in t),
        "note": args.note,
        "marks": [],
        "start_ts": start_ts,
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "cwd": str(cwd),
        "model_at_start": model,
        "git": git_metadata(cwd),
        "env": env_snapshot(),
        "window_dir": str(win_dir),
    }
    (win_dir / "state.json").write_text(json.dumps(state, indent=2))
    save_active(state)

    print(f"claude-code-profiler: started '{name}' [{window_id}]")
    print(f"  session: {session_id}")
    print(f"  transcript: {transcript_path}")
    print(f"  artifacts: {win_dir}")
    if model:
        print(f"  model at start: {model}")
    if state["tags"]:
        print(f"  tags: {state['tags']}")
    if args.note:
        print(f"  note: {args.note}")
    return 0


def cmd_status(args) -> int:
    state = load_active()
    if state is None:
        print("claude-code-profiler: no active window.")
        return 0
    elapsed = time.time() - state["start_ts"]
    print(f"claude-code-profiler: active '{state['name']}' [{state['window_id']}]")
    print(f"  started: {fmt_ts(state['start_ts'])}  ({fmt_dur(elapsed)} ago)")
    print(f"  session: {state['session_id']}")
    print(f"  transcript: {state['transcript_path']}")
    if state.get("marks"):
        print(f"  marks ({len(state['marks'])}):")
        for m in state["marks"][-10:]:
            print(f"    [{fmt_ts(float(m['ts']))}] {m['label']}")

    # Live preview: count of turns since start
    rows = list(iter_jsonl(Path(state["transcript_path"])))
    n_assist = sum(1 for r in rows
                   if r.get("type") == "assistant"
                   and (parse_ts(r.get("timestamp")) or 0) >= state["start_ts"])
    n_user = sum(1 for r in rows
                 if r.get("type") == "user"
                 and (parse_ts(r.get("timestamp")) or 0) >= state["start_ts"]
                 and not (isinstance((r.get("message") or {}).get("content"), list)
                          and all(isinstance(c, dict) and c.get("type") == "tool_result"
                                  for c in (r.get("message") or {}).get("content"))))
    print(f"  so far: assistant_turns={n_assist}  user_prompts={n_user}")
    return 0


def cmd_mark(args) -> int:
    state = load_active()
    if state is None:
        sys.stderr.write("claude-code-profiler: no active window. Run `claude-code-profiler start` first.\n")
        return 1
    label = args.label or ""
    state.setdefault("marks", []).append({"ts": time.time(), "label": label})
    save_active(state)
    win_dir = Path(state["window_dir"])
    (win_dir / "state.json").write_text(json.dumps(state, indent=2))
    print(f"claude-code-profiler: marked [{label}]")
    return 0


def cmd_stop(args) -> int:
    state = load_active()
    if state is None:
        sys.stderr.write("claude-code-profiler: no active window.\n")
        return 1

    end_ts = time.time()
    transcript_path = Path(state["transcript_path"])
    rows = list(iter_jsonl(transcript_path))

    prices = parse_prices_override(args.prices) or DEFAULT_PRICES
    agg = Aggregator(start_ts=state["start_ts"], end_ts=end_ts, prices=prices)
    agg.feed_transcript(rows)
    agg.feed_marks(state.get("marks") or [])

    # Hook events (optional): if events.jsonl already exists in window dir,
    # ingest. (Hooks themselves write there directly when configured.)
    win_dir = Path(state["window_dir"])
    hook_events_path = win_dir / "events.from_hooks.jsonl"
    if hook_events_path.is_file():
        agg.feed_hook_events(list(iter_jsonl(hook_events_path)))

    rep = report_dict(state, agg, end_ts)
    rep["_window_dir"] = str(win_dir)

    # Persist artifacts
    (win_dir / "profile.json").write_text(json.dumps(rep, indent=2, sort_keys=True))
    (win_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in agg.events) + ("\n" if agg.events else "")
    )
    (win_dir / "transcript.snippet.jsonl").write_text(
        "\n".join(json.dumps(r) for r in agg.raw_rows) + ("\n" if agg.raw_rows else "")
    )
    md = render_markdown(rep)
    (win_dir / "profile.md").write_text(md)

    # Render to chosen format
    if args.format == "json":
        print(json.dumps(rep, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(md)
    else:
        print(render_table(rep))

    # Optional export
    if args.export:
        dest = Path(args.export).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / win_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(win_dir, target)
        print(f"\nclaude-code-profiler: exported to {target}")

    clear_active()
    return 0


def cmd_reset(args) -> int:
    state = load_active()
    if state is None:
        print("claude-code-profiler: no active window to reset.")
        return 0
    print(f"claude-code-profiler: discarded active window '{state['name']}' [{state['window_id']}]")
    print(f"  artifacts at {state['window_dir']} are kept (delete manually if not needed)")
    clear_active()
    return 0


def cmd_hook(args) -> int:
    """Append a normalized hook event to the active window's hook log.
    Inert (exit 0) when no window is active — hooks must be safe to leave installed."""
    state = load_active()
    if state is None:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    win_dir = Path(state["window_dir"])
    win_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": fmt_ts(time.time()),
        "kind": f"hook:{args.event}",
        "payload": payload,
    }
    with (win_dir / "events.from_hooks.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-code-profiler", description="Profile a Claude Code session window.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="Begin a profiling window")
    sp.add_argument("name", nargs="?", default=None)
    sp.add_argument("--tag", action="append", default=[],
                    help="key=value tag; repeatable")
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("status", help="Show the active window")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("mark", help="Append a labeled timestamp to the active window")
    sp.add_argument("label", nargs="?", default="")
    sp.set_defaults(func=cmd_mark)

    sp = sub.add_parser("stop", help="Stop the active window and emit a report")
    sp.add_argument("--format", choices=("table", "markdown", "json"), default="table")
    sp.add_argument("--export", default=None,
                    help="Copy the window artifacts to this directory after stop")
    sp.add_argument("--prices", default=None,
                    help="Override price table, e.g. 'opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3'")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("reset", help="Discard the active window without producing a report")
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("_hook", help=argparse.SUPPRESS)
    sp.add_argument("event")
    sp.set_defaults(func=cmd_hook)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
