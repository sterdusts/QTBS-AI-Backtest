"""
PortfolioBacktestCore（契约 v2）金样例测试。

所有期望值手算。关键不变量：
- 单资产、全仓、离散权重场景下，v2 必须和 v1 引擎给出完全相同的结果
- 强平 α 插值在单资产时退化为 v1 强平价公式（同一场景同一数字）
"""

import math

import pandas as pd
import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from tests.helpers import make_df, strategy_from_targets, weights_strategy


def run_portfolio(data, rows, **kwargs):
    symbols = list(data.keys())
    params = dict(initial_cash=1000.0, fee_rate=0.0, slippage=0.0,
                  leverage=1, position_size=1.0, rebalance_threshold=0.01)
    params.update(kwargs)
    core = PortfolioBacktestCore(strategy_func=weights_strategy(rows, symbols), **params)
    return core.run(data)


# =========================================================
# 金样例 P1：单资产全仓 —— 必须与 v1 引擎完全一致
# =========================================================

def test_single_asset_matches_v1_engine():
    opens = [100, 100, 100, 105, 110]
    targets = [0, 1, 1, 0, 0]

    # v1 引擎
    v1 = CodeBacktestCore(strategy_func=strategy_from_targets(targets),
                          initial_cash=1000.0, fee_rate=0.001).run(make_df(opens))

    # v2 引擎（同一场景，权重写法）
    v2 = run_portfolio({"BTCUSDT": make_df(opens)},
                       [(t,) for t in targets], fee_rate=0.001)

    assert v2["metrics"]["final_equity"] == pytest.approx(v1["metrics"]["final_equity"])
    assert v2["metrics"]["final_equity"] == pytest.approx(1097.9)

    assert len(v2["trades"]) == len(v1["trades"]) == 1
    assert v2["trades"][0]["pnl"] == pytest.approx(v1["trades"][0]["pnl"])
    assert v2["trades"][0]["pnl_pct"] == pytest.approx(v1["trades"][0]["pnl_pct"])
    assert v2["trades"][0]["entry_time"] == v1["trades"][0]["entry_time"]
    assert v2["trades"][0]["exit_time"] == v1["trades"][0]["exit_time"]

    # 逐根权益曲线一致
    v1_closes = [p["equity_close"] for p in v1["equity_curve"]]
    v2_closes = [p["equity_close"] for p in v2["equity_curve"]]
    assert v2_closes == pytest.approx(v1_closes)


# =========================================================
# 金样例 P2：50/50 双资产组合
# =========================================================

def test_two_asset_50_50():
    data = {
        "BTCUSDT": make_df([100, 100, 100, 100, 110]),
        "ETHUSDT": make_df([10, 10, 10, 10, 12]),
    }
    rows = [(0, 0), (0.5, 0.5), (0.5, 0.5), (0, 0), (0, 0)]

    result = run_portfolio(data, rows)

    # 进场：各 500 名义 → BTC 5 个，ETH 50 个
    open_fills = [f for f in result["fills"] if f["action"] == "open"]
    assert len(open_fills) == 2
    fills_by_symbol = {f["symbol"]: f for f in open_fills}
    assert fills_by_symbol["BTCUSDT"]["qty"] == pytest.approx(5.0)
    assert fills_by_symbol["ETHUSDT"]["qty"] == pytest.approx(50.0)

    # 出场：BTC 5×10=50，ETH 50×2=100 → 1150
    assert result["metrics"]["final_equity"] == pytest.approx(1150.0)
    assert result["metrics"]["trade_count"] == 2

    episodes = {e["symbol"]: e for e in result["trades"]}
    assert episodes["BTCUSDT"]["pnl"] == pytest.approx(50.0)
    assert episodes["BTCUSDT"]["pnl_pct"] == pytest.approx(5.0)
    assert episodes["ETHUSDT"]["pnl"] == pytest.approx(100.0)
    assert episodes["ETHUSDT"]["pnl_pct"] == pytest.approx(10.0)

    # 持仓期间敞口 = 0.5 / 0.5
    exposure = result["exposure_curve"][2]
    assert exposure["BTCUSDT"] == pytest.approx(0.5)
    assert exposure["ETHUSDT"] == pytest.approx(0.5)


