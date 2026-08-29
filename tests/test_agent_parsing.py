import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_layer.claude_agent import _strip_code_fences


def test_strips_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert _strip_code_fences(text) == '{"a": 1}'


def test_strips_bare_fence():
    text = '```\n{"a": 1}\n```'
    assert _strip_code_fences(text) == '{"a": 1}'


def test_passes_through_unfenced_text():
    text = '{"a": 1}'
    assert _strip_code_fences(text) == '{"a": 1}'


def test_handles_surrounding_whitespace():
    text = '  \n```json\n{"a": 1}\n```\n  '
    assert _strip_code_fences(text) == '{"a": 1}'
