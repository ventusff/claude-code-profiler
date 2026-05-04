"""Tests for the `retro` subcommand — retroactive profiling without prior `start`.

`retro` shares the aggregation+emit body with `stop`, so we don't re-test
metric correctness here; we only verify the retro-specific surface:

  - default behavior (no args) covers the whole session transcript
  - `--since` / `--until` parsing (relative duration, ISO, literals)
  - active-profile pointer is not disturbed
  - error paths (no transcript, since>=until)
  - bundle is self-describing (`mode: "retroactive"`)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _synth import Transcript, parent_path


def _seed_transcript(factory, session_id: str, anchor: datetime) -> tuple[Path, Transcript]:
    """Build a Transcript pointed at the path Claude Code would use for `session_id`.

    `factory` is the `env_factory` fixture itself (the callable with `.cwd` /
    `.claude_home` attributes attached by conftest), NOT the env dict it
    produces.
    """
    cwd = factory.cwd
    claude_home = factory.claude_home
    p = parent_path(claude_home, cwd, session_id)
    t = Transcript(path=p, anchor=anchor)
    return p, t


def _retro(run_cli, env, *args: str):
    """Run `retro --format=json …` and parse stdout. Raises if rc != 0."""
    r = run_cli(["retro", "--format=json", *args], env=env)
    if r.returncode != 0:
        raise AssertionError(
            f"retro {args} failed (rc={r.returncode}):\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------
# Default window covers the whole transcript
# ---------------------------------------------------------------------------

def test_retro_default_covers_full_transcript(env_factory, run_cli):
    """`retro` with no args → since=first, until=now: every transcript row is counted."""
    env = env_factory("retro-default", "11111111-aaaa-aaaa-aaaa-111111111111")
    # Anchor the transcript at "5 minutes ago" so until=now sweeps everything in.
    anchor = datetime.now(timezone.utc) - timedelta(minutes=5)
    p, t = _seed_transcript(env_factory, "11111111-aaaa-aaaa-aaaa-111111111111", anchor)
    t.assistant_text("hello 1")
    t.assistant_text("hello 2")
    t.user_prompt("ask")
    t.assistant_text("hello 3")
    t.write()

    rep = _retro(run_cli, env)
    assert rep["turns"] == 3, rep
    assert rep["user_prompts"] == 1, rep
    assert rep.get("mode") == "retroactive", rep
    # window_dir was created
    assert Path(rep["_window_dir"]).is_dir()
    # state.json exists in the window dir and carries mode=retroactive
    state = json.loads((Path(rep["_window_dir"]) / "state.json").read_text())
    assert state["mode"] == "retroactive"
    assert state["since"] == "first"
    assert state["until"] == "now"


# ---------------------------------------------------------------------------
# --since with a relative duration filters the window
# ---------------------------------------------------------------------------

def test_retro_since_relative_filters_old_rows(env_factory, run_cli):
    """`retro --since 5m` should drop rows older than 5m, keep newer ones."""
    env = env_factory("retro-relative", "22222222-aaaa-aaaa-aaaa-222222222222")
    now = datetime.now(timezone.utc)

    # Build a transcript where the first 3 rows land ~10m ago and the next 3 land ~30s ago.
    p, old = _seed_transcript(env_factory, "22222222-aaaa-aaaa-aaaa-222222222222",
                              anchor=now - timedelta(minutes=10))
    old.assistant_text("ancient 1")
    old.assistant_text("ancient 2")
    old.assistant_text("ancient 3")
    # Switch the builder's anchor by appending recent rows via a second Transcript
    # writing to the same path. Simplest: extend `old.rows` directly with rows
    # produced by a "recent" builder, then write once.
    recent = Transcript(path=p, anchor=now - timedelta(seconds=30))
    recent.assistant_text("fresh 1")
    recent.assistant_text("fresh 2")
    recent.assistant_text("fresh 3")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in (*old.rows, *recent.rows)) + "\n")

    rep = _retro(run_cli, env, "--since", "5m")
    # Only the 3 recent assistant rows fall inside [now-5m, now]
    assert rep["turns"] == 3, rep


# ---------------------------------------------------------------------------
# `--since last-prompt` anchors at the most recent real user prompt
# ---------------------------------------------------------------------------

def test_retro_since_last_prompt(env_factory, run_cli):
    """`--since last-prompt` should start the window at the most recent user prompt."""
    env = env_factory("retro-last-prompt", "33333333-aaaa-aaaa-aaaa-333333333333")
    anchor = datetime.now(timezone.utc) - timedelta(minutes=5)
    p, t = _seed_transcript(env_factory, "33333333-aaaa-aaaa-aaaa-333333333333", anchor)
    t.assistant_text("before-prompt")           # row1: before any prompt
    t.user_prompt("prompt 1")                    # row2
    t.assistant_text("after-p1-a")               # row3
    t.user_prompt("prompt 2")                    # row4 — last-prompt anchor
    t.assistant_text("after-p2-a")               # row5
    t.assistant_text("after-p2-b")               # row6
    t.write()

    rep = _retro(run_cli, env, "--since", "last-prompt")
    # Window spans [prompt 2 ts, now] → captures the last user_prompt itself
    # (boundary-inclusive) plus the two assistant rows after it.
    assert rep["turns"] == 2, rep
    assert rep["user_prompts"] == 1, rep


# ---------------------------------------------------------------------------
# Active pointer is left alone
# ---------------------------------------------------------------------------

def test_retro_does_not_touch_active(env_factory, run_cli):
    """Running `retro` while a profile is active must not clear the active pointer."""
    env = env_factory("retro-coexist", "44444444-aaaa-aaaa-aaaa-444444444444")

    # Start a real profile — env_factory already seeded a single-row transcript.
    r = run_cli(["start", "live"], env=env)
    assert r.returncode == 0, r.stderr

    # Verify it's active.
    r = run_cli(["status"], env=env)
    assert "active 'live'" in r.stdout, r.stdout

    # Now run retro — should succeed without disturbing the active pointer.
    rep = _retro(run_cli, env)
    assert rep.get("mode") == "retroactive"

    # Active pointer still points at "live".
    r = run_cli(["status"], env=env)
    assert "active 'live'" in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_retro_no_transcript_exits_2(tmp_path, env_factory, run_cli):
    """If no transcript exists for cwd, retro exits with code 2 + clear message."""
    env = env_factory("retro-no-transcript", "55555555-aaaa-aaaa-aaaa-555555555555")
    # env_factory seeded one — delete it to simulate "fresh cwd, no Claude session".
    cwd = env_factory.cwd
    claude_home = env_factory.claude_home
    p = parent_path(claude_home, cwd, "55555555-aaaa-aaaa-aaaa-555555555555")
    p.unlink()

    r = run_cli(["retro"], env=env)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "no transcript found" in r.stderr, r.stderr


def test_retro_since_after_until_exits_2(env_factory, run_cli):
    """`--since` later than `--until` is rejected with code 2."""
    env = env_factory("retro-bad-window", "66666666-aaaa-aaaa-aaaa-666666666666")
    # env_factory's seed transcript is enough — we just need a transcript to exist.
    r = run_cli(
        ["retro", "--since", "2026-05-04T12:00:00Z", "--until", "2026-05-04T11:00:00Z"],
        env=env,
    )
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "must be strictly before" in r.stderr, r.stderr


def test_retro_invalid_time_spec_exits_2(env_factory, run_cli):
    """Garbage `--since` value is rejected with a hint message."""
    env = env_factory("retro-bad-spec", "77777777-aaaa-aaaa-aaaa-777777777777")
    r = run_cli(["retro", "--since", "not-a-time"], env=env)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "not a recognized time spec" in r.stderr, r.stderr