# =========================================================
# 金样例 P3：BTC/ETH 多空对冲
# =========================================================

def test_hedge_long_short():
    data = {
        "BTCUSDT": make_df([100, 100, 100, 100, 90]),
        "ETHUSDT": make_df([10, 10, 10, 10, 8]),
    }
    rows = [(0, 0), (0.5, -0.5), (0.5, -0.5), (0, 0), (0, 0)]

    result = run_portfolio(data, rows)

    episodes = {e["symbol"]: e for e in result["trades"]}

    # BTC 多头：5 × (90-100) = -50
    assert episodes["BTCUSDT"]["side"] == "long"
    assert episodes["BTCUSDT"]["pnl"] == pytest.approx(-50.0)

    # ETH 空头：50 × (10-8) = +100
    assert episodes["ETHUSDT"]["side"] == "short"
    assert episodes["ETHUSDT"]["pnl"] == pytest.approx(100.0)

    # 对冲净收益 +50
    assert result["metrics"]["final_equity"] == pytest.approx(1050.0)

    # 持仓期间敞口符号正确
    exposure = result["exposure_curve"][2]
    assert exposure["BTCUSDT"] == pytest.approx(0.5)
    assert exposure["ETHUSDT"] == pytest.approx(-0.5)


# =========================================================
# 金样例 P4：部分减仓（动态仓位）
# =========================================================

def test_partial_rebalance():
    data = {"BTCUSDT": make_df([100, 100, 100, 100, 100, 100])}
    rows = [(0,), (1.0,), (0.4,), (0.4,), (0,), (0,)]

    result = run_portfolio(data, rows)

    # 三笔成交：开 10 个 → 减 6 个 → 全平 4 个
    assert len(result["fills"]) == 3
    assert result["fills"][0]["action"] == "open"
    assert result["fills"][0]["qty"] == pytest.approx(10.0)
    assert result["fills"][1]["action"] == "close"
    assert result["fills"][1]["qty"] == pytest.approx(6.0)
    assert result["fills"][2]["action"] == "close"
    assert result["fills"][2]["qty"] == pytest.approx(4.0)

    # 敞口轨迹：开仓后 1.0 → 减仓后 0.4 → 全平后 0
    assert result["exposure_curve"][2]["BTCUSDT"] == pytest.approx(1.0)
    assert result["exposure_curve"][3]["BTCUSDT"] == pytest.approx(0.4)
    assert result["exposure_curve"][4]["BTCUSDT"] == pytest.approx(0.4)
    assert result["exposure_curve"][5]["BTCUSDT"] == pytest.approx(0.0)

    # 价格不动、无费用：权益不变
    assert result["metrics"]["final_equity"] == pytest.approx(1000.0)
    # 完整持仓片段：开 → 减 → 全平 算一笔
    assert result["metrics"]["trade_count"] == 1
    assert result["trades"][0]["exit_time"] == str(data["BTCUSDT"].index[5])


# =========================================================
# 金样例 P5：总敞口归一化 + position_size 全局缩放
# =========================================================

def test_gross_exposure_normalized():
    data = {
        "BTCUSDT": make_df([100, 100, 100, 100, 110]),
        "ETHUSDT": make_df([10, 10, 10, 10, 12]),
    }
    # AI 给出总敞口 2.0 → 引擎缩放为 0.5/0.5
    rows = [(0, 0), (1.0, 1.0), (1.0, 1.0), (0, 0), (0, 0)]

    result = run_portfolio(data, rows)

    open_fills = {f["symbol"]: f for f in result["fills"] if f["action"] == "open"}
    assert open_fills["BTCUSDT"]["qty"] == pytest.approx(5.0)
    assert open_fills["ETHUSDT"]["qty"] == pytest.approx(50.0)
    assert result["metrics"]["final_equity"] == pytest.approx(1150.0)


