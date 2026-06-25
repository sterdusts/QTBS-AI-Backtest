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
    assert back["record_version"] == "run_v1"
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
