"""
Tests for the agent's tooling-issue escalation channel — added after a
real bug (place_spread_order hardcoding "_to_open") went unnoticed
until a human happened to review trade history. The agent cannot fix
its own tools, so this is its only mechanism to surface a problem
quickly: write to the structured log AND a dedicated human-readable
file, immediately, the moment it notices something wrong.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher


@pytest.fixture(autouse=True)
def isolated_issues_file(monkeypatch, tmp_path):
    """Redirect the TOOLING_ISSUES.md write target so tests never touch
    the real file or leak state between tests."""
    fake_path_str = str(tmp_path / "TOOLING_ISSUES.md")  # resolve to plain str BEFORE patching os.path
    import agent_layer.tools as tools_module
    original_method = tools_module.ToolDispatcher._report_tooling_issue
    real_join = os.path.join

    def fake_join(*args):
        if args and args[-1] == "TOOLING_ISSUES.md":
            return fake_path_str
        return real_join(*args)

    def patched(self, tool_input):
        import os as os_module
        os_module.path.join = fake_join
        try:
            return original_method(self, tool_input)
        finally:
            os_module.path.join = real_join

    monkeypatch.setattr(tools_module.ToolDispatcher, "_report_tooling_issue", patched)
    yield tmp_path / "TOOLING_ISSUES.md"


def test_report_creates_file_with_header_on_first_call(isolated_issues_file):
    dispatcher = ToolDispatcher()
    result = dispatcher._report_tooling_issue({
        "severity": "high",
        "tool_name": "place_spread_order",
        "what_you_tried": "Tried to close an existing spread",
        "what_happened": "It opened an additional position instead",
        "suspected_cause": "position_intent may be hardcoded",
    })
    parsed = json.loads(result)
    assert parsed["acknowledged"] is True

    assert isolated_issues_file.exists()
    content = isolated_issues_file.read_text()
    assert "Tooling Issues Reported" in content
    assert "place_spread_order" in content
    assert "HIGH" in content


def test_report_appends_without_duplicating_header(isolated_issues_file):
    dispatcher = ToolDispatcher()
    dispatcher._report_tooling_issue({
        "severity": "low", "tool_name": "tool_a",
        "what_you_tried": "a", "what_happened": "b", "suspected_cause": "c",
    })
    dispatcher._report_tooling_issue({
        "severity": "medium", "tool_name": "tool_b",
        "what_you_tried": "d", "what_happened": "e", "suspected_cause": "f",
    })
    content = isolated_issues_file.read_text()
    assert content.count("# Tooling Issues Reported") == 1
    assert "tool_a" in content
    assert "tool_b" in content


def test_report_logs_to_structured_event_log(isolated_issues_file, tmp_path, monkeypatch):
    import execution.trade_logger as logger_module
    fake_log = tmp_path / "events.jsonl"
    monkeypatch.setattr(logger_module, "LOG_PATH", str(fake_log))

    dispatcher = ToolDispatcher()
    dispatcher._report_tooling_issue({
        "severity": "high", "tool_name": "place_spread_order",
        "what_you_tried": "x", "what_happened": "y", "suspected_cause": "z",
    })

    assert fake_log.exists()
    lines = fake_log.read_text().strip().split("\n")
    events = [json.loads(l) for l in lines]
    assert any(e["event_type"] == "tooling_issue_reported" for e in events)


def test_severity_markers_differ(isolated_issues_file):
    dispatcher = ToolDispatcher()
    dispatcher._report_tooling_issue({
        "severity": "high", "tool_name": "t", "what_you_tried": "a", "what_happened": "b", "suspected_cause": "c",
    })
    content = isolated_issues_file.read_text()
    assert "🔴" in content