def test_position_size_scales_weights():
    data = {"BTCUSDT": make_df([100, 100, 100, 105, 110])}
    rows = [(0,), (1.0,), (1.0,), (0,), (0,)]

    result = run_portfolio(data, rows, position_size=0.5)

    open_fill = result["fills"][0]
    assert open_fill["qty"] == pytest.approx(5.0)
    # 5 × (110-100) = 50
    assert result["metrics"]["final_equity"] == pytest.approx(1050.0)


# =========================================================
# 金样例 P6：上市前 NaN —— 不可交易，上市后自动进场
# =========================================================

def test_pre_listing_nan_skipped():
    btc = make_df([100, 100, 100, 100, 100, 100])

    eth = make_df([10, 10, 10, 10, 10, 10])
    eth.iloc[0:3] = float("nan")  # 前 3 根未上市

    data = {"BTCUSDT": btc, "ETHUSDT": eth}
    rows = [(0.5, 0.5)] * 6

    result = run_portfolio(data, rows)

    fills_by_symbol = {}
    for f in result["fills"]:
        fills_by_symbol.setdefault(f["symbol"], []).append(f)

    # BTC 首根信号 → 第二根开盘进场
    assert fills_by_symbol["BTCUSDT"][0]["time"] == str(btc.index[1])

    # ETH 上市前无法交易，第一笔成交在 idx[3]（首个有效开盘价）
    assert fills_by_symbol["ETHUSDT"][0]["time"] == str(btc.index[3])
    assert fills_by_symbol["ETHUSDT"][0]["qty"] == pytest.approx(50.0)


# =========================================================
# 金样例 P7：强平 —— α 插值退化为 v1 公式
# =========================================================

def test_liquidation_matches_v1_formula():
    data = {
        "BTCUSDT": make_df(
            [100, 100, 100, 100, 100],
            closes=[100, 100, 88, 100, 100],
            highs=[100, 100, 100, 100, 100],
            lows=[100, 100, 85, 100, 100],
        )
    }
    rows = [(0,), (1.0,), (1.0,), (1.0,), (1.0,)]

    result = run_portfolio(data, rows, leverage=10)

    # α = (0-1000)/(-1500) = 2/3 → 强平价 = 100 + (85-100)×2/3 = 90
    # 与 v1 引擎同场景的强平价完全一致
    assert len(result["liquidation_events"]) == 1
    leg = result["liquidation_events"][0]["legs"][0]
    assert leg["liquidation_price"] == pytest.approx(90.0)
    assert leg["trigger_price"] == pytest.approx(85.0)

    trade = result["trades"][0]
    assert trade["exit_reason"] == "liquidation"
    assert trade["pnl"] == pytest.approx(-1000.0)
    assert trade["pnl_pct"] == pytest.approx(-100.0)

    metrics = result["metrics"]
    assert metrics["liquidated"] is True
    assert metrics["final_equity"] == pytest.approx(0.0)

    # stop_on_liquidation=True：曲线终止于强平 K 线
    assert len(result["equity_curve"]) == 3
    assert result["equity_curve"][-1]["liquidated"] is True
    assert result["realized_equity_curve"][-1]["equity"] == pytest.approx(0.0)


# =========================================================
# 权重缺行语义：策略未返回的行 = 无意见 = 维持当前目标
# =========================================================

def test_missing_weight_rows_treated_as_hold():
    data = {"BTCUSDT": make_df([100, 100, 100, 100, 100, 100])}
    full_index = data["BTCUSDT"].index

    def strategy(d):
        idx = d["BTCUSDT"].index
        w = pd.DataFrame({"BTCUSDT": [0.0, 1.0, 1.0, 1.0, 0.0, 0.0]}, index=idx)
        # 策略丢掉了中间一行（如 dropna 的副作用）
        return w.drop(idx[2])

    core = PortfolioBacktestCore(strategy_func=strategy, initial_cash=1000.0)
    result = core.run(data)

    # 缺行视为「维持」：只有 开仓→平仓 两笔成交，
    # 而不是把缺行解释成目标空仓导致 平仓→再开仓 的循环磨损
    assert [f["action"] for f in result["fills"]] == ["open", "close"]
    assert result["metrics"]["trade_count"] == 1
    assert result["trades"][0]["exit_time"] == str(full_index[5])


# =========================================================
# 末尾持仓虚拟结算（end_of_data）
# =========================================================

