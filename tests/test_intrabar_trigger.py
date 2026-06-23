"""
盘中触发价（止损/止盈）金样例（契约 §10.9，v2.2）—— v1 引擎。

v1 策略可选返回 stop_loss_price / take_profit_price 列（绝对价、逐根当根值、
NaN=该根无触发单、支持移动止损）。引擎当根用 high/low 判触及、按触发价成交
（跳空越过按本根 open，不加滑点），强平优先、同根止损+止盈保守优先止损。

手算基于 helpers.make_df（freq=4h、初始资金 1000、leverage=1、fee=0、slip=0 默认）：
入场在「目标确认的下一根开盘」，qty = 名义/入场价；本文件场景入场价均 100、qty=10。
"""

import numpy as np
import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from tests.helpers import make_df

NAN = float("nan")


def _strat(targets, stop=None, take=None):
    def strategy(df):
        df = df.copy()
        df["target_position"] = list(targets)
        if stop is not None:
            df["stop_loss_price"] = list(stop)
        if take is not None:
            df["take_profit_price"] = list(take)
        return df
    return strategy


def _run(strategy, opens, closes=None, highs=None, lows=None, funding_rates=None, **kw):
    params = dict(initial_cash=1000.0)
    params.update(kw)
    df = make_df(opens, closes=closes, highs=highs, lows=lows)
    return CodeBacktestCore(strategy_func=strategy, **params).run(df, funding_rates=funding_rates)


# =========================================================
# 退化硬门槛：全 NaN 触发列 == 无触发列 == baseline
# =========================================================

def test_trigger_all_nan_columns_equals_baseline():
    targets = [1, 1, 1, 0, 0]
    opens = [100, 100, 100, 105, 110]
    base = _run(_strat(targets), opens)
    nan_cols = _run(_strat(targets, stop=[NAN] * 5, take=[NAN] * 5), opens)
    b = [p["equity_close"] for p in base["equity_curve"]]
    assert [p["equity_close"] for p in nan_cols["equity_curve"]] == pytest.approx(b)
    assert nan_cols["metrics"]["final_equity"] == pytest.approx(base["metrics"]["final_equity"])


# =========================================================
# 多头止损 / 止盈
# =========================================================

