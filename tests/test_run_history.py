"""
回测运行历史记录 run_history 金样例：自包含 JSON 落盘/读回、inf/NaN 严格归一、
空提示词留档、目录枚举。
"""
import json
import os

from module.modules.run_history import (
    build_run_record,
    list_run_records,
    load_run_record,
    save_run_record,
)


def _strict_loads(raw):
    """严格解析：JSON 里若出现 Infinity/NaN（非法 JSON），parse_constant 抛错。"""
    def boom(c):
        raise ValueError(f"非法 JSON 常量: {c}")
    return json.loads(raw, parse_constant=boom)


def test_save_and_load_round_trip(tmp_path):
    rec = build_run_record(
        prompt="比特币，5日均线上穿20日均线买入做多",
        strategy_code="CONTRACT_VERSION = 1\ndef generate_signals(df):\n    return df\n",
        market="crypto",
        params={"symbol": "BTCUSDT", "timeframe": "1d", "leverage": 1, "initial_cash": 1000.0},
        metrics={"total_return_pct": 12.5, "annual_return_pct": 8.0,
                 "sharpe_ratio": 1.2, "max_drawdown_pct": -20.0, "trade_count": 3},
        summary="总收益率：12.50%\n年化收益：8.00%\n夏普比率：1.20",
        chart_file="Past_data/x.html",
        timestamp_utc="2026-06-24 05:00:00 UTC",
    )
    path = save_run_record(rec, output_dir=str(tmp_path))
    assert os.path.exists(path) and path.endswith(".json")

    back = load_run_record(path)
    assert back["record_version"] == "run_v3"
    assert back["prompt"].startswith("比特币")
    assert "generate_signals" in back["strategy_code"]
    assert back["params"]["symbol"] == "BTCUSDT"
    # 结构化指标：收益率/年化/夏普/回撤/交易次数全部留存
    assert back["metrics"]["total_return_pct"] == 12.5
    assert back["metrics"]["annual_return_pct"] == 8.0
    assert back["metrics"]["sharpe_ratio"] == 1.2
    assert back["metrics"]["max_drawdown_pct"] == -20.0
    assert back["metrics"]["trade_count"] == 3
    # 人类可读摘要原文也留存
    assert "夏普比率" in back["summary"]
    assert back["chart_file"] == "Past_data/x.html"
    assert path in list_run_records(str(tmp_path))


def test_inf_nan_sanitized_to_null(tmp_path):
    rec = build_run_record(
        prompt="", strategy_code="x", market="crypto", params={},
        metrics={"profit_factor": float("inf"), "sharpe_ratio": float("nan"), "ok": 1.5},
        chart_file=None, timestamp_utc="t",
    )
    path = save_run_record(rec, output_dir=str(tmp_path))
    parsed = _strict_loads(open(path, encoding="utf-8").read())  # 不抛 ⇒ 无 inf/NaN
    assert parsed["metrics"]["profit_factor"] is None
    assert parsed["metrics"]["sharpe_ratio"] is None
    assert parsed["metrics"]["ok"] == 1.5


def test_empty_prompt_allowed(tmp_path):
    # 直接粘贴代码回测（无提示词）也能留档，prompt 归一为空串
    rec = build_run_record(prompt=None, strategy_code="x", market="crypto",
                           params={}, metrics={}, chart_file=None, timestamp_utc="t")
    path = save_run_record(rec, output_dir=str(tmp_path))
    assert load_run_record(path)["prompt"] == ""


def test_list_empty_dir_returns_empty(tmp_path):
    assert list_run_records(str(tmp_path / "nonexistent")) == []


def test_trades_and_equity_persisted_for_rerender(tmp_path):
    # run_v2：成交 + 权益随记录留档，供「查看历史」重渲仪表盘
    trades = [{"side": "long", "net_pnl": 10.0, "entry_price": 100.0, "exit_price": 110.0,
               "entry_time": "2024-01-01 00:00:00", "exit_time": "2024-01-02 00:00:00",
               "exit_reason": "signal", "max_abs_qty": 1.0}]
    rec = build_run_record(
        prompt="x", strategy_code="x", market="crypto", params={}, metrics={},
        chart_file=None, timestamp_utc="t", trades=trades, equity=[1000, 1010, 1005, 1100])
    back = load_run_record(save_run_record(rec, output_dir=str(tmp_path)))
    assert back["trades"][0]["side"] == "long"
    assert back["trades"][0]["exit_reason"] == "signal"
    assert back["equity"] == [1000, 1010, 1005, 1100]


def test_trades_and_equity_default_empty(tmp_path):
    # 不传 trades/equity（旧调用方）：归一为空列表，不为 None、不崩
    rec = build_run_record(prompt="x", strategy_code="x", market="crypto",
                           params={}, metrics={}, chart_file=None, timestamp_utc="t")
    assert rec["trades"] == [] and rec["equity"] == []


def test_robustness_persisted_and_defaults(tmp_path):
    # run_v3：稳健性报告文本 + 图表路径随记录留档；不传时归一为空串/None
    rec = build_run_record(prompt="x", strategy_code="x", market="crypto",
                           params={}, metrics={}, chart_file=None, timestamp_utc="t",
                           robustness="【稳健性分析】...\n样本外平均夏普：1.2",
                           robustness_chart="Past_data/r.html")
    back = load_run_record(save_run_record(rec, output_dir=str(tmp_path)))
    assert "样本外平均夏普" in back["robustness"]
    assert back["robustness_chart"] == "Past_data/r.html"

    rec2 = build_run_record(prompt="x", strategy_code="x", market="crypto",
                            params={}, metrics={}, chart_file=None, timestamp_utc="t")
    assert rec2["robustness"] == "" and rec2["robustness_chart"] is None


def test_trades_capped_and_equity_downsampled(tmp_path):
    from module.modules.run_history import _MAX_STORED_TRADES, _MAX_STORED_EQUITY
    many_trades = [{"side": "long", "net_pnl": float(i)} for i in range(_MAX_STORED_TRADES + 250)]
    long_equity = list(range(_MAX_STORED_EQUITY * 4))
    rec = build_run_record(prompt="x", strategy_code="x", market="crypto",
                           params={}, metrics={}, chart_file=None, timestamp_utc="t",
                           trades=many_trades, equity=long_equity)
    assert len(rec["trades"]) == _MAX_STORED_TRADES           # 成交封顶
    assert len(rec["equity"]) == _MAX_STORED_EQUITY           # 权益降采样
    assert rec["equity"][0] == 0                              # 保留首点
    assert rec["equity"][-1] == long_equity[-1]               # 始终保留最终权益


def test_numpy_scalars_serialize_as_numbers(tmp_path):
    # 审查发现：np.int64 不是 Python int 子类，旧版会经 default=str 存成字符串 "153"
    import numpy as np
    rec = build_run_record(
        prompt="", strategy_code="x", market="crypto",
        params={"kline_count": np.int64(153), "ratio": np.float64(1.5)},
        metrics={"sharpe": np.float64(float("inf"))}, chart_file=None, timestamp_utc="t")
    path = save_run_record(rec, output_dir=str(tmp_path))
    back = load_run_record(path)
    assert back["params"]["kline_count"] == 153 and isinstance(back["params"]["kline_count"], int)
    assert back["params"]["ratio"] == 1.5
    assert back["metrics"]["sharpe"] is None          # np inf 仍归一为 null
    assert '"153"' not in open(path, encoding="utf-8").read()   # 是数字、非字符串
