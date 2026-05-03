"""Regression tests for the concurrent-/profile bug.

Two Claude Code sessions on the same machine share `~/.local/state/claude-code-profiler/`.
Before the fix, both sessions wrote to a single `active.json`, so the second
session's `/profile start` was rejected with "a window is already active".

These tests simulate two independent sessions via `CLAUDE_PROFILER_SESSION_KEY`
(a test-friendly override of the auto-detected per-session key) and verify that
each session's start/status/mark/stop/reset only touches its own window.
"""
from __future__ import annotations

import json


def _start(run_cli, env, name):
    r = run_cli(["start", name], env=env)
    assert r.returncode == 0, f"start({name!r}) failed: {r.stderr}"
    return r


def _status(run_cli, env):
    r = run_cli(["status"], env=env)
    assert r.returncode == 0, f"status failed: {r.stderr}"
    return r


def _stop(run_cli, env):
    r = run_cli(["stop", "--format=json"], env=env)
    return r


def test_two_sessions_can_start_concurrently(env_factory, run_cli):
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")
    env_b = env_factory("session-B", "22222222-bbbb-bbbb-bbbb-222222222222")

    rA = run_cli(["start", "winA"], env=env_a)
    assert rA.returncode == 0, f"session A start failed: {rA.stderr}"

    rB = run_cli(["start", "winB"], env=env_b)
    assert rB.returncode == 0, (
        "session B start should not be blocked by session A's active window. "
        f"stderr={rB.stderr!r}"
    )


def test_status_isolated_per_session(env_factory, run_cli):
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")
    env_b = env_factory("session-B", "22222222-bbbb-bbbb-bbbb-222222222222")

    _start(run_cli, env_a, "winA")
    _start(run_cli, env_b, "winB")

    sA = _status(run_cli, env_a).stdout
    sB = _status(run_cli, env_b).stdout

    assert "winA" in sA and "winB" not in sA, f"session A status leaked: {sA}"
    assert "winB" in sB and "winA" not in sB, f"session B status leaked: {sB}"


def test_mark_isolated_per_session(env_factory, run_cli):
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")
    env_b = env_factory("session-B", "22222222-bbbb-bbbb-bbbb-222222222222")

    _start(run_cli, env_a, "winA")
    _start(run_cli, env_b, "winB")

    rA = run_cli(["mark", "phase-a"], env=env_a)
    assert rA.returncode == 0, rA.stderr

    sB = _status(run_cli, env_b).stdout
    assert "phase-a" not in sB, f"session A's mark leaked into session B: {sB}"

    sA = _status(run_cli, env_a).stdout
    assert "phase-a" in sA, f"session A's mark missing from its own status: {sA}"


def test_stop_isolated_per_session(env_factory, run_cli):
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")
    env_b = env_factory("session-B", "22222222-bbbb-bbbb-bbbb-222222222222")

    _start(run_cli, env_a, "winA")
    _start(run_cli, env_b, "winB")

    rA = _stop(run_cli, env_a)
    assert rA.returncode == 0, rA.stderr
    rep_a = json.loads(rA.stdout)
    assert rep_a["name"] == "winA"

    sB = _status(run_cli, env_b).stdout
    assert "winB" in sB, (
        "session B's window should still be active after session A stopped. "
        f"status output: {sB}"
    )

    rB = _stop(run_cli, env_b)
    assert rB.returncode == 0, rB.stderr
    rep_b = json.loads(rB.stdout)
    assert rep_b["name"] == "winB"


def test_reset_isolated_per_session(env_factory, run_cli):
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")
    env_b = env_factory("session-B", "22222222-bbbb-bbbb-bbbb-222222222222")

    _start(run_cli, env_a, "winA")
    _start(run_cli, env_b, "winB")

    rA = run_cli(["reset"], env=env_a)
    assert rA.returncode == 0, rA.stderr

    sA = _status(run_cli, env_a).stdout
    assert "no active profile" in sA.lower()

    sB = _status(run_cli, env_b).stdout
    assert "winB" in sB, f"reset on A should not touch B. B status: {sB}"


def test_starting_twice_in_same_session_is_still_rejected(env_factory, run_cli):
    """The original guard ('one active window per session') still applies within
    a single session — this is intentional and not what the bug report is about."""
    env_a = env_factory("session-A", "11111111-aaaa-aaaa-aaaa-111111111111")

    r1 = run_cli(["start", "first"], env=env_a)
    assert r1.returncode == 0, r1.stderr

    r2 = run_cli(["start", "second"], env=env_a)
    assert r2.returncode != 0, (
        "starting a second window in the SAME session should still fail — "
        f"got rc={r2.returncode}, stdout={r1.stdout!r}, stderr={r2.stderr!r}"
    )
    assert "already active" in r2.stderr.lower()