def test_long_stop_loss_intrabar():
    # 入场 100、qty10；bar2 low=92 触及 stop=95 → 按 95 成交、pnl=-50、final=950
    res = _run(
        _strat([1, 1, 0, 0, 0], stop=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 92, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_price"] == pytest.approx(95.0)
    assert tr["pnl"] == pytest.approx(-50.0)
    assert res["metrics"]["final_equity"] == pytest.approx(950.0)


def test_long_take_profit_intrabar():
    # bar2 high=108 触及 tp=105 → 按 105 成交、pnl=+50、final=1050
    res = _run(
        _strat([1, 1, 0, 0, 0], take=[NAN, 105, 105, 105, 105]),
        opens=[100, 100, 100, 100, 100],
        highs=[100, 100, 108, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "take_profit"
    assert tr["exit_price"] == pytest.approx(105.0)
    assert tr["pnl"] == pytest.approx(50.0)
    assert res["metrics"]["final_equity"] == pytest.approx(1050.0)


# =========================================================
# 跳空越过：止损取更不利 open、止盈取更有利 open
# =========================================================

def test_long_stop_gap_down_fills_at_open():
    # bar2 跳空低开 open=90 < stop=95，low=85 → 按 open=90 成交（非 95）、pnl=-100
    res = _run(
        _strat([1, 1, 0, 0, 0], stop=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 90, 100, 100],
        highs=[100, 100, 92, 100, 100],
        lows=[100, 100, 85, 100, 100],
        closes=[100, 100, 88, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_price"] == pytest.approx(90.0)
    assert tr["pnl"] == pytest.approx(-100.0)


def test_long_take_gap_up_fills_at_open():
    # bar2 跳空高开 open=110 > tp=105，high=110 → 按 open=110 成交（更有利）、pnl=+100
    res = _run(
        _strat([1, 1, 0, 0, 0], take=[NAN, 105, 105, 105, 105]),
        opens=[100, 100, 110, 100, 100],
        highs=[100, 100, 112, 100, 100],
        lows=[100, 100, 108, 100, 100],
        closes=[100, 100, 111, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "take_profit"
    assert tr["exit_price"] == pytest.approx(110.0)
    assert tr["pnl"] == pytest.approx(100.0)


# =========================================================
# 做空方向（镜像）
# =========================================================

def test_short_stop_loss_intrabar():
    # 空头入场 100、qty10；bar2 high=108 触及 stop=105 → 按 105 成交、pnl=-50（涨即亏）
    res = _run(
        _strat([-1, -1, 0, 0, 0], stop=[NAN, 105, 105, 105, 105]),
        opens=[100, 100, 100, 100, 100],
        highs=[100, 100, 108, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["side"] == "short"
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_price"] == pytest.approx(105.0)
    assert tr["pnl"] == pytest.approx(-50.0)


def test_short_take_profit_intrabar():
    # 空头 bar2 low=92 触及 tp=95 → 按 95 成交、pnl=+50（跌即盈）
    res = _run(
        _strat([-1, -1, 0, 0, 0], take=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 92, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["side"] == "short"
    assert tr["exit_reason"] == "take_profit"
    assert tr["exit_price"] == pytest.approx(95.0)
    assert tr["pnl"] == pytest.approx(50.0)


# =========================================================
# 定序：强平优先 / 同根止损+止盈保守优先止损
# =========================================================

def test_liquidation_takes_priority_over_stop():
    # leverage=10 ⇒ qty100、强平价=(100*100-1000)/100=90；stop=95 在其上方。
    # bar2 low=85 同时触及 stop(95) 与强平(90)：强平先于止损 → exit_reason=liquidation
    res = _run(
        _strat([1, 1, 0, 0, 0], stop=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 85, 100, 100],
        leverage=10,
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "liquidation"
    assert res["metrics"]["liquidation_count"] >= 1


def test_same_bar_stop_and_take_prefers_stop():
    # bar2 low=92 触 stop=95 且 high=108 触 tp=105：保守优先止损
    res = _run(
        _strat([1, 1, 0, 0, 0], stop=[NAN, 95, 95, 95, 95], take=[NAN, 105, 105, 105, 105]),
        opens=[100, 100, 100, 100, 100],
        highs=[100, 100, 108, 100, 100],
        lows=[100, 100, 92, 100, 100],
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_price"] == pytest.approx(95.0)


# =========================================================
# 末根触发不退化为 end_of_data / 触发后重入
# =========================================================

def test_trigger_on_last_bar_not_end_of_data():
    # 末根(i=4) low=92 触 stop=95 → exit_reason=stop_loss（非 end_of_data）
    res = _run(
        _strat([1, 1, 1, 1, 1], stop=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 100, 100, 92],
    )
    assert len(res["trades"]) == 1
    assert res["trades"][0]["exit_reason"] == "stop_loss"


def test_reentry_after_stop_trigger():
    # bar2 止损平仓后，目标仍为多 → 下根重入，产生第二笔（末根 end_of_data 收尾）
    res = _run(
        _strat([1, 1, 1, 1, 1], stop=[NAN, NAN, 95, NAN, NAN]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 92, 100, 100],
    )
    reasons = [t["exit_reason"] for t in res["trades"]]
    assert reasons[0] == "stop_loss"
    assert len(res["trades"]) >= 2  # 触发后重入再收尾


def test_trigger_bar_includes_funding():
    # 触发平仓那根仍计 funding：带正费率多头、bar2 触发止损，funding_pnl<0（净付出）
    res = _run(
        _strat([1, 1, 0, 0, 0], stop=[NAN, 95, 95, 95, 95]),
        opens=[100, 100, 100, 100, 100],
        lows=[100, 100, 92, 100, 100],
        funding_rates=[0.001] * 5,
    )
    tr = res["trades"][0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["funding_pnl"] < 0