def test_open_position_finalized_at_end_of_data():
    data = {"BTCUSDT": make_df([100, 102, 104, 106])}
    result = run_portfolio(data, [(1,), (1,), (1,), (1,)], fee_rate=0.001)

    assert result["metrics"]["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "end_of_data"

    # 虚拟结算不产生成交、不改变权益：episode 盈亏 = 最终权益变动
    assert len(result["fills"]) == 1
    expected_pnl = result["metrics"]["final_equity"] - 1000.0
    assert trade["pnl"] == pytest.approx(expected_pnl)


def test_hold_to_end_matches_v1_engine():
    opens = [100, 102, 104, 106]
    targets = [1, 1, 1, 1]

    v1 = CodeBacktestCore(strategy_func=strategy_from_targets(targets),
                          initial_cash=1000.0, fee_rate=0.001).run(make_df(opens))
    v2 = run_portfolio({"BTCUSDT": make_df(opens)},
                       [(t,) for t in targets], fee_rate=0.001)

    # 持有到结束的场景：两引擎的虚拟结算给出相同的交易统计与权益
    assert v1["metrics"]["trade_count"] == v2["metrics"]["trade_count"] == 1
    assert v1["trades"][0]["exit_reason"] == v2["trades"][0]["exit_reason"] == "end_of_data"
    assert v2["trades"][0]["pnl"] == pytest.approx(v1["trades"][0]["pnl"])
    assert v2["metrics"]["final_equity"] == pytest.approx(v1["metrics"]["final_equity"])


# =========================================================
# 对账语义：权重恒定 + 1 倍杠杆 → 不产生漂移交易
# =========================================================

def test_constant_full_weight_does_not_churn():
    data = {"BTCUSDT": make_df([100, 100, 110, 121, 133])}
    rows = [(1.0,)] * 5

    result = run_portfolio(data, rows)

    # 期货式账本下满仓权重自我维持：只有 1 笔进场，无漂移调仓
    assert len(result["fills"]) == 1
    assert result["fills"][0]["action"] == "open"


# =========================================================
# 输入校验
# =========================================================

def test_misaligned_indexes_rejected():
    btc = make_df([100, 100, 100])
    eth = make_df([10, 10, 10, 10])  # 长度不同 → 索引不对齐

    with pytest.raises(ValueError, match="对齐"):
        run_portfolio({"BTCUSDT": btc, "ETHUSDT": eth}, [(0, 0)] * 3)


def test_missing_column_rejected():
    df = make_df([100, 100, 100]).drop(columns=["low"])
    with pytest.raises(ValueError, match="缺少必要字段"):
        run_portfolio({"BTCUSDT": df}, [(0,)] * 3)


def test_non_dataframe_weights_rejected():
    data = {"BTCUSDT": make_df([100, 100, 100])}
    core = PortfolioBacktestCore(strategy_func=lambda d: {"BTCUSDT": [0, 0, 0]})
    with pytest.raises(ValueError, match="DataFrame"):
        core.run(data)


def test_unknown_symbol_in_weights_rejected():
    data = {"BTCUSDT": make_df([100, 100, 100])}

    def strategy(d):
        index = d["BTCUSDT"].index
        return pd.DataFrame({"BTCUSDT": [0, 0, 0], "DOGEUSDT": [1, 1, 1]}, index=index)

    core = PortfolioBacktestCore(strategy_func=strategy)
    with pytest.raises(ValueError, match="面板之外"):
        core.run(data)


def test_nan_weights_treated_as_zero():
    data = {"BTCUSDT": make_df([100, 100, 100, 100, 100])}
    rows = [(float("nan"),), (1.0,), (float("nan"),), (float("nan"),), (0,)]

    result = run_portfolio(data, rows)

    # NaN → 0：idx2 开仓后，row2 的 NaN 视为目标空仓 → idx3 全平
    assert result["metrics"]["trade_count"] == 1
    assert result["trades"][0]["exit_time"] == str(data["BTCUSDT"].index[3])


def test_weights_missing_declared_column_raises():
    """缺列与多列同为契约违例：静默补 0 会把对冲组合无声变成单边裸仓。"""
    data = {"BTCUSDT": make_df([100, 100, 100]), "ETHUSDT": make_df([10, 10, 10])}

    def strategy(d):
        return pd.DataFrame({"BTCUSDT": [0, 1, 1]}, index=d["BTCUSDT"].index)

    core = PortfolioBacktestCore(strategy_func=strategy)
    with pytest.raises(ValueError, match="缺少 SYMBOLS"):
        core.run(data)


def test_close_allowed_when_equity_below_zero():
    """关闭强平、权益打穿 0 后：目标 0 的强制平仓必须仍然执行
    （破产后只禁止开仓/调仓，不能禁止止损离场，与 v1 行为一致）。"""
    opens = [100, 100, 40, 40, 40]
    rows = [(1,), (1,), (0,), (0,), (0,)]

    result = run_portfolio(
        {"BTCUSDT": make_df(opens)}, rows,
        leverage=5, enable_liquidation=False,
    )

    # 入场 idx1 开盘 100，qty=50；idx2 收盘 40 → 权益 -2000；
    # row2 目标 0 → idx3 开盘全平，仓位不能滞留在账上
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "signal"
    assert result["trades"][0]["exit_time"] == str(
        make_df(opens).index[3]
    )
    assert result["metrics"]["final_equity"] == pytest.approx(-2000.0)


def test_reopen_after_liquidation_when_not_stopping():
    """对账语义下强平后目标仍为非零 → 下一根重新开仓（v1/v2 一致，
    契约 §5.3/§10.5）；stop_on_liquidation=True（默认）则回测终止不受影响。"""
    opens = [100, 100, 100, 100, 100]
    lows = [100, 80, 100, 80, 100]
    targets = [1, 1, 1, 1, 1]

    kw = dict(initial_cash=1000.0, leverage=10,
              maintenance_margin_rate=0.05, stop_on_liquidation=False)

    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets(targets), **kw
    ).run(make_df(opens, lows=lows))

    v2 = run_portfolio({"BTCUSDT": make_df(opens, lows=lows)},
                       [(t,) for t in targets], **kw)

    assert v1["metrics"]["liquidation_count"] == 2
    assert v2["metrics"]["liquidation_count"] == 2


