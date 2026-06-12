"""
portfolio_chart 渲染烟雾测试：跑一次真实组合回测，渲染 HTML，验证产物。
"""

import os

import pandas as pd

from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from module.modules.portfolio_chart import (
    MAX_CHART_POINTS,
    _downsample_positions,
    plot_portfolio_result,
)
from tests.helpers import make_df, weights_strategy


def test_chart_renders(tmp_path):
    data = {
        "BTCUSDT": make_df([100, 100, 100, 100, 110]),
        "ETHUSDT": make_df([10, 10, 10, 10, 8]),
    }

    rows = [(0, 0), (0.5, -0.5), (0.5, -0.5), (0, 0), (0, 0)]
    strategy = weights_strategy(rows, ["BTCUSDT", "ETHUSDT"])

    result = PortfolioBacktestCore(strategy_func=strategy, initial_cash=1000.0).run(data)

    html_path = plot_portfolio_result(
        result=result,
        output_dir=str(tmp_path),
        file_prefix="test_portfolio",
        timeframe="4h",
        language="zh",
        auto_open=False,
    )

    assert os.path.exists(html_path)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert "echarts" in html
    assert "BTCUSDT" in html
    assert "ETHUSDT" in html

    # 历史文件切换器（manifest 方案）：HTML 注入加载器，清单写在 manifest.js
    assert "QTBS_MANIFEST_SWITCHER_START" in html

    manifest_path = os.path.join(str(tmp_path), "qtbs_chart_manifest.js")
    assert os.path.exists(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        assert os.path.basename(html_path) in f.read()


def test_downsample_positions():
    # 未超限：不抽样
    assert _downsample_positions(MAX_CHART_POINTS, set()) is None

    keep = _downsample_positions(100_000, {12_345, 67_891})
    assert keep is not None
    assert keep == sorted(set(keep))             # 升序无重复
    assert keep[0] == 0 and keep[-1] == 99_999   # 首尾保留
    assert 12_345 in keep and 67_891 in keep     # 成交点整点保留
    assert len(keep) <= MAX_CHART_POINTS + 3


def test_chart_supports_all_languages(tmp_path):
    data = {"BTCUSDT": make_df([100, 100, 100, 105, 110])}

    strategy = weights_strategy([(0,), (1,), (1,), (0,), (0,)], ["BTCUSDT"])

    result = PortfolioBacktestCore(strategy_func=strategy, initial_cash=1000.0).run(data)

    for language in ["zh", "en", "ko", "ja", "ru", "ar"]:
        html_path = plot_portfolio_result(
            result=result,
            output_dir=str(tmp_path),
            file_prefix=f"lang_{language}",
            timeframe="4h",
            language=language,
            auto_open=False,
        )
        assert os.path.exists(html_path)
