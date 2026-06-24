"""
差分验证 + 全仓审查确认缺陷的修复金样例（审查发现 F2/F3/F4/F5/F6/F7/F8/F9）。
每条都构造最小确定性场景，锁定修复后的正确数字，防回归。
"""
import numpy as np
import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from tests.helpers import make_df


def _v1_strat(targets, stop=None, take=None):
    def s(df):
        df = df.copy()
        df["target_position"] = list(targets)
        if stop is not None:
            df["stop_loss_price"] = list(stop)
        if take is not None:
            df["take_profit_price"] = list(take)
        return df
    return s


def _v2_weights(rows, sym="BTCUSDT"):
    def s(data):
        import pandas as pd
        return pd.DataFrame(list(rows), index=data[sym].index, columns=[sym])
    return s


# =========================================================
# F5 / F6 — 爆仓归零时 Sharpe / 年化不再被强制为 0
# =========================================================

def _wipeout_run():
    o = [100, 102, 104, 106, 50]
    highs = [x * 1.01 for x in o]
    lows = [x * 0.99 for x in o[:-1]] + [40]
    closes = [102, 104, 106, 108, 45]
    df = make_df(o, closes, highs, lows)
    return CodeBacktestCore(_v1_strat([1, 1, 1, 1, 1]), initial_cash=1000.0, leverage=3,
                            position_size=1.0, maintenance_margin_rate=0.0).run(df)


def test_f5_sharpe_negative_on_wipeout():
    m = _wipeout_run()["metrics"]
    assert m["total_return_pct"] == pytest.approx(-100.0, abs=1e-6)
    # 爆仓策略夏普必须是强负值，而非被 <=0 守卫误清成 0（看似"中性"）
    assert m["sharpe_ratio"] < 0


def test_f6_annual_return_minus_100_on_wipeout():
    m = _wipeout_run()["metrics"]
    # 归零年化退化为 -100%，不能停在 0 而与 total_return=-100% 自相矛盾
    assert m["annual_return_pct"] == pytest.approx(-100.0, abs=1e-6)


# =========================================================
# F3 / F4 / F7 — v2 单笔净亏不超过 -100%（与 v1 一致）
# =========================================================

def test_f3_v2_liquidation_trade_pnl_floored_to_margin():
    # 10x 多头，bar2 跳空 100->50 击穿强平价（裸亏名义 -500%）
    df = make_df([100, 100, 50, 100], [100, 100, 50, 100],
                 [100, 100, 55, 100], [100, 100, 40, 100])
    v1 = CodeBacktestCore(_v1_strat([1, 1, 1, 1]), initial_cash=1000.0, leverage=10,
                          position_size=1.0, maintenance_margin_rate=0.0).run(df)
    v2 = PortfolioBacktestCore(_v2_weights([(1,), (1,), (1,), (1,)]), initial_cash=1000.0,
                               leverage=10, position_size=1.0, rebalance_threshold=10.0,
                               maintenance_margin_rate=0.0).run({"BTCUSDT": df})

    def liq(res):
        return next(t for t in res["trades"] if t.get("exit_reason") == "liquidation")
    t1, t2 = liq(v1), liq(v2)
    # 单笔净亏夹到入场权益 -1000（-100%），不再报 -5000；v1≡v2
    assert t1["net_pnl"] == pytest.approx(-1000.0, abs=1e-6)
    assert t2["net_pnl"] == pytest.approx(-1000.0, abs=1e-6)
    assert v2["metrics"]["avg_loss"] == pytest.approx(v1["metrics"]["avg_loss"], abs=1e-6)
    # 权益曲线仍夹 0（终值不变）
    assert v1["metrics"]["final_equity"] == pytest.approx(0.0, abs=1e-9)
    assert v2["metrics"]["final_equity"] == pytest.approx(0.0, abs=1e-9)
    # gross 不夹（raw 价格口径，含杠杆放大），两引擎一致且 < -1000
    assert t2["gross_pnl"] < -1000.0


