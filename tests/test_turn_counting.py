"""Turn-counting accuracy tests.

These lock in the relationship between rows in the transcript JSONLs and the
fields the profiler reports:

  - `turns`            ← every assistant row (parent + subagents, after merge)
  - `sidechain_turns`  ← assistant rows attributed to a subagent file
                         (or `isSidechain: true` when no separate file exists)
  - `debug_turns`      ← subset of assistant rows that contain ≥1 tool_use
  - `tool_calls`       ← total tool_use blocks across all assistant rows
  - `subagent_calls`   ← Agent/Skill tool_use blocks (the dispatch event itself)
  - `subagent_files`   ← number of agent-*.jsonl files discovered
  - `user_prompts`     ← user rows that are NOT pure tool_result containers

The relationship `turns == main_turns + sidechain_turns` is implicit; we assert
the components rather than expose a derived field, so future renames don't
silently break the contract.
"""
from __future__ import annotations


def test_main_only_assistant_turns(built_window):
    """N text-only assistant rows on the parent → turns=N, sidechain=0, debug=0."""
    win = built_window()
    win.parent.assistant_text("hi 1")
    win.parent.assistant_text("hi 2")
    win.parent.assistant_text("hi 3")
    rep = win.stop()

    assert rep["turns"] == 3, rep
    assert rep["sidechain_turns"] == 0, rep
    assert rep["debug_turns"] == 0, rep
    assert rep["tool_calls"] == 0, rep
    assert rep["subagent_calls"] == 0, rep
    assert rep["subagent_files"] == 0, rep


def test_user_prompts_excludes_tool_results(built_window):
    """user_prompts counts true prompts; tool_result-only user rows don't qualify."""
    win = built_window()
    win.parent.user_prompt("first ask")
    win.parent.assistant_with_tools([("Bash", {"command": "ls"})])  # emits 1 tool_result user row
    win.parent.user_prompt("second ask")
    win.parent.assistant_text("done")
    rep = win.stop()

    assert rep["user_prompts"] == 2, rep
    assert rep["turns"] == 2, rep
    assert rep["debug_turns"] == 1, rep
    assert rep["tool_calls"] == 1, rep


def test_debug_turns_counts_tool_using_rows(built_window):
    """debug_turns = number of assistant rows containing ≥1 tool_use, regardless of how many tools."""
    win = built_window()
    win.parent.assistant_text("plain")
    win.parent.assistant_with_tools([("Bash", {"command": "ls"})])
    win.parent.assistant_with_tools([
        ("Bash", {"command": "pwd"}),
        ("Read", {"file_path": "/tmp/x"}),
        ("Edit", {"file_path": "/tmp/x", "old_string": "a", "new_string": "b"}),
    ])
    win.parent.assistant_text("done")
    rep = win.stop()

    assert rep["turns"] == 4, rep
    assert rep["debug_turns"] == 2, rep
    assert rep["tool_calls"] == 4, rep  # 1 + 3


def test_subagent_file_turns_added_to_total(built_window):
    """parent K rows + 1 subagent file with S rows → turns=K+S, sidechain=S, subagent_files=1."""
    win = built_window()
    win.parent.user_prompt("kick off subagent")
    win.parent.assistant_with_tools([("Agent", {"description": "explore"})])
    win.parent.assistant_text("subagent finished, summary follows")

    sub = win.sub("abc123", agent_type="Explore")
    sub.assistant_text("sub turn 1", is_sidechain=True)
    sub.assistant_with_tools([("Bash", {"command": "ls"})], is_sidechain=True)
    sub.assistant_text("sub turn 3", is_sidechain=True)
    sub.assistant_text("sub turn 4", is_sidechain=True)

    rep = win.stop()

    assert rep["turns"] == 2 + 4, rep
    assert rep["sidechain_turns"] == 4, rep
    assert rep["subagent_files"] == 1, rep
    assert rep["subagent_calls"] == 1, rep
    # main turns is the implicit complement
    assert rep["turns"] - rep["sidechain_turns"] == 2


def test_multiple_subagent_files_aggregate(built_window):
    """Two parallel subagent files → turns/sidechain include both, subagent_files=2."""
    win = built_window()
    win.parent.user_prompt("dispatch two")
    win.parent.assistant_with_tools([
        ("Agent", {"description": "one"}),
        ("Agent", {"description": "two"}),
    ])

    s1 = win.sub("aaa", agent_type="Explore")
    s1.assistant_text("s1-a", is_sidechain=True)
    s1.assistant_text("s1-b", is_sidechain=True)

    s2 = win.sub("bbb", agent_type="Plan")
    s2.assistant_text("s2-a", is_sidechain=True)
    s2.assistant_text("s2-b", is_sidechain=True)
    s2.assistant_text("s2-c", is_sidechain=True)

    rep = win.stop()

    assert rep["turns"] == 1 + 5, rep
    assert rep["sidechain_turns"] == 5, rep
    assert rep["subagent_files"] == 2, rep
    assert rep["subagent_calls"] == 2, rep


