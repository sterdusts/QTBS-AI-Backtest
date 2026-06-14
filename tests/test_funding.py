"""
资金费率（funding）金样例（契约 §10.8）。

覆盖：
- 零费率/None 退化（funding 关闭时逐根行为与无 funding 完全一致——金样例零影响硬门槛）
- 多头付/空头收的符号与数值（v1≡v2 跨引擎逐根一致）
- gross 口径不受 funding 影响（仍为 raw 价差）
- funding 拖入强平（v1≡v2 强平时点与权益一致）
- per-symbol 分标的费率（对冲组合各腿独立结算）
- 单笔 net_pnl 含 funding、两引擎同口径

手算基于 helpers.make_df 的形状约定（freq=4h、起点 2024-01-01、high/low 贴合 open/close）。
funding 在「持仓那根 K 线起点、MTM/强平之前」按 -signed_qty×close×rate 结算，
策略权重在收盘确认、下一根开盘执行（与无 funding 的成交时序一致）。
"""

import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from tests.helpers import make_df, strategy_from_targets, weights_strategy


FLAT5 = [100, 100, 100, 100, 100]


def _run_v1(targets, opens=None, funding=None, **kw):
    opens = FLAT5 if opens is None else opens
    params = dict(initial_cash=1000.0)
    params.update(kw)
    core = CodeBacktestCore(strategy_func=strategy_from_targets(targets), **params)
    return core.run(make_df(opens), funding_rates=funding)


def _run_v2(rows, opens=None, funding=None, symbols=("BTCUSDT",), data=None, **kw):
    if data is None:
        opens = FLAT5 if opens is None else opens
        data = {symbols[0]: make_df(opens)}
    params = dict(initial_cash=1000.0)
    params.update(kw)
    core = PortfolioBacktestCore(
        strategy_func=weights_strategy(rows, list(symbols)), **params
    )
    return core.run(data, funding_rates=funding)


def _closes(result):
    return [p["equity_close"] for p in result["equity_curve"]]


# =========================================================
# 金样例 F1：零费率 / None 退化（向后兼容硬门槛）
# =========================================================

def test_funding_none_zero_equals_baseline():
    targets = [1, 1, 1, 0, 0]

    base = _run_v1(targets)                       # 不传 funding
    none_ = _run_v1(targets, funding=None)        # 显式 None
    zeros = _run_v1(targets, funding=[0.0] * 5)   # 全零费率

    assert _closes(none_) == pytest.approx(_closes(base))
    assert _closes(zeros) == pytest.approx(_closes(base))
    # 三者 metrics 全等价，且 total_funding_cost 恒为 0
    assert base["metrics"]["total_funding_cost"] == 0.0
    assert zeros["metrics"]["total_funding_cost"] == 0.0
    assert zeros["metrics"]["final_equity"] == pytest.approx(base["metrics"]["final_equity"])
    # 单笔 funding_pnl 字段存在且为 0
    assert base["trades"][0]["funding_pnl"] == 0.0


def test_funding_zero_degeneracy_v2():
    rows = [(1,), (1,), (1,), (0,), (0,)]
    base = _run_v2(rows)
    zeros = _run_v2(rows, funding={"BTCUSDT": [0.0] * 5})
    assert _closes(zeros) == pytest.approx(_closes(base))
    assert zeros["metrics"]["total_funding_cost"] == 0.0
    assert zeros["trades"][0]["funding_pnl"] == 0.0


# =========================================================
# 金样例 F2：多头付资金费率（v1≡v2）
# =========================================================

def test_long_pays_funding_v1_equiv_v2():
    r = 0.001  # 每根费率
    # 持仓 3 根（bar1/2/3），qty=10，close=100 ⇒ 每根 -10×100×0.001=-1.0，合计 -3.0
    v1 = _run_v1([1, 1, 1, 0, 0], funding=[r] * 5)
    v2 = _run_v2([(1,), (1,), (1,), (0,), (0,)], funding={"BTCUSDT": [r] * 5})

    for res in (v1, v2):
        assert res["metrics"]["final_equity"] == pytest.approx(997.0)
        assert res["metrics"]["total_funding_cost"] == pytest.approx(3.0)
        tr = res["trades"][0]
        assert tr["net_pnl"] == pytest.approx(-3.0)
        assert tr["funding_pnl"] == pytest.approx(-3.0)
        assert tr["gross_pnl"] == pytest.approx(0.0)  # 平价无价差，gross=0

    assert _closes(v2) == pytest.approx(_closes(v1))


# =========================================================
# 金样例 F3：空头收资金费率（v1≡v2）
# =========================================================

def test_short_receives_funding_v1_equiv_v2():
    r = 0.001
    v1 = _run_v1([-1, -1, -1, 0, 0], funding=[r] * 5)
    v2 = _run_v2([(-1,), (-1,), (-1,), (0,), (0,)], funding={"BTCUSDT": [r] * 5})

    for res in (v1, v2):
        assert res["metrics"]["final_equity"] == pytest.approx(1003.0)
        assert res["metrics"]["total_funding_cost"] == pytest.approx(-3.0)  # 净收
        tr = res["trades"][0]
        assert tr["net_pnl"] == pytest.approx(3.0)
        assert tr["funding_pnl"] == pytest.approx(3.0)

    assert _closes(v2) == pytest.approx(_closes(v1))


# =========================================================
# 金样例 F4：funding 不污染 gross 口径
# =========================================================