def test_f4_v2_liquidation_fee_does_not_exceed_margin():
    # 无跳空、带清算费：净亏仍不超过 -100%
    df = make_df([100, 100, 100, 100], [100, 100, 90, 100],
                 [100, 100, 100, 100], [100, 100, 90, 100])
    v2 = PortfolioBacktestCore(_v2_weights([(1,), (1,), (1,), (1,)]), initial_cash=1000.0,
                               leverage=10, position_size=1.0, rebalance_threshold=10.0,
                               liquidation_fee_rate=0.01, maintenance_margin_rate=0.0).run({"BTCUSDT": df})
    t = next(t for t in v2["trades"] if t.get("exit_reason") == "liquidation")
    assert t["net_pnl"] >= -1000.0 - 1e-6


# =========================================================
# F2 — 止盈成交根保留真实盘中回撤（max_drawdown 不被抹成 0）
# =========================================================

def test_f2_take_profit_bar_keeps_intrabar_drawdown():
    # 进场根先跌到 low=80(-20%)，再涨到 high=130 命中 TP；盘中回撤必须计入
    df = make_df([100, 100, 100, 100, 100], [100, 120, 100, 100, 100],
                 [100, 130, 100, 100, 100], [100, 80, 100, 100, 100])
    r = CodeBacktestCore(_v1_strat([1, 1, 1, 1, 1], take=[np.nan, 130, np.nan, np.nan, np.nan]),
                         initial_cash=1000.0, leverage=1, position_size=1.0,
                         maintenance_margin_rate=0.0).run(df)
    bar1 = r["equity_curve"][1]
    # 真实盘中最坏权益 = 1000 + 10*(80-100) = 800，不再被抹成结算后 cash=1300
    assert bar1["equity_worst"] == pytest.approx(800.0, abs=1e-6)
    # 回撤不再是 0
    assert r["metrics"]["max_drawdown_pct"] < -10.0


# =========================================================
# F9 — 开盘跳空越过止盈：止盈先成交（不被误判止损）
# =========================================================

def test_f9_v1_gap_open_through_tp_is_take_profit():
    # bar2 跳空高开 open=110 (>tp=105)，同根 low=92 (<stop=95)
    df = make_df([100, 100, 110, 100], [100, 100, 111, 100],
                 [100, 100, 112, 100], [100, 100, 92, 100])
    r = CodeBacktestCore(_v1_strat([1, 1, 1, 1],
                                   stop=[np.nan, np.nan, 95, np.nan],
                                   take=[np.nan, np.nan, 105, np.nan]),
                         initial_cash=1000.0, leverage=1, position_size=1.0,
                         maintenance_margin_rate=0.0).run(df)
    t = next(t for t in r["trades"] if t.get("exit_reason") in ("stop_loss", "take_profit"))
    assert t["exit_reason"] == "take_profit"
    assert t["net_pnl"] > 0  # 开盘 110 成交，盈利


def test_f9_v2_gap_open_through_tp_is_take_profit():
    df = make_df([100, 100, 110, 100], [100, 100, 111, 100],
                 [100, 100, 112, 100], [100, 100, 92, 100])
    r = PortfolioBacktestCore(_v2_weights([(1,), (1,), (1,), (1,)]), initial_cash=1000.0,
                              leverage=1, position_size=1.0, rebalance_threshold=10.0,
                              stop_loss_pct=0.05, take_profit_pct=0.05,
                              maintenance_margin_rate=0.0).run({"BTCUSDT": df})
    trig = [e for e in r["trades"] if e.get("exit_reason") in ("stop_loss", "take_profit")]
    assert trig and trig[0]["exit_reason"] == "take_profit"


# =========================================================
# F8 — 负的 stop/take pct 视为关闭（None），不产生退化触发
# =========================================================

def test_f8_negative_pct_disables_trigger():
    core = PortfolioBacktestCore(_v2_weights([(1,), (1,), (1,), (1,), (0,)]),
                                 initial_cash=1000.0, stop_loss_pct=-0.05, take_profit_pct=-0.1)
    assert core.stop_loss_pct is None
    assert core.take_profit_pct is None
    df = make_df([100, 100, 100, 100, 100], [100, 100, 100, 100, 100],
                 [100, 100, 100, 100, 100], [100, 99, 99, 100, 100])
    r = core.run({"BTCUSDT": df})
    # 无触发：不应出现 stop_loss/take_profit 出场（只有 signal/end_of_data）
    assert not any(e.get("exit_reason") in ("stop_loss", "take_profit") for e in r["trades"])
