"""
deepseek_strategy_reviewer 的 JSON 提取健壮性测试（不调用 API）。
"""

import pytest

from module.AI.api_config import clamp_score
from module.AI.deepseek_strategy_reviewer import _extract_json


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_trailing_commentary():
    # JSON 后面跟说明文字（含右花括号）也要能解析
    assert _extract_json('{"a": 1}\n以上就是审查结果')["a"] == 1


def test_extract_json_none_or_empty_raises():
    with pytest.raises(ValueError, match="空内容"):
        _extract_json(None)
    with pytest.raises(ValueError, match="空内容"):
        _extract_json("   ")


def test_extract_json_backticks_inside_value_preserved():
    # 不做全局 replace：字符串值内部的反引号必须原样保留
    data = _extract_json('{"s": "code `x` here"}')
    assert data["s"] == "code `x` here"


def test_clamp_score_bounds():
    assert clamp_score(150) == pytest.approx(99.99)
    assert clamp_score(-5) == pytest.approx(0.0)
    assert clamp_score("abc") == pytest.approx(0.0)
    assert clamp_score(88.5) == pytest.approx(88.5)
