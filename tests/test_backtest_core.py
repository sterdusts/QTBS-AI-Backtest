"""
CodeBacktestCore 金样例测试。

所有期望值均为手算，对应 STRATEGY_CONTRACT.md 第 5 节的执行模型承诺：
- 信号在当前 K 线收盘确认，下一根 K 线开盘成交
- 滑点方向：多开/空平 ×(1+slip)，多平/空开 ×(1-slip)
- 手续费：开平仓均按 名义价值 × fee_rate
- 仓位：保证金 = 权益 × position_size，名义 = 保证金 × leverage
- 对账语义：实际持仓 vs 目标状态不一致才交易
"""

import pandas as pd
import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from tests.helpers import make_df, strategy_from_targets


def run_core(df, targets, **kwargs):
    params = dict(initial_cash=1000.0, fee_rate=0.0, slippage=0.0,
                  leverage=1, position_size=1.0)
    params.update(kwargs)
    core = CodeBacktestCore(strategy_func=strategy_from_targets(targets), **params)
    return core.run(df)


# =========================================================
# 金样例 1：成交时点 + 手续费
# =========================================================

def test_golden_timing_and_fees():
    df = make_df([100, 100, 100, 105, 110])
    result = run_core(df, [0, 1, 1, 0, 0], fee_rate=0.001)

    trades = result["trades"]
    assert len(trades) == 1
    trade = trades[0]

    # 信号在 idx[1] 收盘确认 → idx[2] 开盘成交；平仓信号 idx[3] → idx[4] 开盘成交
    assert trade["entry_time"] == str(df.index[2])
    assert trade["exit_time"] == str(df.index[4])
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(110.0)

    # 手算：开仓名义 1000，开仓费 1.0；平仓名义 10×110=1100，平仓费 1.1
    assert trade["open_fee"] == pytest.approx(1.0)
    assert trade["close_fee"] == pytest.approx(1.1)

    # cash: 1000 - 1 + 10×(110-100) - 1.1 = 1097.9
    assert trade["pnl"] == pytest.approx(97.9)
    assert trade["pnl_pct"] == pytest.approx(9.79)
    assert trade["equity_after"] == pytest.approx(1097.9)
    assert trade["holding_hours"] == pytest.approx(8.0)

    metrics = result["metrics"]
    assert metrics["final_equity"] == pytest.approx(1097.9)
    assert metrics["total_return_pct"] == pytest.approx(9.79)
    assert metrics["trade_count"] == 1
    assert metrics["net_win_rate"] == pytest.approx(100.0)


def test_golden_equity_curve_values():
    df = make_df([100, 100, 100, 105, 110])
    result = run_core(df, [0, 1, 1, 0, 0], fee_rate=0.001)

    curve = result["equity_curve"]

    # 权益曲线覆盖每一根 K 线（含首尾）
    assert len(curve) == len(df)
    assert curve[0]["equity_close"] == pytest.approx(1000.0)

    # idx2 持仓首根：cash=999, close=100 → 999
    assert curve[2]["equity_close"] == pytest.approx(999.0)
    # idx3: 999 + 10×(105-100) = 1049
    assert curve[3]["equity_close"] == pytest.approx(1049.0)
    # idx4 已平仓：1097.9
    assert curve[4]["equity_close"] == pytest.approx(1097.9)

    realized = result["realized_equity_curve"]
    assert len(realized) == len(df)
    assert realized[0]["equity"] == pytest.approx(1000.0)
    assert realized[3]["equity"] == pytest.approx(1000.0)   # 平仓发生在 idx4 开盘
    assert realized[4]["equity"] == pytest.approx(1097.9)


# =========================================================
# 金样例 2：滑点方向
# =========================================================

def test_golden_slippage_long():
    df = make_df([100, 100, 100, 110, 110])
    result = run_core(df, [0, 1, 1, 0, 0], slippage=0.01)

    trade = result["trades"][0]

    # 多头开仓 100×1.01=101，平仓 110×0.99=108.9
    assert trade["entry_price"] == pytest.approx(101.0)
    assert trade["exit_price"] == pytest.approx(108.9)
    assert trade["entry_raw_price"] == pytest.approx(100.0)
    assert trade["exit_raw_price"] == pytest.approx(110.0)

    # position = 1000/101，pnl = position × 7.9
    assert trade["pnl"] == pytest.approx(7900.0 / 101.0)

    # gross = 实际持仓 × raw 价差（不含滑点与手续费，与组合引擎同口径）：
    # position = 1000/101，gross = (1000/101) × (110-100) = 99.0099…
    assert trade["gross_pnl"] == pytest.approx(10000.0 / 101.0)
    assert trade["gross_pnl_pct"] == pytest.approx(1000.0 / 101.0)


