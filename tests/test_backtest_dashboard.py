"""
回测结果可视化仪表盘 backtest_dashboard 金样例：HTML 含关键数字、权益迷你曲线、
指标卡；空 metrics → 占位；v1/v2 成交费用结构都兼容。
"""
from module.modules.backtest_dashboard import (
    build_dashboard_html,
    build_dashboard_placeholder,
)

_LABELS = {
    "initial_cash": "初始资金", "final_equity": "最终权益", "total_return": "总收益率",
    "annual_return": "年化收益", "sharpe_ratio": "夏普比率", "max_drawdown": "最大回撤",
    "net_win_rate": "净胜率", "profit_factor": "盈亏比", "trade_count": "交易次数",
}

_METRICS = {
    "initial_cash": 1000.0, "final_equity": 1448.63, "total_return_pct": 44.863,
    "annual_return_pct": 34.35, "sharpe_ratio": 5.06, "max_drawdown_pct": -22.87,
    "net_win_rate": 67.19, "profit_factor": 2.46, "trade_count": 4, "avg_holding_hours": 17.2,
}
_TRADES = [
    {"side": "long", "net_pnl": 300.0, "open_fee": 1.0, "close_fee": 1.0},
    {"side": "long", "net_pnl": 200.0, "open_fee": 1.0, "close_fee": 1.0},
    {"side": "short", "net_pnl": 100.0, "open_fee": 1.0, "close_fee": 1.0},
    {"side": "short", "net_pnl": -151.37, "open_fee": 1.0, "close_fee": 1.0},
]
_META = {
    "symbol": "BTCUSDT", "timeframe": "1d", "start": "2024-01-01", "end": "2024-06-01",
    "kline_count": 153, "initial_cash": 1000.0, "equity": [1000, 1100, 1050, 1300, 1448.63],
}


def test_dashboard_contains_key_numbers_and_chart():
    h = build_dashboard_html(_METRICS, _TRADES, _META, _LABELS, "zh")
    assert "qtbs-dash" in h
    assert "44.86" in h            # 总收益率大字
    assert "BTCUSDT" in h and "1d" in h
    assert "<svg" in h and "polyline" in h   # 权益迷你曲线
    assert "5.06" in h             # 夏普
    assert "-22.87" in h           # 最大回撤
    assert "2.46" in h             # 盈亏比
    # 胜率卡：3 盈 / 1 亏
    assert "3 盈利 / 1 亏损" in h
    # 多空比卡：2多 / 2空
    assert "2多 / 2空" in h


def test_dashboard_empty_metrics_is_placeholder():
    assert build_dashboard_html(None, [], {}, _LABELS, "zh") == build_dashboard_placeholder("zh")
    assert "qd-empty" in build_dashboard_placeholder("en")


def test_dashboard_v2_fees_key_supported():
    # v2 片段用 "fees"（总）而非 open_fee/close_fee：不应报错，总费用应计入
    v2_trades = [
        {"side": "long", "net_pnl": 100.0, "fees": 2.5},
        {"side": "short", "net_pnl": -40.0, "fees": 1.5},
    ]
    h = build_dashboard_html(_METRICS, v2_trades, _META, _LABELS, "en")
    assert "qtbs-dash" in h
    assert "1 Win / 1 Loss" in h


def test_dashboard_handles_none_metric_values():
    # sharpe/profit_factor 为 None（如零交易/穿零）时不崩，显示占位符
    m = dict(_METRICS, sharpe_ratio=None, profit_factor=None, annual_return_pct=None)
    h = build_dashboard_html(m, _TRADES, _META, _LABELS, "zh")
    assert "qtbs-dash" in h and "—" in h


def test_dashboard_sparkline_filters_non_finite():
    # 审查 F1：权益里含 ±inf/NaN 不得产出 nan 坐标污染整条折线
    meta = dict(_META, equity=[1000, float("inf"), 1100, float("nan"), 1300, float("-inf")])
    h = build_dashboard_html(_METRICS, _TRADES, meta, _LABELS, "zh")
    assert "<svg" in h and "polyline" in h
    assert "nan" not in h.lower()          # 无非法坐标
    assert ",inf" not in h and "inf," not in h


def test_dashboard_empty_dates_no_bare_separator():
    meta = {"symbol": "BTCUSDT", "timeframe": "1d", "equity": [100, 110]}
    h = build_dashboard_html(_METRICS, _TRADES, meta, _LABELS, "zh")
    assert " ~ " not in h   # start/end 都缺时不出现裸的 " ~ "


def test_dashboard_negative_return_red():
    m = dict(_METRICS, total_return_pct=-30.0, final_equity=700.0)
    h = build_dashboard_html(m, _TRADES, _META, _LABELS, "zh")
    assert "#ea3943" in h          # 亏损红
    assert "-30.00%" in h
