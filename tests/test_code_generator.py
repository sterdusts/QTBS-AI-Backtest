"""
deepseek_code_generator 验证逻辑测试（不调用 API）。
"""

import pytest

from module.AI.deepseek_code_generator import (
    build_strategy_code_prompt,
    clean_python_code,
    validate_generated_code,
)


V1_CODE = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 1


def generate_signals(df):
    df = df.copy()
    df["target_position"] = 0
    return df
"""

V2_CODE = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def generate_signals(data):
    btc = data["BTCUSDT"]
    weights = pd.DataFrame(0.0, index=btc.index, columns=SYMBOLS)
    return weights.fillna(0.0)
"""


# =========================================================
# 代码块清理
# =========================================================

@pytest.mark.parametrize("wrapped", [
    "```python\nCODE\n```",
    "```py\nCODE\n```",
    "```Python\nCODE\n```",
    "```\nCODE\n```",
    "CODE",
])
def test_clean_python_code_variants(wrapped):
    assert clean_python_code(wrapped) == "CODE"


def test_clean_python_code_empty_raises():
    with pytest.raises(ValueError):
        clean_python_code("")


# ---------------------------------------------------------
# 修复 #8：稳健提取代码围栏（模型加前言 / 多代码块时不残留 markdown）
# ---------------------------------------------------------

# 围栏内必须是合法 Python，clean 后会做 ast.parse 真因校验
_FENCE_INNER = (
    "import pandas as pd\n"
    "import numpy as np\n"
    "\n"
    "CONTRACT_VERSION = 1\n"
)


def test_clean_python_code_strips_preamble_before_fence():
    # (a) 模型加了前言再给围栏：应只提取首个完整围栏内容，丢掉前言
    raw = f"Sure! Here is the strategy:\n```python\n{_FENCE_INNER}```"
    assert clean_python_code(raw) == _FENCE_INNER.strip()


def test_clean_python_code_pure_code_without_fence_returned_asis():
    # (b) 纯代码无围栏：原样返回（仅 strip）
    assert clean_python_code(_FENCE_INNER) == _FENCE_INNER.strip()


def test_clean_python_code_single_fence_extracted():
    # (c) 单个正常围栏：提取围栏内容
    raw = f"```python\n{_FENCE_INNER}```"
    assert clean_python_code(raw) == _FENCE_INNER.strip()


def test_clean_python_code_picks_first_of_multiple_blocks():
    # 多代码块：只取首个完整围栏，不把第二块或破碎中段拼进来
    raw = (
        "先看代码：\n"
        f"```python\n{_FENCE_INNER}```\n"
        "另外还有一个例子：\n"
        "```python\nx = 999\n```"
    )
    assert clean_python_code(raw) == _FENCE_INNER.strip()


def test_clean_python_code_residual_markdown_raises_actionable_error():
    # 残留 markdown / 混入说明文字导致无法解析时，给出可行动真因，
    # 而非误导性的「语法错误: invalid syntax」
    raw = "Sure! Here is the strategy I wrote for you, hope it helps."
    with pytest.raises(ValueError, match="纯 Python 代码"):
        clean_python_code(raw)


# =========================================================
# 生成代码验证（双契约）
# =========================================================

def test_validate_v1_code():
    validate_generated_code(V1_CODE)


def test_validate_v1_without_target_position_rejected():
    code = V1_CODE.replace('df["target_position"] = 0', "pass")
    with pytest.raises(ValueError, match="target_position"):
        validate_generated_code(code)


def test_validate_v2_code():
    validate_generated_code(V2_CODE)


def test_validate_v2_without_symbols_rejected():
    code = V2_CODE.replace('SYMBOLS = ["BTCUSDT", "ETHUSDT"]', "")
    with pytest.raises(ValueError, match="SYMBOLS"):
        validate_generated_code(code)


def test_validate_unknown_version_rejected():
    code = V1_CODE.replace("CONTRACT_VERSION = 1", "CONTRACT_VERSION = 3")
    with pytest.raises(ValueError, match="契约版本"):
        validate_generated_code(code)


def test_validate_missing_generate_signals_rejected():
    with pytest.raises(ValueError, match="generate_signals"):
        validate_generated_code("CONTRACT_VERSION = 1\nx = 1\n")


def test_validate_lookahead_shift_rejected():
    code = V1_CODE.replace(
        'df["target_position"] = 0',
        'df["target_position"] = (df["close"].shift(-1) > df["close"]).astype(int)',
    )
    with pytest.raises(ValueError, match="未来函数"):
        validate_generated_code(code)


@pytest.mark.parametrize("token", ['"4h"', "'1d'", "resample("])
def test_validate_forbidden_tokens_rejected_for_both_contracts(token):
    v1_bad = V1_CODE + f"\n# {token}\n"
    with pytest.raises(ValueError, match="不允许"):
        validate_generated_code(v1_bad)

    v2_bad = V2_CODE + f"\n# {token}\n"
    with pytest.raises(ValueError, match="不允许"):
        validate_generated_code(v2_bad)


def test_comment_mentioning_timeframe_word_allowed():
    # 裸词 "timeframe" 不在黑名单：英文注释提到它不应误杀合规代码
    code = V1_CODE + "\n# This strategy works on any timeframe.\n"
    validate_generated_code(code)


def test_validate_v2_bad_symbol_format_rejected():
    code = V2_CODE.replace('["BTCUSDT", "ETHUSDT"]', '["btc", "ETHUSDT"]')
    with pytest.raises(ValueError, match="USDT"):
        validate_generated_code(code)


def test_validate_v1_multi_symbol_rejected():
    # v1 多标的静默截断会无声丢掉对冲腿：生成侧必须拒绝并指引升级 v2
    code = V1_CODE.replace(
        "CONTRACT_VERSION = 1",
        'CONTRACT_VERSION = 1\nSYMBOLS = ["BTCUSDT", "ETHUSDT"]',
    )
    with pytest.raises(ValueError, match="CONTRACT_VERSION = 2"):
        validate_generated_code(code)


# =========================================================
# Prompt 构造（烟雾测试）
# =========================================================

def test_prompt_contains_both_contracts():
    prompt = build_strategy_code_prompt(
        user_text="MA20 上穿 MA60 做多",
        available_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )

    assert "CONTRACT_VERSION = 1" in prompt
    assert "CONTRACT_VERSION = 2" in prompt
    assert "SYMBOLS" in prompt
    assert "BTCUSDT、ETHUSDT、SOLUSDT" in prompt
    assert "MA20 上穿 MA60 做多" in prompt


def test_prompt_without_available_symbols():
    prompt = build_strategy_code_prompt(user_text="测试策略")
    assert "CONTRACT_VERSION = 2" in prompt