# =========================================================
# 金样例 3：做空
# =========================================================

def test_golden_short_trade():
    df = make_df([100, 100, 100, 95, 90])
    result = run_core(df, [0, -1, -1, 0, 0])

    trade = result["trades"][0]
    assert trade["side"] == "short"
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(90.0)
    # 空头：pnl = 10 × (100 - 90) = 100
    assert trade["pnl"] == pytest.approx(100.0)
    assert result["metrics"]["final_equity"] == pytest.approx(1100.0)

    # 持仓中 idx3（close=95）：1000 + 10×(100-95) = 1050
    assert result["equity_curve"][3]["equity_close"] == pytest.approx(1050.0)


# =========================================================
# 金样例 4：杠杆
# =========================================================

def test_golden_leverage():
    df = make_df([100, 100, 100, 101, 102])
    result = run_core(df, [0, 1, 1, 0, 0], leverage=5, fee_rate=0.001)

    trade = result["trades"][0]

    # 保证金 1000，名义 5000，开仓费 5，position 50
    assert trade["entry_margin"] == pytest.approx(1000.0)
    assert trade["entry_notional"] == pytest.approx(5000.0)
    assert trade["open_fee"] == pytest.approx(5.0)

    # 平仓 @102：pnl=50×2=100，平仓费 5100×0.001=5.1
    # cash = 995 + 100 - 5.1 = 1089.9
    assert trade["equity_after"] == pytest.approx(1089.9)
    assert trade["pnl"] == pytest.approx(89.9)


# =========================================================
# 金样例 5：仓位比例
# =========================================================

def test_golden_position_size():
    df = make_df([100, 100, 100, 105, 110])
    result = run_core(df, [0, 1, 1, 0, 0], position_size=0.5)

    trade = result["trades"][0]
    assert trade["entry_margin"] == pytest.approx(500.0)
    assert trade["entry_notional"] == pytest.approx(500.0)

    # position 5，pnl = 5×10 = 50；收益率以全部权益为基数 = 5%
    assert trade["pnl"] == pytest.approx(50.0)
    assert trade["pnl_pct"] == pytest.approx(5.0)
    assert result["metrics"]["final_equity"] == pytest.approx(1050.0)


# =========================================================
# 金样例 6：强平
# =========================================================

def test_golden_liquidation_long():
    df = make_df(
        [100, 100, 100, 100, 100],
        closes=[100, 100, 88, 100, 100],
        highs=[100, 100, 100, 100, 100],
        lows=[100, 100, 85, 100, 100],
    )
    result = run_core(df, [0, 1, 1, 1, 1], leverage=10)

    # 强平价 = 100 + (0 - 1000)/100 = 90，bar2 low=85 触发
    assert len(result["liquidation_events"]) == 1
    event = result["liquidation_events"][0]
    assert event["liquidation_price"] == pytest.approx(90.0)
    assert event["trigger_price"] == pytest.approx(85.0)

    trade = result["trades"][0]
    assert trade["exit_reason"] == "liquidation"
    assert trade["exit_price"] == pytest.approx(90.0)
    assert trade["pnl"] == pytest.approx(-1000.0)
    assert trade["pnl_pct"] == pytest.approx(-100.0)

    metrics = result["metrics"]
    assert metrics["liquidated"] is True
    assert metrics["liquidation_count"] == 1
    assert metrics["final_equity"] == pytest.approx(0.0)

    # stop_on_liquidation=True：回测在强平 K 线终止
    assert len(result["equity_curve"]) == 3
    assert result["equity_curve"][-1]["liquidated"] is True
    assert result["realized_equity_curve"][-1]["equity"] == pytest.approx(0.0)


# =========================================================
# 金样例 7：首根 K 线即有信号（对账语义回归测试）
# =========================================================

