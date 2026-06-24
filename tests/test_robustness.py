"""
稳健性分析（Phase 3）金样例：split_in_out_sample / scan_engine_params / walk_forward
+ _sanitize_metrics。合成数据 + 注入 prepared（绕过真实数据加载）、funding_dir 指向空
tmp（无 funding）。严格 JSON-able（json.dumps(allow_nan=False)）。
"""

import json

import pytest

from module.analysis import robustness
from module.Strategy.backtest_runner import run_prepared
from tests.helpers import make_df

# 永远做多（产生交易）；永远空仓（产生空窗口 trade_count==0）
V1_LONG = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n\n"
    "def generate_signals(df):\n    df = df.copy()\n    df['target_position'] = 1\n    return df\n"
)
V1_FLAT = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n\n"
    "def generate_signals(df):\n    df = df.copy()\n    df['target_position'] = 0\n    return df\n"
)
# 契约 v3 可参数化：mode=1 做多 / mode=-1 做空（PARAM_SPACE 静态声明）
PARAM_LS = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n"
    'PARAM_SPACE = {"mode": [-1, 1]}\n\n'
    "def generate_signals(df, params=None):\n"
    "    p = params or {}\n"
    "    df = df.copy()\n"
    "    df['target_position'] = p.get('mode', 1)\n"
    "    return df\n"
)


def _prep(df, code=V1_LONG):
    return {"route_version": 1, "route_symbols": ["BTCUSDT"], "strategy_code": code,
            "timeframe": "4h", "data": None, "df": df, "display_symbol": "BTCUSDT"}


def _json_ok(obj):
    json.dumps(obj, allow_nan=False)  # inf/NaN 未归一会抛
    return True


# =========================================================
# _sanitize_metrics
# =========================================================

def test_sanitize_metrics_inf_nan_to_none():
    m = {"sharpe_ratio": float("inf"), "profit_factor": float("nan"),
         "total_return_pct": 12.5, "trade_count": 3, "symbols": ["BTCUSDT"]}
    s = robustness._sanitize_metrics(m)
    assert s["sharpe_ratio"] is None
    assert s["profit_factor"] is None
    assert s["total_return_pct"] == 12.5
    assert s["trade_count"] == 3
    assert _json_ok(s)


# =========================================================
# split_in_out_sample
# =========================================================

def test_split_in_out_sample_boundary_and_match(tmp_path):
    df = make_df([100, 101, 102, 103, 102, 104, 105, 106, 107, 108])  # 10 根
    prep = _prep(df)
    res = robustness.split_in_out_sample(
        V1_LONG, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        split_ratio=0.7, min_klines=1, prepared=prep, funding_dir=str(tmp_path),
    )
    # t = int(10*0.7) = 7：IS 7 根、OOS 3 根
    assert res["in_sample"]["kline_count"] == 7
    assert res["out_sample"]["kline_count"] == 3
    assert res["boundary_time"] == str(df.index[7])
    assert res["degradation"]["available"] is True
    assert _json_ok(res)

    # IS 段（前 7 根，iloc 精确切）指标与直接对该子窗 run 一致
    direct_is = run_prepared(_prep(df.iloc[0:7]), {"initial_cash": 1000.0},
                             min_klines=1, funding_dir=str(tmp_path))
    assert res["in_sample"]["metrics"]["final_equity"] == pytest.approx(
        direct_is["result"]["metrics"]["final_equity"])


def test_split_invalid_ratio_raises(tmp_path):
    df = make_df([100, 101, 102, 103, 104, 105])
    with pytest.raises(ValueError):
        robustness.split_in_out_sample(V1_LONG, "BTC", "4h", "s", "e",
                                       split_ratio=1.0, prepared=_prep(df), funding_dir=str(tmp_path))


