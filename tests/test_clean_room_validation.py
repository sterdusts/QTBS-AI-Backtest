"""Independent clean-room engines must agree with hand math and production."""

import numpy as np
import pandas as pd
import pytest

from module.modules.code_backtest_core import CodeBacktestCore
from validation.clean_room_engine import (
    run_portfolio_reference,
    run_single_reference,
)
from tests.helpers import make_df, strategy_from_targets


def test_clean_room_single_hand_calculation():
    df = make_df([100, 100, 100, 105, 110])
    result = run_single_reference(
        df, [0, 1, 1, 0, 0],
        initial_cash=1000.0, fee_rate=0.001,
    )
    assert result["final_equity"] == pytest.approx(1097.9)
    assert result["trade_count"] == 1


@pytest.mark.parametrize("seed", range(12))
def test_random_single_differential(seed):
    rng = np.random.default_rng(seed)
    n = 80
    opens = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    closes = opens * np.exp(rng.normal(0, 0.008, n))
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.02, n))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.02, n))
    df = make_df(opens, closes, highs, lows)
    targets = rng.choice([-1, 0, 1], size=n).tolist()
    params = dict(
        initial_cash=1000.0,
        fee_rate=0.0005,
        slippage=0.0003,
        leverage=1 if seed % 2 == 0 else 3,
        position_size=0.7,
        maintenance_margin_rate=0.02,
    )

    current = CodeBacktestCore(strategy_from_targets(targets), **params).run(df)
    reference = run_single_reference(df, targets, **params)

    current_curve = [x["equity_close"] for x in current["equity_curve"]]
    assert current_curve == pytest.approx(reference["equity_close"], abs=1e-9)
    assert current["metrics"]["final_equity"] == pytest.approx(
        reference["final_equity"], abs=1e-9
    )
    assert current["metrics"]["trade_count"] == reference["trade_count"]


def test_clean_room_portfolio_hand_calculation():
    btc = make_df([100, 100, 110, 110])
    eth = make_df([100, 100, 90, 90])
    weights = pd.DataFrame(
        {"BTCUSDT": [0.5] * 4, "ETHUSDT": [-0.5] * 4},
        index=btc.index,
    )
    result = run_portfolio_reference(
        {"BTCUSDT": btc, "ETHUSDT": eth},
        weights,
        initial_cash=1000.0,
        rebalance_threshold=10.0,
    )
    assert result["final_equity"] == pytest.approx(1100.0)
