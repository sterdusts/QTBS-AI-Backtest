"""
测试共用的合成 K 线构造器与策略包装器。

金样例的手算期望全部基于 make_df 的形状约定（high/low 默认贴合
open/close、freq=4h、起点 2024-01-01）——改这里必须同步核对
test_backtest_core 与 test_portfolio_core 的手算注释。
"""

import pandas as pd


def make_df(opens, closes=None, highs=None, lows=None, freq="4h"):
    """构造合成 K 线。默认 high/low 贴合 open/close，便于手算。"""
    opens = [float(x) for x in opens]
    closes = opens if closes is None else [float(x) for x in closes]
    highs = [max(o, c) for o, c in zip(opens, closes)] if highs is None else [float(x) for x in highs]
    lows = [min(o, c) for o, c in zip(opens, closes)] if lows is None else [float(x) for x in lows]

    idx = pd.date_range("2024-01-01", periods=len(opens), freq=freq)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1.0},
        index=idx,
    )


def strategy_from_targets(targets):
    """把显式 target 序列包装成 v1 策略函数。"""
    def strategy(df):
        assert len(df) == len(targets), "测试用例 targets 长度必须等于 K 线数量"
        df = df.copy()
        df["target_position"] = list(targets)
        return df
    return strategy


def weights_strategy(rows, symbols):
    """把显式权重行序列包装成 v2 策略函数。"""
    def strategy(data):
        index = data[symbols[0]].index
        assert len(index) == len(rows)
        return pd.DataFrame(list(rows), index=index, columns=symbols)
    return strategy
