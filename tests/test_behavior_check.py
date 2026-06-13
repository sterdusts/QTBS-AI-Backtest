"""
behavior_check 行为审查测试（不调用任何 API）。
"""

import pytest

from module.Strategy.behavior_check import (
    SYNTHETIC_BARS,
    build_synthetic_kline,
    format_behavior_summary,
    run_behavior_check,
)


V1_TREND_CODE = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 1


def generate_signals(df):
    df = df.copy()
    upper = df["high"].rolling(20).max().shift(1)
    lower = df["low"].rolling(20).min().shift(1)

    signal = pd.Series(np.nan, index=df.index)
    signal[df["close"] > upper] = 1
    signal[df["close"] < lower] = -1

    df["target_position"] = signal.ffill().fillna(0).astype(int)
    return df
"""

V1_FLAT_CODE = """
import pandas as pd

CONTRACT_VERSION = 1


def generate_signals(df):
    df = df.copy()
    df["target_position"] = 0
    return df
"""

V1_RUNTIME_ERROR_CODE = """
import pandas as pd

CONTRACT_VERSION = 1


def generate_signals(df):
    df = df.copy()
    df["target_position"] = df["no_such_column"]
    return df
"""

V2_HEDGE_CODE = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def generate_signals(data):
    btc = data["BTCUSDT"]
    weights = pd.DataFrame(0.0, index=btc.index, columns=SYMBOLS)
    weights.iloc[10:, 0] = 0.5
    weights.iloc[10:, 1] = -0.5
    return weights
"""


# =========================================================
# 合成数据
# =========================================================

def test_synthetic_kline_shape_and_validity():
    df = build_synthetic_kline()

    assert len(df) == SYNTHETIC_BARS
    assert not df.isna().any().any()

    # OHLC 关系合法
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
    assert (df["low"] > 0).all()

    # 成交量必须有变化：恒定成交量会让一切量价条件策略永假
    assert df["volume"].nunique() > 100
    assert (df["volume"] > 0).all()


def test_synthetic_kline_deterministic():
    a = build_synthetic_kline(seed=7)
    b = build_synthetic_kline(seed=7)
    assert a.equals(b)

    c = build_synthetic_kline(seed=8)
    assert not a["close"].equals(c["close"])


# =========================================================
# v1 行为检查
# =========================================================

def test_v1_trend_strategy_passes_and_trades():
    behavior = run_behavior_check(V1_TREND_CODE)

    assert behavior["ok"] is True
    assert behavior["error"] is None
    assert behavior["contract_version"] == 1
    assert behavior["opened_position"] is True
    # 三段行情（涨/跌/震荡）下突破策略必然触发多与空
    assert behavior["trade_count"] >= 1
    assert behavior["used_short"] is True


def test_v1_flat_strategy_reports_no_activity():
    behavior = run_behavior_check(V1_FLAT_CODE)

    assert behavior["ok"] is True
    assert behavior["trade_count"] == 0
    assert behavior["opened_position"] is False
    assert behavior["used_short"] is False


def test_v1_runtime_error_is_captured_not_raised():
    behavior = run_behavior_check(V1_RUNTIME_ERROR_CODE)

    assert behavior["ok"] is False
    assert "no_such_column" in behavior["error"]


def test_behavior_check_is_deterministic():
    a = run_behavior_check(V1_TREND_CODE)
    b = run_behavior_check(V1_TREND_CODE)
    assert a == b


def test_v1_signal_only_on_last_bar_reported_as_unfilled():
    """信号只出现在末根：永远不会成交。事实必须按执行视角报告
    （0 笔交易、未开仓），并提示出现过非零信号，不能输出
    「0 笔交易；曾开仓」的自相矛盾陈述。"""
    code = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 1


def generate_signals(df):
    df = df.copy()
    df["target_position"] = 0
    df.iloc[-1, df.columns.get_loc("target_position")] = 1
    return df
"""
    behavior = run_behavior_check(code)

    assert behavior["ok"] is True
    assert behavior["trade_count"] == 0
    assert behavior["opened_position"] is False
    assert behavior["has_nonzero_signal"] is True

    summary = format_behavior_summary(behavior)
    assert "未发生成交" in summary


# =========================================================
# v2 行为检查
# =========================================================

def test_v2_hedge_strategy_passes():
    behavior = run_behavior_check(V2_HEDGE_CODE)

    assert behavior["ok"] is True
    assert behavior["contract_version"] == 2
    assert behavior["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert behavior["fill_count"] >= 2          # 两腿各至少一笔成交
    assert behavior["opened_position"] is True
    assert behavior["used_short"] is True
    assert behavior["max_gross_exposure"] == pytest.approx(1.0)


def test_v2_raw_exposure_not_masked_by_engine_scaling():
    """敞口事实必须取策略原始输出：引擎会把 gross>1 整行缩放，
    缩放后的权重永远 ≤1，超敞口违例对审查 AI 完全不可见。"""
    code = V2_HEDGE_CODE.replace(
        "weights.iloc[10:, 0] = 0.5", "weights.iloc[10:, 0] = 2.0"
    ).replace(
        "weights.iloc[10:, 1] = -0.5", "weights.iloc[10:, 1] = -1.0"
    )

    behavior = run_behavior_check(code)

    assert behavior["ok"] is True
    assert behavior["max_gross_exposure"] == pytest.approx(3.0)


def test_v2_missing_symbols_fails_cleanly():
    code = V2_HEDGE_CODE.replace('SYMBOLS = ["BTCUSDT", "ETHUSDT"]', "")
    behavior = run_behavior_check(code)

    assert behavior["ok"] is False
    assert "SYMBOLS" in behavior["error"]


# =========================================================
# 事实段落格式化
# =========================================================

def test_format_summary_pass():
    behavior = run_behavior_check(V2_HEDGE_CODE)
    summary = format_behavior_summary(behavior)

    assert "实际运行通过" in summary
    assert "契约 v2" in summary
    assert "BTCUSDT+ETHUSDT" in summary
    assert "做空" in summary


def test_format_summary_fail():
    behavior = run_behavior_check(V1_RUNTIME_ERROR_CODE)
    summary = format_behavior_summary(behavior)

    assert "失败" in summary
    assert "no_such_column" in summary


def test_format_summary_empty():
    assert format_behavior_summary(None) == ""