def test_golden_first_bar_signal_opens_position():
    df = make_df([100, 102, 104, 106])
    result = run_core(df, [1, 1, 1, 1])

    # 首根信号 → 第二根开盘 102 成交，持有到结束
    # 持有到结束的仓位按最后收盘价虚拟结算计入交易统计
    assert result["metrics"]["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "end_of_data"
    assert trade["exit_price"] == pytest.approx(106.0)
    assert result["equity_curve"][-1]["position_side"] == 1

    # 最终权益 = 1000 + (1000/102)×(106-102)，虚拟结算不改变权益
    expected = 1000.0 + (1000.0 / 102.0) * 4.0
    assert result["metrics"]["final_equity"] == pytest.approx(expected)
    assert trade["pnl"] == pytest.approx(expected - 1000.0)

    # 权益曲线覆盖全部 K 线
    assert len(result["equity_curve"]) == len(df)
    assert result["equity_curve"][0]["equity_close"] == pytest.approx(1000.0)


# =========================================================
# 金样例 8：平仓后再进场（对账语义）
# =========================================================

def test_golden_reentry_after_exit():
    df = make_df([100, 100, 100, 100, 100, 100])
    result = run_core(df, [0, 1, 0, 1, 1, 1])

    # 第一段：idx2 开 → idx3 平（signal）；
    # 第二段：idx4 开，持有到结束（end_of_data 虚拟结算）
    assert result["metrics"]["trade_count"] == 2
    assert result["trades"][0]["exit_reason"] == "signal"
    assert result["trades"][1]["exit_reason"] == "end_of_data"
    assert result["trades"][1]["pnl"] == pytest.approx(0.0)
    assert result["equity_curve"][-1]["position_side"] == 1


# =========================================================
# 金样例 9：多空反手
# =========================================================

def test_golden_flip_long_to_short():
    df = make_df([100, 100, 100, 110, 110, 105])
    result = run_core(df, [0, 1, 1, -1, -1, -1])

    trades = result["trades"]
    assert len(trades) == 2
    assert trades[0]["side"] == "long"
    # 多头 idx2@100 → idx4@110 平仓，pnl=100；同根开盘反手开空
    assert trades[0]["exit_time"] == str(df.index[4])
    assert trades[0]["pnl"] == pytest.approx(100.0)

    # 空头持有到结束：1100 资金 @110 开仓（position=10），
    # 收盘 105 虚拟结算：pnl = 10×(110-105) = 50
    assert trades[1]["side"] == "short"
    assert trades[1]["exit_reason"] == "end_of_data"
    assert trades[1]["pnl"] == pytest.approx(50.0)

    assert result["equity_curve"][-1]["position_side"] == -1
    assert result["metrics"]["final_equity"] == pytest.approx(1150.0)


# =========================================================
# 输入校验
# =========================================================

def test_invalid_target_position_rejected():
    df = make_df([100, 100, 100])
    with pytest.raises(ValueError, match="target_position"):
        run_core(df, [0, 2, 0])


def test_missing_price_column_rejected():
    df = make_df([100, 100, 100]).drop(columns=["high"])
    with pytest.raises(ValueError, match="缺少必要字段"):
        run_core(df, [0, 0, 0])


def test_missing_target_column_rejected():
    df = make_df([100, 100, 100])
    core = CodeBacktestCore(strategy_func=lambda d: d)
    with pytest.raises(ValueError, match="target_position"):
        core.run(df)


# =========================================================
# 盘中浮动权益（mtm）数学
# =========================================================

def test_mtm_long():
    core = CodeBacktestCore(strategy_func=lambda d: d)
    mtm = core._calculate_mtm_equity(
        cash=1000.0, position=10.0, position_side=1,
        entry_price=100.0, high_price=120.0, low_price=95.0, close_price=110.0,
    )
    assert mtm["equity_close"] == pytest.approx(1100.0)
    assert mtm["equity_at_high"] == pytest.approx(1200.0)
    assert mtm["equity_at_low"] == pytest.approx(950.0)
    assert mtm["equity_worst"] == pytest.approx(950.0)
    assert mtm["equity_best"] == pytest.approx(1200.0)
    # 偏离 close 更远的一侧：|950-1100|=150 > |1200-1100|=100
    assert mtm["equity_intrabar_extreme"] == pytest.approx(950.0)


def test_mtm_short():
    core = CodeBacktestCore(strategy_func=lambda d: d)
    mtm = core._calculate_mtm_equity(
        cash=1000.0, position=10.0, position_side=-1,
        entry_price=100.0, high_price=120.0, low_price=95.0, close_price=110.0,
    )
    assert mtm["equity_close"] == pytest.approx(900.0)
    assert mtm["equity_at_high"] == pytest.approx(800.0)
    assert mtm["equity_at_low"] == pytest.approx(1050.0)
    assert mtm["equity_worst"] == pytest.approx(800.0)
    assert mtm["equity_best"] == pytest.approx(1050.0)
    assert mtm["equity_intrabar_extreme"] == pytest.approx(1050.0)


def test_mtm_flat():
    core = CodeBacktestCore(strategy_func=lambda d: d)
    mtm = core._calculate_mtm_equity(
        cash=1234.5, position=0.0, position_side=0,
        entry_price=None, high_price=120.0, low_price=95.0, close_price=110.0,
    )
    assert all(
        mtm[key] == pytest.approx(1234.5)
        for key in ["equity_close", "equity_at_high", "equity_at_low",
                    "equity_worst", "equity_best", "equity_intrabar_extreme"]
    )


# =========================================================
# 指标计算
# =========================================================

def test_metrics_math():
    core = CodeBacktestCore(strategy_func=lambda d: d)

    times = pd.date_range("2024-01-01", periods=4, freq="1D")
    equity_curve = [
        {"time": str(t), "equity": e, "equity_close": e, "equity_worst": w}
        for t, e, w in zip(times, [1000, 1200, 950, 1100], [1000, 1200, 900, 1100])
    ]

    trades = [
        {"gross_pnl": p, "pnl": p, "holding_hours": 2.0}
        for p in [10.0, 20.0, -5.0, 15.0]
    ]

    metrics = core._calculate_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_cash=1000.0,
        final_equity=1100.0,
    )

    assert metrics["total_return_pct"] == pytest.approx(10.0)
    # 回撤基于盘中最坏权益：(900-1200)/1200 = -25%
    assert metrics["max_drawdown_pct"] == pytest.approx(-25.0)
    assert metrics["trade_count"] == 4
    assert metrics["net_win_rate"] == pytest.approx(75.0)
    assert metrics["avg_profit"] == pytest.approx(15.0)
    assert metrics["avg_loss"] == pytest.approx(-5.0)
    assert metrics["payoff_ratio"] == pytest.approx(3.0)
    assert metrics["profit_factor"] == pytest.approx(9.0)
    assert metrics["max_consecutive_wins"] == 2
    assert metrics["max_consecutive_losses"] == 1
    assert metrics["avg_holding_hours"] == pytest.approx(2.0)
    assert metrics["annual_return_pct"] > 0