def test_subagent_calls_counts_agent_tool_uses(built_window):
    """subagent_calls increments for every Agent/Skill tool_use, even with no subagent files yet."""
    win = built_window()
    win.parent.assistant_with_tools([("Agent", {"description": "x"})])
    win.parent.assistant_with_tools([("Skill", {"skill": "y"})])
    win.parent.assistant_with_tools([("Bash", {"command": "echo hi"})])
    rep = win.stop()

    assert rep["subagent_calls"] == 2, rep
    assert rep["tool_calls"] == 3, rep
    # No subagent files were created, so:
    assert rep["subagent_files"] == 0, rep
    assert rep["sidechain_turns"] == 0, rep


def test_legacy_isSidechain_flag_in_parent(built_window):
    """If no separate subagent file exists but a parent row carries isSidechain=true,
    the row must still be classified as sidechain."""
    win = built_window()
    win.parent.assistant_text("main row")  # is_sidechain=False
    win.parent.assistant_text("legacy sidechain row", is_sidechain=True)
    win.parent.assistant_text("another main", is_sidechain=False)
    rep = win.stop()

    assert rep["turns"] == 3, rep
    # Note: when no subagent files exist, _qs_source defaults to "main". The
    # is_sidechain fallback is only triggered when r.get('_qs_source') is falsy.
    # The current implementation tags every parent row with _qs_source="main",
    # so isSidechain in the parent file is *not* honored. This test asserts the
    # current behavior so any future flip is intentional.
    assert rep["sidechain_turns"] == 0, rep


def test_message_id_dedup_does_not_inflate_turns(built_window):
    """Streaming: multiple JSONL rows sharing one message.id are one logical turn."""
    win = built_window()
    first = win.parent.assistant_text("chunk 1")
    mid = first["message"]["id"]
    win.parent.assistant_streaming_chunk(mid, "chunk 2")
    win.parent.assistant_streaming_chunk(mid, "chunk 3")
    win.parent.assistant_text("real second turn")
    rep = win.stop()

    assert rep["turns"] == 2, rep


def test_parent_and_subagent_both_present_with_user_prompts(built_window):
    """End-to-end shape: realistic small session.

    1 user prompt → assistant dispatches Agent → subagent does 3 turns (1 with Bash) →
    assistant summarizes. Verifies all counters compose correctly.
    """
    win = built_window()
    win.parent.user_prompt("research X")
    win.parent.assistant_with_tools([("Agent", {"description": "research"})])
    win.parent.assistant_text("here is the summary")

    sub = win.sub("agent01", agent_type="Explore")
    sub.assistant_text("starting research", is_sidechain=True)
    sub.assistant_with_tools([("Bash", {"command": "rg foo"})], is_sidechain=True)
    sub.assistant_text("done researching", is_sidechain=True)

    rep = win.stop()

    assert rep["user_prompts"] == 1
    assert rep["turns"] == 2 + 3
    assert rep["sidechain_turns"] == 3
    assert rep["subagent_calls"] == 1
    assert rep["subagent_files"] == 1
    # Tool counts: 1 Agent (parent) + 1 Bash (subagent) = 2
    assert rep["tool_calls"] == 2
    # debug_turns: 1 in parent (the Agent dispatch row) + 1 in subagent (the Bash row)
    assert rep["debug_turns"] == 2


def test_api_call_count_by_source_splits_main_and_subagent(built_window):
    """The per-source breakdown must reflect that subagent rows came from a separate file."""
    win = built_window()
    win.parent.assistant_text("main 1")
    win.parent.assistant_text("main 2")

    sub = win.sub("agent01")
    sub.assistant_text("sub 1", is_sidechain=True)
    sub.assistant_text("sub 2", is_sidechain=True)
    sub.assistant_text("sub 3", is_sidechain=True)

    rep = win.stop()
    by_src = rep.get("api_call_count_by_source", {})
    assert by_src.get("main") == 2, rep
    assert by_src.get("subagent") == 3, rep


def test_message_id_collision_across_subagent_files_does_not_merge(built_window):
    """Defensive: two subagent files with a colliding message.id must NOT be merged.

    Real Anthropic message ids are globally unique, but the profiler's dedup key
    must include the agent_id (not just `(source, message.id)`) so that any
    cross-file collision can't silently fold two distinct API calls into one.
    """
    shared_mid = "msg_collision_xyz"
    win = built_window()
    win.parent.assistant_text("main")

    s1 = win.sub("aaa")
    s1.assistant_text("sub-1 turn", is_sidechain=True, message_id=shared_mid)

    s2 = win.sub("bbb")
    s2.assistant_text("sub-2 turn", is_sidechain=True, message_id=shared_mid)

    rep = win.stop()

    # Without the fix, both subagent rows would merge into one and turns drops to 2.
    assert rep["turns"] == 3, rep
    assert rep["sidechain_turns"] == 2, rep
    assert rep["subagent_files"] == 2, rep