def test_split_empty_strategy_flags(tmp_path):
    df = make_df([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    res = robustness.split_in_out_sample(
        V1_FLAT, "BTC", "4h", "s", "e", split_ratio=0.5,
        min_klines=1, prepared=_prep(df, V1_FLAT), funding_dir=str(tmp_path),
    )
    assert res["in_sample"]["empty"] is True
    assert res["out_sample"]["empty"] is True
    assert any("空窗口" in f for f in res["degradation"]["overfit_flags"])
    assert _json_ok(res)


# =========================================================
# scan_engine_params
# =========================================================

def test_scan_engine_params_2d_shape_and_match(tmp_path):
    df = make_df([100, 101, 102, 101, 103, 104, 105, 106])
    prep = _prep(df)
    res = robustness.scan_engine_params(
        V1_LONG, "BTC", "4h", "s", "e",
        base_engine_params={"initial_cash": 1000.0},
        param_grid={"leverage": [1, 2], "fee_rate": [0.0, 0.001]},
        metric="final_equity", min_klines=1, prepared=prep, funding_dir=str(tmp_path),
    )
    assert res["x_param"] == "leverage" and res["x_values"] == [1, 2]
    assert res["y_param"] == "fee_rate" and res["y_values"] == [0.0, 0.001]
    assert len(res["matrix"]) == 2 and all(len(r) == 2 for r in res["matrix"])  # 2×2
    assert len(res["cells"]) == 4
    assert _json_ok(res)

    # 取一格 (leverage=2, fee_rate=0.001) 与直接构造该参数 run 一致
    direct = run_prepared(prep, {"initial_cash": 1000.0, "leverage": 2, "fee_rate": 0.001},
                          min_klines=1, funding_dir=str(tmp_path))
    cell = next(c for c in res["cells"] if c["x"] == 2 and c["y"] == 0.001)
    assert cell["metrics"]["final_equity"] == pytest.approx(direct["result"]["metrics"]["final_equity"])


def test_scan_engine_params_1d(tmp_path):
    df = make_df([100, 101, 102, 103, 104, 105])
    res = robustness.scan_engine_params(
        V1_LONG, "BTC", "4h", "s", "e", base_engine_params={"initial_cash": 1000.0},
        param_grid={"leverage": [1, 2, 3]}, metric="final_equity",
        min_klines=1, prepared=_prep(df), funding_dir=str(tmp_path),
    )
    assert res["y_param"] is None
    assert len(res["matrix"]) == 1 and len(res["matrix"][0]) == 3
    assert _json_ok(res)


def test_scan_too_many_dims_raises(tmp_path):
    df = make_df([100, 101, 102, 103])
    with pytest.raises(ValueError):
        robustness.scan_engine_params(V1_LONG, "BTC", "4h", "s", "e", {},
                                      {"a": [1], "b": [2], "c": [3]},
                                      prepared=_prep(df), funding_dir=str(tmp_path))


# =========================================================
# walk_forward
# =========================================================

def test_walk_forward_rolling_window_count(tmp_path):
    df = make_df(list(range(100, 112)))  # 12 根
    res = robustness.walk_forward(
        V1_LONG, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        train_bars=4, test_bars=2, step_bars=2, anchored=False,
        min_klines=1, prepared=_prep(df), funding_dir=str(tmp_path),
    )
    # k=0..3：oos_end = 4+2*k+2 ≤ 12 ⇒ k≤3 ⇒ 4 段
    assert len(res["windows"]) == 4
    assert res["window_def"]["step_bars"] == 2
    assert res["aggregate"]["total_windows"] == 4
    assert _json_ok(res)


def test_walk_forward_anchored_train_grows(tmp_path):
    df = make_df(list(range(100, 112)))
    res = robustness.walk_forward(
        V1_LONG, "BTC", "4h", "s", "e", train_bars=4, test_bars=2, anchored=True,
        min_klines=1, prepared=_prep(df), funding_dir=str(tmp_path),
    )
    bars = [w["train"]["bars"] for w in res["windows"]]
    assert bars == sorted(bars) and bars[0] < bars[-1]  # 锚定 → 训练窗增长


# =========================================================
# scan_strategy_params（契约 v3 策略参数扫描）
# =========================================================

def test_scan_strategy_params_explicit_grid(tmp_path):
    df = make_df(list(range(100, 110)))  # 上涨 10 根
    prep = _prep(df, PARAM_LS)
    res = robustness.scan_strategy_params(
        PARAM_LS, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        param_grid={"mode": [-1, 1]}, metric="final_equity",
        min_klines=1, prepared=prep, funding_dir=str(tmp_path),
    )
    assert res["scan_target"] == "strategy"
    assert res["x_param"] == "mode" and res["x_values"] == [-1, 1]
    assert res["y_param"] is None
    assert len(res["matrix"]) == 1 and len(res["matrix"][0]) == 2
    # mode=1（做多，上涨）final_equity > mode=-1（做空，上涨）
    eq_short = next(c for c in res["cells"] if c["x"] == -1)["metrics"]["final_equity"]
    eq_long = next(c for c in res["cells"] if c["x"] == 1)["metrics"]["final_equity"]
    assert eq_long > eq_short
    assert _json_ok(res)
    # 与直接 strategy_params=mode:1 run 一致
    direct = run_prepared(prep, {"initial_cash": 1000.0}, min_klines=1,
                          funding_dir=str(tmp_path), strategy_params={"mode": 1})
    assert eq_long == pytest.approx(direct["result"]["metrics"]["final_equity"])


def test_scan_strategy_params_auto_uses_param_space(tmp_path):
    df = make_df(list(range(100, 110)))
    res = robustness.scan_strategy_params(
        PARAM_LS, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        metric="final_equity", min_klines=1, prepared=_prep(df, PARAM_LS), funding_dir=str(tmp_path),
    )
    assert res["x_param"] == "mode" and res["x_values"] == [-1, 1]  # 取自 PARAM_SPACE


def test_scan_strategy_params_no_space_raises(tmp_path):
    df = make_df(list(range(100, 106)))
    with pytest.raises(ValueError, match="PARAM_SPACE"):
        robustness.scan_strategy_params(
            V1_LONG, "BTC", "4h", "s", "e", min_klines=1,
            prepared=_prep(df, V1_LONG), funding_dir=str(tmp_path))


# =========================================================
# walk_forward 真 WFO（IS 寻优 → OOS 评估）
# =========================================================

def test_walk_forward_true_wfo_optimizes_is(tmp_path):
    df = make_df(list(range(100, 130)))  # 30 根上涨
    res = robustness.walk_forward(
        PARAM_LS, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        train_bars=8, test_bars=4, step_bars=4, optimize_metric="total_return_pct",
        min_klines=1, prepared=_prep(df, PARAM_LS), funding_dir=str(tmp_path),
    )
    assert res["optimized"] is True
    assert res["param_grid"] == {"mode": [-1, 1]}
    assert res["optimize_metric"] == "total_return_pct"
    assert len(res["windows"]) >= 1
    for w in res["windows"]:
        # 上涨段 IS 寻优必选做多（mode=1 正收益 > mode=-1 负收益）
        assert w["train"]["chosen_params"] == {"mode": 1}
        assert w["train"]["is_optimized"] is True
        assert w["train"]["candidates_evaluated"] == 2  # 两候选都成交、都参选
        if not w["test"].get("empty") and not w["test"].get("insufficient"):
            assert w["test"]["metrics"]["total_return_pct"] > 0  # OOS 用做多 → 上涨正收益
    assert _json_ok(res)


def test_walk_forward_no_param_space_is_stability_scan(tmp_path):
    df = make_df(list(range(100, 112)))
    res = robustness.walk_forward(
        V1_LONG, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        train_bars=4, test_bars=2, step_bars=2, min_klines=1,
        prepared=_prep(df, V1_LONG), funding_dir=str(tmp_path),
    )
    assert res["optimized"] is False
    assert res["param_grid"] is None
    assert "chosen_params" not in res["windows"][0]["train"]  # 退化模式不寻优
