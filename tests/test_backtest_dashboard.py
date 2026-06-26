"""
回测结果可视化仪表盘 backtest_dashboard 金样例：HTML 含关键数字、权益迷你曲线、
指标卡；空 metrics → 占位；v1/v2 成交费用结构都兼容。
"""
from module.modules.backtest_dashboard import (
    build_dashboard_html,
    build_dashboard_placeholder,
    build_history_detail_html,
)

_LABELS = {
    "initial_cash": "初始资金", "final_equity": "最终权益", "total_return": "总收益率",
    "annual_return": "年化收益", "sharpe_ratio": "夏普比率", "max_drawdown": "最大回撤",
    "net_win_rate": "净胜率", "profit_factor": "Profit Factor", "payoff_ratio": "盈亏比",
    "trade_count": "交易次数",
}

_METRICS = {
    "initial_cash": 1000.0, "final_equity": 1448.63, "total_return_pct": 44.863,
    "annual_return_pct": 34.35, "sharpe_ratio": 5.06, "max_drawdown_pct": -22.87,
    "net_win_rate": 67.19, "profit_factor": 2.46, "payoff_ratio": 1.83,
    "avg_profit": 200.0, "avg_loss": -109.3, "trade_count": 4, "avg_holding_hours": 17.2,
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
    assert "2.46" in h             # Profit Factor 卡
    assert "盈亏比" in h and "1.83" in h   # 新增的盈亏比(payoff_ratio)行
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


def test_dashboard_non_finite_metric_shows_placeholder_not_inf():
    # 毛亏为 0 时 profit_factor=inf：仪表盘不得显示字面 "inf"，应占位符（_num 用 isfinite）
    m = dict(_METRICS, profit_factor=float("inf"), payoff_ratio=float("inf"))
    h = build_dashboard_html(m, _TRADES, _META, _LABELS, "zh")
    assert "inf" not in h.lower()


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


# =========================================================
# 历史成交 + 订单记录卡片列表（VergeX 风格 + CSS 过滤标签）
# =========================================================

_TRADES_FULL = [
    {"side": "long", "net_pnl": 300.0, "net_pnl_pct": 29.76, "open_fee": 1.0, "close_fee": 1.0,
     "entry_time": "2024-01-02 08:00:00", "exit_time": "2024-01-05 12:00:00",
     "entry_price": 42000.0, "exit_price": 45000.0, "entry_notional": 4200.0,
     "exit_reason": "take_profit", "max_abs_qty": 0.1},
    {"side": "short", "net_pnl": -151.37, "net_pnl_pct": -15.13, "open_fee": 1.0, "close_fee": 1.0,
     "entry_time": "2024-02-01 00:00:00", "exit_time": "2024-02-03 04:00:00",
     "entry_price": 43000.0, "exit_price": 44500.0, "entry_notional": 4300.0,
     "exit_reason": "stop_loss", "max_abs_qty": 0.1},
]


def test_dashboard_renders_trade_and_order_sections():
    h = build_dashboard_html(_METRICS, _TRADES_FULL, _META, _LABELS, "zh")
    assert "历史成交" in h and "订单记录" in h
    assert "qd-trow" in h and "qd-tabs" in h
    # 成交行：止盈/止损中文 reason
    assert "止盈" in h and "止损" in h
    # 过滤标签三选一
    assert "盈利" in h and "亏损" in h        # 成交过滤
    assert "开仓记录" in h and "平仓记录" in h  # 订单过滤
    # 行 class 供 CSS 过滤：盈利→c1 亏损→c2
    assert "qd-trow c1" in h and "qd-trow c2" in h
    # 入场/出场价
    assert "42,000" in h and "45,000" in h
    # 未截断时不得出现「仅显示前 N 条」误报（2 笔 / 4 条订单都远低于上限）
    assert "仅显示前" not in h
    # 盈亏百分比小字角标（跟在盈亏额后）
    assert "qd-pnlpct" in h and "+29.76%" in h and "-15.13%" in h


def test_dashboard_orders_are_twice_trades():
    # 每笔成交派生 2 条订单（开 + 平）：2 笔 → 4 条订单行
    h = build_dashboard_html(_METRICS, _TRADES_FULL, _META, _LABELS, "zh")
    ord_block = h.split("订单记录", 1)[1]
    # 订单区里开/平动作标签出现
    assert ord_block.count("c1") >= 2 and ord_block.count("c2") >= 2


def test_dashboard_no_trades_no_sections():
    # 零成交：不渲染成交/订单区（避免空标签栏）
    h = build_dashboard_html(_METRICS, [], _META, _LABELS, "zh")
    assert "历史成交" not in h and "订单记录" not in h


def test_dashboard_trade_section_caps_rows():
    # 超量成交只渲染前 _MAX_LIST_ROWS 条并提示「仅显示前 N 条」
    from module.modules.backtest_dashboard import _MAX_LIST_ROWS
    many = _TRADES_FULL * (_MAX_LIST_ROWS)   # 远超上限
    h = build_dashboard_html(_METRICS, many, _META, _LABELS, "zh")
    assert "仅显示前" in h


def test_dashboard_v2_episode_notional_from_qty():
    # v2 片段无 entry_notional：用 max_abs_qty × entry_price 推名义价值
    v2 = [{"side": "long", "net_pnl": 100.0, "fees": 2.5,
           "entry_time": "2024-01-01 00:00:00", "exit_time": "2024-01-02 00:00:00",
           "entry_price": 100.0, "exit_price": 110.0, "max_abs_qty": 5.0,
           "exit_reason": "signal", "symbol": "ETHUSDT"}]
    h = build_dashboard_html(_METRICS, v2, _META, _LABELS, "en")
    assert "ETHUSDT" in h
    assert "500" in h          # 名义价值 = 5 × 100
    assert "Signal" in h       # 英文 reason


# =========================================================
# 历史记录详情（提示词 / 参数 / 代码 / 摘要 独立展示）
# =========================================================

_RECORD = {
    "timestamp_utc": "2024-06-01 12:00:00 UTC",
    "prompt": "MA20 上穿 MA60 开多，下穿平仓，不做空。",
    "strategy_code": "def strategy(ctx):\n    return {'target_position': 1}",
    "params": {"symbol": "BTCUSDT", "timeframe": "4h", "route_symbols": ["BTCUSDT"],
               "initial_cash": 1000.0, "leverage": 3},
    "summary": "总收益率 44.86%\n夏普 5.06",
}


def test_history_detail_contains_all_sections():
    h = build_history_detail_html(_RECORD, "zh")
    assert "自然语言提示词" in h and "MA20 上穿 MA60" in h
    assert "回测代码" in h and "target_position" in h
    assert "回测参数" in h and "BTCUSDT" in h and "4h" in h
    assert "route_symbols" in h and "BTCUSDT" in h   # list 值逗号连接
    assert "回测摘要" in h and "44.86%" in h


def test_history_detail_escapes_html():
    rec = dict(_RECORD, prompt="<script>alert(1)</script>")
    h = build_history_detail_html(rec, "zh")
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


def test_history_detail_no_prompt_placeholder():
    rec = dict(_RECORD, prompt="")
    h = build_history_detail_html(rec, "zh")
    assert "直接粘贴代码回测" in h     # 无提示词占位


def test_history_detail_none_record_is_empty():
    h = build_history_detail_html(None, "en")
    assert "qtbs-dash" in h and "(empty)" in h


def test_history_detail_english_labels():
    h = build_history_detail_html(_RECORD, "en")
    assert "Prompt" in h and "Strategy Code" in h and "Parameters" in h
