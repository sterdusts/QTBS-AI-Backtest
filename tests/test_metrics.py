"""
backtest_metrics 边界与口径测试（第五轮审查修复的金样例）。
"""

import pytest

from module.modules.backtest_metrics import calculate_metrics


def test_annual_return_overflow_guarded():
    """极短窗口 + 高收益：年化指数爆 float 上限时给 inf 而不是崩溃。"""
    curve = [
        {"time": "2024-01-01 00:00", "equity": 1000.0, "equity_close": 1000.0},
        {"time": "2024-01-01 00:30", "equity": 1500.0, "equity_close": 1500.0},
    ]

    m = calculate_metrics(curve, [], 1000.0, 1500.0)

    assert m["annual_return_pct"] == float("inf")
    assert m["total_return_pct"] == pytest.approx(50.0)


def test_drawdown_peak_uses_best_trough_uses_worst():
    """峰值按最有利权益、谷底按最不利权益：
    手算 peak=200（bar2 best）、trough=140（bar3 worst）→ -30%。"""
    curve = [
        {"time": "2024-01-01", "equity": 100.0, "equity_close": 100.0,
         "equity_worst": 100.0, "equity_best": 100.0},
        {"time": "2024-01-02", "equity": 200.0, "equity_close": 200.0,
         "equity_worst": 150.0, "equity_best": 200.0},
        {"time": "2024-01-03", "equity": 140.0, "equity_close": 140.0,
         "equity_worst": 140.0, "equity_best": 145.0},
    ]

    m = calculate_metrics(curve, [], 100.0, 140.0)

    assert m["max_drawdown_pct"] == pytest.approx(-30.0)


def test_liquidation_row_no_phantom_peak():
    """强平 bar 的 equity_best 已落地结算后 cash：不会造一个仓位从未
    实现的幽灵峰抬高后续回撤分母。手算：峰=200（bar2），强平 bar3
    结算到 50（best/worst 都是 50），后续无更高峰 → 回撤 -75%。"""
    curve = [
        {"time": "2024-01-01", "equity": 100.0, "equity_close": 100.0,
         "equity_worst": 100.0, "equity_best": 100.0},
        {"time": "2024-01-02", "equity": 200.0, "equity_close": 200.0,
         "equity_worst": 200.0, "equity_best": 200.0},
        # 强平 bar：若 best 仍是虚拟极值（如 250）会造幽灵峰
        {"time": "2024-01-03", "equity": 50.0, "equity_close": 50.0,
         "equity_worst": 50.0, "equity_best": 50.0, "liquidated": True},
    ]

    m = calculate_metrics(curve, [], 100.0, 50.0)

    assert m["max_drawdown_pct"] == pytest.approx(-75.0)


def test_sharpe_skipped_when_equity_non_positive():
    """权益穿零后 pct_change 符号全错：夏普必须跳过而不是输出垃圾值。"""
    values = [1000.0, -100.0, -150.0, -100.0]
    curve = [
        {"time": f"2024-01-0{i + 1}", "equity": v, "equity_close": v}
        for i, v in enumerate(values)
    ]

    m = calculate_metrics(curve, [], 1000.0, -100.0)

    assert m["sharpe_ratio"] == 0.0
