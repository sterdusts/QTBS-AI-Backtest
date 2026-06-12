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
