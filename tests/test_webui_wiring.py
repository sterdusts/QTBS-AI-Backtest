"""
webUI 事件处理函数契约金样例（不启动 Gradio，仅校验回调 arity 与渲染）：
- run_backtest_from_ui 必须始终返回 (仪表盘HTML, 回测图路径, 稳健性报告HTML, 稳健性图路径)
  四元组——含全部早期校验失败分支，否则与 outputs 数量不符、Gradio 抛 "too few output values"。
- 查看历史回调 on_select_history 重渲仪表盘 + 详情 + 稳健性，并透传两个图表路径 State。

导入 webUI 会在模块级构建整套 Blocks，顺带冒烟「界面能搭起来」。
"""
import os

import webUI
from module.modules.run_history import build_run_record, save_run_record


def test_run_backtest_empty_code_yields_quad():
    # run_backtest_from_ui 是生成器（异步分步：先出回测、再补稳健性）。空策略代码早期
    # 校验失败：仅 yield 一次四元组（错误HTML, None, 稳健性HTML, None）。
    outs = list(webUI.run_backtest_from_ui(
        "", "zh", "crypto", "BTC", "4h", "2024-01-01", "2024-02-01",
        1000, 1, 10, 0.05, 0.02, "",
    ))
    assert len(outs) == 1
    out = outs[0]
    assert isinstance(out, tuple) and len(out) == 4
    assert "qd" not in out[0] or "padding" in out[0]   # 错误红框 HTML
    assert out[1] is None and out[3] is None           # 无回测图/稳健性图路径


def test_run_backtest_bad_params_yields_quad():
    # 参数解析失败（非法初始资金）同样仅 yield 一次四元组
    outs = list(webUI.run_backtest_from_ui(
        "CONTRACT_VERSION = 1", "zh", "crypto", "BTC", "4h",
        "2024-01-01", "2024-02-01", -5, 1, 10, 0.05, 0.02, "",
    ))
    assert len(outs) == 1
    out = outs[0]
    assert isinstance(out, tuple) and len(out) == 4
    assert out[1] is None and out[3] is None


def test_on_select_history_none_is_noop():
    # 无选中（下拉清空/刷新重置）：主仪表盘+稳健性 gr.update 空操作、详情清空、两个图表路径
    # 【原样回传】，绝不覆盖当前回测结果；尤其不得对 gr.State 写 gr.update() 占位 dict
    out = webUI.on_select_history(None, "zh", "Past_data/keep.html", "Past_data/r.html")
    assert isinstance(out, tuple) and len(out) == 5
    dash_update, detail, chart_path, robust_update, robust_chart = out
    assert isinstance(dash_update, dict)            # gr.update() 占位，不改主仪表盘
    assert detail == ""                             # 清空详情
    assert chart_path == "Past_data/keep.html"      # 回测图路径原样回传（非 gr.update dict）
    assert isinstance(robust_update, dict)          # 稳健性报告 gr.update() 占位
    assert robust_chart == "Past_data/r.html"       # 稳健性图路径原样回传


def test_on_select_history_renders_into_main_dashboard(tmp_path):
    rec = build_run_record(
        prompt="MA20 上穿 MA60 开多", strategy_code="def strategy(ctx):\n    return {}",
        market="crypto",
        params={"symbol": "BTCUSDT", "timeframe": "4h", "actual_start": "2024-01-01",
                "actual_end": "2024-06-01", "kline_count": 100, "initial_cash": 1000.0},
        metrics={"initial_cash": 1000.0, "final_equity": 1200.0, "total_return_pct": 20.0,
                 "trade_count": 1, "net_win_rate": 100.0},
        summary="总收益率：20.00%", chart_file="Past_data/x.html",
        timestamp_utc="2024-06-01 00:00:00 UTC",
        trades=[{"side": "long", "net_pnl": 200.0, "entry_price": 100.0, "exit_price": 120.0,
                 "entry_time": "2024-01-01 00:00:00", "exit_time": "2024-02-01 00:00:00",
                 "exit_reason": "take_profit", "max_abs_qty": 2.0}],
        equity=[1000, 1100, 1200],
        robustness="【稳健性分析】BTCUSDT\n样本外平均夏普：1.20",
        robustness_chart="Past_data/r.html")
    path = save_run_record(rec, output_dir=str(tmp_path))

    dash, detail, chart_path, robust_html, robust_chart = webUI.on_select_history(
        path, "zh", None, None)
    assert "历史成交" in dash and "止盈" in dash        # 主仪表盘重渲成交卡片 + 原因
    assert "自然语言提示词" in detail and "MA20 上穿 MA60" in detail
    assert "回测代码" in detail and "回测参数" in detail
    assert chart_path == "Past_data/x.html"            # 回测图路径切到该次记录
    assert "样本外平均夏普" in robust_html              # 稳健性报告重渲
    assert robust_chart == "Past_data/r.html"          # 稳健性图路径切到该次记录


def test_after_backtest_history_refreshes_and_clears():
    # 回测后：下拉补给 + 清空详情；不动主仪表盘
    upd, detail = webUI.after_backtest_history("zh")
    assert isinstance(upd, dict) and "choices" in upd
    assert detail == ""


def test_refresh_history_choices_returns_update():
    upd = webUI.refresh_history_choices("zh")
    assert isinstance(upd, dict) and "choices" in upd   # gr.update(choices=...)


def test_public_ai_labels_do_not_expose_provider_name():
    for text in webUI.UI_TEXTS.values():
        assert "DeepSeek" not in text["code_output_label"]
        assert "DeepSeek" not in text["api_fail_error"]
