"""
Phase 3 Stage 5：run_full_analysis 一站式编排 + robustness_chart 渲染冒烟。
注入 prepared 绕过真实数据加载；funding_dir 指向空 tmp。报告严格 JSON-able。
"""

import json
import os

from module.analysis import robustness
from module.modules.robustness_chart import plot_robustness
from tests.helpers import make_df

PARAM_LS = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n"
    'PARAM_SPACE = {"mode": [-1, 1]}\n\n'
    "def generate_signals(df, params=None):\n"
    "    p = params or {}\n"
    "    df = df.copy()\n"
    "    df['target_position'] = p.get('mode', 1)\n"
    "    return df\n"
)
V1_LONG = (
    "import pandas as pd\nimport numpy as np\n\nCONTRACT_VERSION = 1\n\n"
    "def generate_signals(df):\n    df = df.copy()\n    df['target_position'] = 1\n    return df\n"
)


def _prep(df, code):
    return {"route_version": 1, "route_symbols": ["BTCUSDT"], "strategy_code": code,
            "timeframe": "4h", "data": None, "df": df, "display_symbol": "BTCUSDT"}


def test_run_full_analysis_with_param_space(tmp_path):
    df = make_df(list(range(100, 160)))  # 60 根
    rep = robustness.run_full_analysis(
        PARAM_LS, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        min_window_bars=5, prepared=_prep(df, PARAM_LS), funding_dir=str(tmp_path))
    assert rep["available"] is True
    assert rep["in_out"]["type"] == "in_out"
    assert rep["walk_forward"]["type"] == "walk_forward"
    assert rep["param_scan"] is not None and rep["param_scan"]["scan_target"] == "strategy"
    assert rep["meta"]["has_param_space"] is True
    json.dumps(rep, allow_nan=False)  # 严格 JSON-able


def test_run_full_analysis_no_param_space(tmp_path):
    df = make_df(list(range(100, 160)))
    rep = robustness.run_full_analysis(
        V1_LONG, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        min_window_bars=5, prepared=_prep(df, V1_LONG), funding_dir=str(tmp_path))
    assert rep["available"] is True
    assert rep["param_scan"] is None
    assert rep["meta"]["has_param_space"] is False
    assert rep["walk_forward"]["optimized"] is False  # 无 PARAM_SPACE ⇒ 稳定性扫描


def test_run_full_analysis_insufficient(tmp_path):
    df = make_df(list(range(100, 106)))  # 6 根
    rep = robustness.run_full_analysis(
        V1_LONG, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        min_window_bars=20, prepared=_prep(df, V1_LONG), funding_dir=str(tmp_path))
    assert rep["available"] is False
    assert "不足" in rep["reason"]


def test_plot_robustness_renders(tmp_path):
    df = make_df(list(range(100, 160)))
    rep = robustness.run_full_analysis(
        PARAM_LS, "BTC", "4h", "s", "e", engine_params={"initial_cash": 1000.0},
        min_window_bars=5, prepared=_prep(df, PARAM_LS), funding_dir=str(tmp_path))
    # 六语言各渲染一次，验证标签表与 pyecharts API 全通
    for lang in ("zh", "en", "ja", "ko", "ru", "ar"):
        path = plot_robustness(rep, output_dir=str(tmp_path),
                               file_prefix=f"rb_{lang}", language=lang, auto_open=False)
        assert path is not None and os.path.exists(path)
        assert os.path.getsize(path) > 2000  # 非空 HTML


def test_plot_robustness_unavailable_returns_none(tmp_path):
    assert plot_robustness({"available": False, "reason": "x"}, output_dir=str(tmp_path)) is None