def test_metrics_no_trades():
    core = CodeBacktestCore(strategy_func=lambda d: d)
    metrics = core._calculate_metrics(
        equity_curve=[], trades=[], initial_cash=1000.0, final_equity=1000.0,
    )
    assert metrics["trade_count"] == 0
    assert metrics["total_return_pct"] == pytest.approx(0.0)
    assert metrics["max_drawdown_pct"] == pytest.approx(0.0)


def test_fractional_target_rejected():
    """合法值校验必须在整数转换之前：0.5 不能被 astype 截断成合法的 0。"""
    core = CodeBacktestCore(
        strategy_func=lambda d: d.assign(target_position=[0, 0.5, 0])
    )
    with pytest.raises(ValueError, match="只能是 -1, 0, 1"):
        core.run(make_df([100, 100, 100]))


def test_mixed_type_invalid_target_message():
    """混合类型非法值（'1' 与 0.5）必须报契约错误，
    不能在 sorted 处炸 TypeError。"""
    core = CodeBacktestCore(
        strategy_func=lambda d: d.assign(target_position=["1", 0.5, 0])
    )
    with pytest.raises(ValueError, match="只能是 -1, 0, 1"):
        core.run(make_df([100, 100, 100]))


def test_non_finite_target_rejected():
    """inf 必须以契约报错拒绝，而不是在 astype 处抛晦涩的转换错误。"""
    core = CodeBacktestCore(
        strategy_func=lambda d: d.assign(target_position=[0, float("inf"), 0])
    )
    with pytest.raises(ValueError, match="只能是 -1, 0, 1"):
        core.run(make_df([100, 100, 100]))