def test_weights_index_mismatch_raises():
    """权重索引与面板完全不重叠：必须报错，不能静默跑出全零权重报告。"""
    data = {"BTCUSDT": make_df([100, 100, 100, 100, 100])}

    def strategy(d):
        return pd.DataFrame({"BTCUSDT": [0, 1, 1, 0, 0]}, index=range(5))

    core = PortfolioBacktestCore(strategy_func=strategy)
    with pytest.raises(ValueError, match="不重叠"):
        core.run(data)


# =========================================================
# 金样例 P-滑点：slippage > 0 时单资产 v1/v2 仍逐根一致
# =========================================================

def test_single_asset_with_slippage_matches_v1():
    """v2 目标数量必须按预期成交价（含滑点）换算，否则成交后名义敞口
    系统性超出目标 (1+slippage) 倍，单资产不变量在 slippage>0 即破。"""
    opens = [100, 100, 100, 105, 110]
    targets = [0, 1, 1, 0, 0]

    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets(targets),
        initial_cash=1000.0, fee_rate=0.001, slippage=0.002,
    ).run(make_df(opens))

    v2 = run_portfolio({"BTCUSDT": make_df(opens)}, [(t,) for t in targets],
                       fee_rate=0.001, slippage=0.002)

    # 成交数量一致：qty = 名义 / 含滑点成交价 = 1000 / 100.2
    open_fill = [f for f in v2["fills"] if f["action"] == "open"][0]
    assert open_fill["qty"] == pytest.approx(1000.0 / 100.2)

    assert v2["metrics"]["final_equity"] == pytest.approx(v1["metrics"]["final_equity"])
    assert v2["trades"][0]["pnl"] == pytest.approx(v1["trades"][0]["pnl"])
    assert v2["trades"][0]["gross_pnl"] == pytest.approx(v1["trades"][0]["gross_pnl"])

    v1_closes = [p["equity_close"] for p in v1["equity_curve"]]
    v2_closes = [p["equity_close"] for p in v2["equity_curve"]]
    assert v2_closes == pytest.approx(v1_closes)


