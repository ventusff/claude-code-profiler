"""Shared fixtures for cc_profiler tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from _synth import Transcript, parent_path, subagent_path, write_subagent_meta

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cc_profiler.py"


def _slug(cwd: Path) -> str:
    s = str(cwd.resolve())
    return s.replace("/", "-").replace("_", "-").replace(".", "-")


def _make_transcript(claude_home: Path, cwd: Path, session_id: str) -> Path:
    """Write a minimal valid transcript JSONL for `cwd` under a fake CLAUDE_CONFIG_DIR.

    Includes one assistant turn so model-detection at `start` succeeds.
    """
    project_dir = claude_home / "projects" / _slug(cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{session_id}.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "u1",
                "timestamp": "2026-04-30T00:00:00Z",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-4-7",
                    "content": [],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        + "\n"
    )
    return p


@pytest.fixture
def env_factory(tmp_path):
    """Return a callable that builds an env dict for one simulated Claude session.

    Each invocation yields a fresh env that points the profiler at an isolated
    state dir + fake claude_home, and tags it with a distinct session_key. Both
    sessions share the same `cwd` (the realistic case the user reported).
    """
    state_home = tmp_path / "state"
    claude_home = tmp_path / "claude"
    cwd = tmp_path / "proj"
    cwd.mkdir()

    def _build(session_key: str, session_id: str) -> dict[str, str]:
        _make_transcript(claude_home, cwd, session_id)
        env = {
            **os.environ,
            "XDG_STATE_HOME": str(state_home),
            "CLAUDE_CONFIG_DIR": str(claude_home),
            "CLAUDE_PROFILER_SESSION_KEY": session_key,
        }
        # Don't inherit a real CLAUDE_SESSION_ID from the host shell.
        env.pop("CLAUDE_SESSION_ID", None)
        return env

    _build.cwd = cwd  # type: ignore[attr-defined]
    _build.state_home = state_home  # type: ignore[attr-defined]
    _build.claude_home = claude_home  # type: ignore[attr-defined]
    return _build


@pytest.fixture
def run_cli(env_factory):
    """Return a callable to invoke cc_profiler.py via subprocess."""

    def _run(args: list[str], env: dict[str, str], check: bool = False):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=env,
            cwd=str(env_factory.cwd),  # type: ignore[attr-defined]
            capture_output=True,
            text=True,
        )
        if check and r.returncode != 0:
            raise AssertionError(
                f"cc_profiler {args} failed (rc={r.returncode}):\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}"
            )
        return r

    return _run


@dataclass
class BuiltWindow:
    """Handle returned by `built_window` — one prepared profiling window.

    The window is already started, with `start_ts` patched to 0 so test rows
    with arbitrary past timestamps fall inside the window. Caller writes
    transcripts via `parent` / `sub(agent_id)`, then calls `stop()` to get the
    JSON report.
    """

    env: dict[str, str]
    cwd: Path
    parent_jsonl: Path
    state_json: Path
    parent: Transcript
    _run_cli: callable
    _subs: list[tuple[str, Transcript]] = field(default_factory=list)

    def sub(self, agent_id: str, agent_type: str = "Explore") -> Transcript:
        """Get (or create) a Transcript builder for a subagent file."""
        for aid, t in self._subs:
            if aid == agent_id:
                return t
        sub_jsonl = subagent_path(self.parent_jsonl, agent_id)
        write_subagent_meta(sub_jsonl, agent_type=agent_type)
        t = Transcript(path=sub_jsonl)
        self._subs.append((agent_id, t))
        return t

    def stop(self) -> dict:
        """Flush all transcripts to disk and run `stop --format=json`. Returns the report dict."""
        self.parent.write()
        for _aid, t in self._subs:
            t.write()
        r = self._run_cli(["stop", "--format=json"], env=self.env)
        if r.returncode != 0:
            raise AssertionError(
                f"stop failed (rc={r.returncode}):\nstdout: {r.stdout}\nstderr: {r.stderr}"
            )
        return json.loads(r.stdout)


@pytest.fixture
def built_window(env_factory, run_cli):
    """Set up + start a profiling window, with `start_ts` patched to 0.

    Returns a `BuiltWindow` whose `parent` is a fresh `Transcript` builder
    targeting the parent JSONL file (so writing it overwrites the seed row
    `_make_transcript` placed there). Use `.sub(agent_id)` to get builders for
    subagent files. Call `.stop()` to flush + collect the report.
    """

    def _build(session_key: str = "default", session_id: str = "11111111-aaaa-aaaa-aaaa-111111111111") -> BuiltWindow:
        env = env_factory(session_key, session_id)

        # Run start so the active pointer + window dir get created.
        r = run_cli(["start", "test-window"], env=env)
        if r.returncode != 0:
            raise AssertionError(f"start failed: {r.stderr}")

        # Locate the active pointer for this session_key and patch start_ts=0.
        # That way, transcript rows with our anchored 2026-01-01 timestamps fall
        # within the window even though `start_ts = time.time()` originally.
        state_home = env_factory.state_home  # type: ignore[attr-defined]
        active_dir = state_home / "claude-code-profiler" / "active"
        actives = list(active_dir.glob("*.json"))
        assert actives, f"no active pointer found under {active_dir}"
        # Pick the one matching this session_key (sanitization is conservative).
        ptr_path = actives[0] if len(actives) == 1 else next(
            p for p in actives if session_key.replace("/", "_") in p.name
        )
        state = json.loads(ptr_path.read_text())
        state["start_ts"] = 0.0
        ptr_path.write_text(json.dumps(state))
        # Mirror to the per-window state.json the cmd_start wrote.
        win_state = Path(state["window_dir"]) / "state.json"
        if win_state.is_file():
            ws = json.loads(win_state.read_text())
            ws["start_ts"] = 0.0
            win_state.write_text(json.dumps(ws))

        cwd = env_factory.cwd  # type: ignore[attr-defined]
        claude_home = env_factory.claude_home  # type: ignore[attr-defined]
        parent_jsonl = parent_path(claude_home, cwd, session_id)
        parent_builder = Transcript(path=parent_jsonl)

        return BuiltWindow(
            env=env,
            cwd=cwd,
            parent_jsonl=parent_jsonl,
            state_json=ptr_path,
            parent=parent_builder,
            _run_cli=run_cli,
        )

    return _build