def test_funding_does_not_affect_gross():
    r = 0.002
    opens = [100, 100, 100, 110, 110]
    targets = [1, 1, 1, 0, 0]

    base = _run_v1(targets, opens=opens)
    fund = _run_v1(targets, opens=opens, funding=[r] * 5)

    base_tr = base["trades"][0]
    fund_tr = fund["trades"][0]

    # gross 完全不受 funding 影响（仍为 raw 价差）
    assert fund_tr["gross_pnl"] == pytest.approx(base_tr["gross_pnl"])
    assert fund_tr["gross_pnl"] == pytest.approx(100.0)  # qty10 ×(110-100)
    # net 与无 funding 的差额恰为 funding_pnl（多头付，为负）
    assert fund_tr["funding_pnl"] < 0
    assert fund_tr["net_pnl"] == pytest.approx(base_tr["net_pnl"] + fund_tr["funding_pnl"])


# =========================================================
# 金样例 F5：funding 拖入强平（v1≡v2 强平时点与权益一致）
# =========================================================

def test_funding_drives_liquidation_v1_equiv_v2():
    r = 0.05
    opens = [100, 100, 100, 100, 100, 100]
    kw = dict(
        initial_cash=1000.0,
        leverage=10,
        enable_liquidation=True,
        maintenance_margin_rate=0.0,
    )

    # leverage 10 ⇒ qty=100；每根 funding -100×100×0.05=-500，
    # cash 在第二根持仓后归零 ⇒ 被 funding 拖入强平（无 funding 时平价永不爆）。
    # v2 设 rebalance_threshold=10（关闭再平衡）使其与 v1 一样持仓不变——否则
    # 杠杆>1 时 v2 会随 funding 拖低权益主动减仓，这是 v1/v2 固有差异、非 funding bug。
    v1 = CodeBacktestCore(
        strategy_func=strategy_from_targets([1, 1, 1, 1, 1, 0]), **kw
    ).run(make_df(opens), funding_rates=[r] * 6)

    v2 = PortfolioBacktestCore(
        strategy_func=weights_strategy(
            [(1,), (1,), (1,), (1,), (1,), (0,)], ["BTCUSDT"]
        ),
        rebalance_threshold=10.0,
        **kw,
    ).run({"BTCUSDT": make_df(opens)}, funding_rates={"BTCUSDT": [r] * 6})

    # 无 funding 的对照：平价不应触发强平（证明强平确由 funding 引发）
    v1_nofund = CodeBacktestCore(
        strategy_func=strategy_from_targets([1, 1, 1, 1, 1, 0]), **kw
    ).run(make_df(opens))
    assert v1_nofund["metrics"]["liquidation_count"] == 0

    assert v1["metrics"]["liquidation_count"] >= 1
    assert v1["metrics"]["liquidation_count"] == v2["metrics"]["liquidation_count"]
    assert v1["metrics"]["final_equity"] == pytest.approx(v2["metrics"]["final_equity"])
    assert _closes(v2) == pytest.approx(_closes(v1))


# =========================================================
# 金样例 F6：per-symbol 分标的费率（对冲组合各腿独立结算）
# =========================================================

def test_per_symbol_funding_hedge():
    data = {"BTCUSDT": make_df([100] * 5), "ETHUSDT": make_df([10] * 5)}
    rows = [(0, 0), (0.5, -0.5), (0.5, -0.5), (0, 0), (0, 0)]
    funding = {"BTCUSDT": [0.001] * 5, "ETHUSDT": [0.002] * 5}

    res = PortfolioBacktestCore(
        strategy_func=weights_strategy(rows, ["BTCUSDT", "ETHUSDT"]),
        initial_cash=1000.0,
    ).run(data, funding_rates=funding)

    eps = {e["symbol"]: e for e in res["trades"]}
    # 持仓 2 根（bar2/bar3）：
    #   BTC 多头 qty=5  ⇒ 每根 -5×100×0.001=-0.5，合计 -1.0（付）
    #   ETH 空头 qty=-50 ⇒ 每根 -(-50)×10×0.002=+1.0，合计 +2.0（收）
    assert eps["BTCUSDT"]["funding_pnl"] == pytest.approx(-1.0)
    assert eps["ETHUSDT"]["funding_pnl"] == pytest.approx(2.0)
    # 净 funding 现金流 = -1.0 + 2.0 = +1.0（净收）⇒ total_funding_cost = -1.0
    assert res["metrics"]["total_funding_cost"] == pytest.approx(-1.0)


# =========================================================
# 金样例 F7：per-symbol 缺标的按 0、长度不符报错
# =========================================================

def test_funding_missing_symbol_treated_as_zero():
    data = {"BTCUSDT": make_df([100] * 5), "ETHUSDT": make_df([10] * 5)}
    rows = [(0, 0), (0.5, 0.5), (0.5, 0.5), (0, 0), (0, 0)]
    # 只给 BTC 费率，ETH 缺失 ⇒ ETH 按 0 处理（不报错）
    funding = {"BTCUSDT": [0.001] * 5}
    res = PortfolioBacktestCore(
        strategy_func=weights_strategy(rows, ["BTCUSDT", "ETHUSDT"]),
        initial_cash=1000.0,
    ).run(data, funding_rates=funding)
    eps = {e["symbol"]: e for e in res["trades"]}
    assert eps["ETHUSDT"]["funding_pnl"] == pytest.approx(0.0)
    assert eps["BTCUSDT"]["funding_pnl"] < 0  # BTC 多头付


def test_funding_length_mismatch_raises():
    with pytest.raises(ValueError, match="长度"):
        _run_v1([1, 1, 1, 0, 0], funding=[0.001] * 3)  # 3 != 5

    with pytest.raises(ValueError, match="长度"):
        _run_v2([(1,), (1,), (1,), (0,), (0,)], funding={"BTCUSDT": [0.001] * 4})
