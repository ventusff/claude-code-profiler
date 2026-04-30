"""Shared fixtures for cc_profiler tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
