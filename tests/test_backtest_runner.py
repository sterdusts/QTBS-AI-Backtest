"""
backtest_runner（无 Gradio 回测内核）回归测试：抽出的 run_prepared/run_once 必须
与直接构造引擎 run() 产出完全一致——保证 webUI 单次回测语义在重构前后不变、
且稳健性分析层与单次回测走同一内核不分叉。

合成数据 + 注入 prepared，不依赖网络/真实数据/funding 文件（funding_dir 指向空
tmp 目录 ⇒ build_funding_rates 返回 None）。
"""

import pytest

from module.Strategy import backtest_runner
from module.Strategy.backtest_runner import (
    InsufficientKlinesError,
    resolve_strategy_route,
    run_prepared,
)
from module.Strategy.strategy_loader import load_strategy_func_from_code
from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.data_panel import filter_df_by_date
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from tests.helpers import make_df

V1_CODE = (
    "import pandas as pd\nimport numpy as np\n\n"
    "CONTRACT_VERSION = 1\n\n"
    "def generate_signals(df):\n"
    "    df = df.copy()\n"
    "    df['target_position'] = 1\n"
    "    return df\n"
)

V2_CODE = (
    "import pandas as pd\nimport numpy as np\n\n"
    "CONTRACT_VERSION = 2\nSYMBOLS = ['BTCUSDT']\n\n"
    "def generate_signals(data):\n"
    "    idx = data['BTCUSDT'].index\n"
    "    return pd.DataFrame({'BTCUSDT': 1.0}, index=idx)\n"
)


def _closes(res):
    return [p["equity_close"] for p in res["equity_curve"]]


def _prep_v1(df):
    return {"route_version": 1, "route_symbols": ["BTCUSDT"], "strategy_code": V1_CODE,
            "timeframe": "4h", "data": None, "df": df, "display_symbol": "BTCUSDT"}


def _prep_v2(data):
    return {"route_version": 2, "route_symbols": ["BTCUSDT"], "strategy_code": V2_CODE,
            "timeframe": "4h", "data": data, "df": None, "display_symbol": "BTCUSDT"}


def test_run_prepared_v1_matches_direct_engine(tmp_path):
    df = make_df([100, 101, 102, 101, 103, 104, 105, 106])
    run = run_prepared(_prep_v1(df), dict(initial_cash=1000.0, fee_rate=0.001),
                       min_klines=1, funding_dir=str(tmp_path))
    direct = CodeBacktestCore(
        strategy_func=load_strategy_func_from_code(V1_CODE),
        initial_cash=1000.0, fee_rate=0.001,
    ).run(df)
    assert run["result"]["metrics"]["final_equity"] == pytest.approx(direct["metrics"]["final_equity"])
    assert _closes(run["result"]) == pytest.approx(_closes(direct))
    assert run["route_version"] == 1
    assert run["kline_count"] == len(df)
    assert run["display_symbol"] == "BTCUSDT"


def test_run_prepared_v2_matches_direct_engine(tmp_path):
    data = {"BTCUSDT": make_df([100, 101, 102, 101, 103, 104])}
    run = run_prepared(_prep_v2(data), dict(initial_cash=1000.0),
                       min_klines=1, funding_dir=str(tmp_path))
    direct = PortfolioBacktestCore(
        strategy_func=load_strategy_func_from_code(V2_CODE), initial_cash=1000.0,
    ).run(data)
    assert run["result"]["metrics"]["final_equity"] == pytest.approx(direct["metrics"]["final_equity"])
    assert _closes(run["result"]) == pytest.approx(_closes(direct))


def test_run_prepared_subwindow_slices_to_subrange(tmp_path):
    df = make_df([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    sub_end = str(df.index[4])  # 前 5 根
    run = run_prepared(_prep_v1(df), dict(initial_cash=1000.0),
                       min_klines=1, sub_end=sub_end, funding_dir=str(tmp_path))
    assert run["kline_count"] == 5
    sub_df = filter_df_by_date(df, None, sub_end)
    direct = CodeBacktestCore(
        strategy_func=load_strategy_func_from_code(V1_CODE), initial_cash=1000.0,
    ).run(sub_df)
    assert _closes(run["result"]) == pytest.approx(_closes(direct))


def test_insufficient_klines_raises(tmp_path):
    df = make_df([100, 101, 102])
    with pytest.raises(InsufficientKlinesError) as exc:
        run_prepared(_prep_v1(df), dict(initial_cash=1000.0),
                     min_klines=100, funding_dir=str(tmp_path))
    assert exc.value.kline_count == 3


def test_resolve_strategy_route():
    assert resolve_strategy_route(V1_CODE, "ETH") == (1, ["ETHUSDT"])
    assert resolve_strategy_route(V2_CODE, "ETH") == (2, ["BTCUSDT"])


def test_spot_prices_do_not_auto_apply_perpetual_funding(tmp_path, monkeypatch):
    calls = []

    def fake_build(symbols, index, funding_dir):
        calls.append((symbols, len(index)))
        return {"BTCUSDT": [0.01] * len(index)}

    monkeypatch.setattr(backtest_runner, "build_funding_rates", fake_build)
    df = make_df([100, 100, 100, 100])

    spot = _prep_v1(df)
    spot["price_market"] = "spot"
    spot_run = run_prepared(
        spot, dict(initial_cash=1000.0), min_klines=1,
        funding_dir=str(tmp_path),
    )
    assert calls == []
    assert spot_run["result"]["metrics"]["funding_enabled"] is False

    perpetual = _prep_v1(df)
    perpetual["price_market"] = "perpetual"
    perp_run = run_prepared(
        perpetual, dict(initial_cash=1000.0), min_klines=1,
        funding_dir=str(tmp_path),
    )
    assert calls == [(["BTCUSDT"], len(df))]
    assert perp_run["result"]["metrics"]["funding_enabled"] is True