def test_hold_to_end_with_slippage_matches_v1():
    """持仓到结束的虚拟结算（end_of_data）在 slippage>0 下 gross/net 同口径。"""
    opens = [100, 100, 100, 105, 110]
    targets = [0, 1, 1, 1, 1]

    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets(targets),
        initial_cash=1000.0, fee_rate=0.001, slippage=0.002,
    ).run(make_df(opens))

    v2 = run_portfolio({"BTCUSDT": make_df(opens)}, [(t,) for t in targets],
                       fee_rate=0.001, slippage=0.002)

    assert v1["trades"][0]["exit_reason"] == "end_of_data"
    assert v2["trades"][0]["exit_reason"] == "end_of_data"
    assert v2["trades"][0]["gross_pnl"] == pytest.approx(v1["trades"][0]["gross_pnl"])
    assert v2["trades"][0]["pnl"] == pytest.approx(v1["trades"][0]["pnl"])
    assert v2["metrics"]["final_equity"] == pytest.approx(v1["metrics"]["final_equity"])


# =========================================================
# 金样例 P-维持保证金：rate > 0 时两引擎同一公式（名义价值口径）
# =========================================================

def test_maintenance_margin_rate_unified_with_v1():
    """维持保证金 = rate × 名义价值（按最不利价估值）。
    手算：lev=10、entry=100、qty=100、cash=1000、rate=5% →
    强平价 = (100×100 − 1000) / (100×(1−0.05)) = 9000/95 = 94.7368…"""
    opens = [100, 100, 100, 100]
    lows = [100, 100, 90, 100]
    targets = [1, 1, 1, 1]

    expected_liq_price = 9000.0 / 95.0

    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets(targets),
        initial_cash=1000.0, leverage=10,
        maintenance_margin_rate=0.05,
    ).run(make_df(opens, lows=lows))

    v2 = run_portfolio(
        {"BTCUSDT": make_df(opens, lows=lows)},
        [(t,) for t in targets],
        leverage=10, maintenance_margin_rate=0.05,
    )

    assert v1["metrics"]["liquidation_count"] == 1
    assert v2["metrics"]["liquidation_count"] == 1

    assert v1["liquidation_events"][0]["liquidation_price"] == pytest.approx(expected_liq_price)
    assert v2["liquidation_events"][0]["legs"][0]["liquidation_price"] == pytest.approx(expected_liq_price)

    # 强平后权益恰好等于维持线 = 5% × 100 × 强平价
    expected_equity = 0.05 * 100.0 * expected_liq_price
    assert v1["metrics"]["final_equity"] == pytest.approx(expected_equity)
    assert v2["metrics"]["final_equity"] == pytest.approx(expected_equity)


# =========================================================
# position_size 语义差异（有意设计，见契约 §10.2 推论）
# =========================================================

def test_position_size_semantics_differ_by_design():
    """v1 的 position_size 是入场时一次性保证金比例（此后不调仓）；
    v2 是持续维持的权重缩放系数（漂移超阈值即再平衡）。
    单边上涨中 v2 的常数混合会持续卖出浮盈，终值低于 v1 买入持有——
    此测试把这一固有差异钉死为有意行为而非回归。"""
    opens = [round(100.0 * 1.1 ** i, 4) for i in range(10)]
    targets = [0] + [1] * 9

    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets(targets),
        initial_cash=1000.0, position_size=0.5,
    ).run(make_df(opens))

    v2 = run_portfolio({"BTCUSDT": make_df(opens)}, [(t,) for t in targets],
                       position_size=0.5)

    # v1：一笔持有到结束；v2：开仓后持续再平衡产生多笔成交
    assert len(v1["trades"]) == 1
    assert v1["trades"][0]["exit_reason"] == "end_of_data"
    assert v2["metrics"]["fill_count"] > 2

    # 单边上涨中常数混合跑输买入持有
    assert v2["metrics"]["final_equity"] < v1["metrics"]["final_equity"]