def test_v2_style_strategy_gets_actionable_hint():
    """v2 写法（按标的名取数据）误入 v1 引擎时，KeyError 必须被翻译成
    指向 CONTRACT_VERSION 声明的可行动报错，而不是裸键名。"""

    def v2_style_strategy(df):
        return df["BTCUSDT"]

    core = CodeBacktestCore(strategy_func=v2_style_strategy)
    with pytest.raises(ValueError, match="CONTRACT_VERSION"):
        core.run(make_df([100, 100, 100]))


# =========================================================
# 金样例 10：策略 dropna / 缩短行不改变回测窗口（reindex 回输入索引）
# =========================================================

def test_golden_strategy_dropna_does_not_truncate_window():
    """修复 #5：策略对返回帧 dropna/缩短行（如 rolling 指标的前导 NaN
    被 dropna 丢掉），引擎必须按【输入】索引重对齐，回测窗口仍为完整
    输入区间——不再在更短窗口静默跑完、equity_curve 只覆盖剩余根数。

    钉死：
      - equity_curve / realized_equity_curve 长度 == 输入长度
      - 曲线起止时间 == 输入起止时间（不被截断到策略返回帧的子区间）
      - target_position 缺行按 ffill().fillna(0) 延续，与显式给出
        重对齐后目标序列的非截断参照逐根一致
    """
    df = make_df([100, 100, 100, 100, 100, 100])
    input_index = df.index

    def dropna_strategy(d):
        d = d.copy()
        # 完整 6 行先给出目标，再丢掉前两行（模拟 rolling 指标 dropna 副作用）
        d["target_position"] = [0, 1, 1, 1, 0, 0]
        return d.iloc[2:]

    core = CodeBacktestCore(strategy_func=dropna_strategy, initial_cash=1000.0)
    result = core.run(df)

    # 窗口锚定输入索引：曲线覆盖完整 6 根，而不是策略返回的 4 根
    assert len(result["equity_curve"]) == len(input_index)
    assert len(result["realized_equity_curve"]) == len(input_index)
    assert result["equity_curve"][0]["time"] == str(input_index[0])
    assert result["equity_curve"][-1]["time"] == str(input_index[-1])

    # 缺行 ffill：前两行无前值兜底为 0，存活行沿用 → 重对齐后目标 [0,0,1,1,0,0]。
    # 与「非截断地显式给出同一目标序列」的参照运行逐根一致，证明截断不改变结算。
    reference = run_core(df, [0, 0, 1, 1, 0, 0])
    got_close = [p["equity_close"] for p in result["equity_curve"]]
    ref_close = [p["equity_close"] for p in reference["equity_curve"]]
    assert got_close == pytest.approx(ref_close)
    assert result["metrics"]["final_equity"] == pytest.approx(
        reference["metrics"]["final_equity"]
    )

    # result["df"] 也回到完整输入索引（webUI/behavior_check 取自它）
    assert list(result["df"].index) == list(input_index)


def test_strategy_index_disjoint_from_input_rejected():
    """策略 reset_index / 整体平移时间轴导致索引与输入完全不重叠时，
    重对齐无源会静默全表回退 0、产出假「0 笔交易」报告——必须报错。"""

    def shifted_strategy(d):
        d = d.copy()
        d["target_position"] = [0, 1, 1]
        # 时间轴整体平移到与输入零交集
        d.index = pd.date_range("2030-01-01", periods=len(d), freq="4h")
        return d

    core = CodeBacktestCore(strategy_func=shifted_strategy, initial_cash=1000.0)
    with pytest.raises(ValueError, match="完全不重叠"):
        core.run(make_df([100, 100, 100]))


def test_equal_length_frame_reindex_is_noop():
    """happy path 零影响显式回归：策略返回与输入等长同序帧时，
    reindex 重对齐块整体跳过，逐根结算与未引入重对齐前完全一致
    （由 strategy_from_targets 返回同索引帧，覆盖全部既有金样例的形状）。"""
    df = make_df([100, 100, 100, 105, 110])
    with_reindex = run_core(df, [0, 1, 1, 0, 0], fee_rate=0.001)

    # 与金样例 1 的手算期望逐项一致：确认等长路径未被重对齐改写
    trade = with_reindex["trades"][0]
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(110.0)
    assert trade["pnl"] == pytest.approx(97.9)
    assert len(with_reindex["equity_curve"]) == len(df)
    assert list(with_reindex["df"].index) == list(df.index)
