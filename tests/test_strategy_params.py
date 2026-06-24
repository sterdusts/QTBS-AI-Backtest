"""
契约 v3：策略可参数化（generate_signals(df, params=None) + 模块级 PARAM_SPACE）金样例。
- call_strategy 按签名分发（1 参历史策略 / 2 参参数化策略）
- PARAM_SPACE AST 解析与校验
- 引擎把 params 透传给策略、params=None 走默认（bit-level 退化）
- behavior_check 两次调用一致性探针（无副作用纯函数）
"""

import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from module.Strategy.behavior_check import run_behavior_check
from module.Strategy.strategy_loader import (
    call_strategy,
    load_strategy_func_from_code,
    parse_strategy_metadata,
)
from tests.helpers import make_df

PARAM_V1 = (
    "import pandas as pd\nimport numpy as np\n\n"
    "CONTRACT_VERSION = 1\n"
    'PARAM_SPACE = {"threshold": [0, 1, 2]}\n\n'
    "def generate_signals(df, params=None):\n"
    "    p = params or {}\n"
    "    threshold = p.get('threshold', 0)\n"
    "    df = df.copy()\n"
    "    df['target_position'] = (df['close'].diff().fillna(0) > threshold).astype(int)\n"
    "    return df\n"
)

PLAIN_V1 = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n\n"
    "def generate_signals(df):\n    df = df.copy()\n    df['target_position'] = 1\n    return df\n"
)

STATEFUL_V1 = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n_calls = []\n\n"
    "def generate_signals(df):\n"
    "    _calls.append(1)\n"
    "    df = df.copy()\n"
    "    df['target_position'] = len(_calls) % 2\n"
    "    return df\n"
)


# =========================================================
# call_strategy 按签名分发
# =========================================================

def test_call_strategy_dispatch_by_signature():
    df = make_df([100, 101, 102])

    def one_arg(d):
        d = d.copy()
        d["target_position"] = 1
        return d

    def two_arg(d, params=None):
        d = d.copy()
        d["target_position"] = (params or {}).get("t", 9)
        return d

    # 1 参：params 被忽略
    assert int(call_strategy(one_arg, df, {"t": 5})["target_position"].iloc[0]) == 1
    # 2 参：params 传入
    assert int(call_strategy(two_arg, df, {"t": 5})["target_position"].iloc[0]) == 5
    # 2 参 + params=None：走策略默认
    assert int(call_strategy(two_arg, df, None)["target_position"].iloc[0]) == 9


# =========================================================
# PARAM_SPACE 解析与校验
# =========================================================

def test_param_space_parsed():
    meta = parse_strategy_metadata(PARAM_V1)
    assert meta["param_space"] == {"threshold": [0, 1, 2]}


def test_param_space_absent_is_none():
    assert parse_strategy_metadata(PLAIN_V1)["param_space"] is None


def test_param_space_invalid_raises():
    for bad in ['PARAM_SPACE = {"a": "notlist"}', 'PARAM_SPACE = {1: [1, 2]}',
                'PARAM_SPACE = {"a": []}', 'PARAM_SPACE = []']:
        code = f"import pandas as pd\nCONTRACT_VERSION = 1\n{bad}\n\ndef generate_signals(df):\n    return df\n"
        with pytest.raises(ValueError, match="PARAM_SPACE"):
            parse_strategy_metadata(code)


# =========================================================
# 引擎透传 params
# =========================================================

def test_v1_engine_passes_params_and_none_uses_default(tmp_path):
    df = make_df([100, 101, 102, 103, 104, 105, 106, 107])  # 持续上涨
    func = load_strategy_func_from_code(PARAM_V1)

    # threshold=0：每根 diff>0 ⇒ 持续做多 ⇒ 有交易
    r0 = CodeBacktestCore(strategy_func=func, initial_cash=1000.0).run(df, params={"threshold": 0})
    # threshold=1000：diff 永不超 ⇒ 全 0 ⇒ 无交易
    rhi = CodeBacktestCore(strategy_func=func, initial_cash=1000.0).run(df, params={"threshold": 1000})
    assert r0["metrics"]["trade_count"] >= 1
    assert rhi["metrics"]["trade_count"] == 0

    # params=None ⇒ 用策略默认 threshold=0 ⇒ 与显式 0 一致
    rnone = CodeBacktestCore(strategy_func=func, initial_cash=1000.0).run(df, params=None)
    c0 = [p["equity_close"] for p in r0["equity_curve"]]
    assert [p["equity_close"] for p in rnone["equity_curve"]] == pytest.approx(c0)


# =========================================================
# behavior_check 两次调用一致性探针
# =========================================================

def test_behavior_check_deterministic_true():
    res = run_behavior_check(PLAIN_V1)
    assert res["ok"] is True
    assert res["deterministic"] is True


def test_behavior_check_detects_stateful_strategy():
    res = run_behavior_check(STATEFUL_V1)
    assert res["ok"] is True
    assert res["deterministic"] is False  # 模块级状态 ⇒ 两次调用不一致
