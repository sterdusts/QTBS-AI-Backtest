"""
KlineBuilder 重采样与防未来函数测试。
"""

import pandas as pd
import pytest

from module.modules.kline_builder import KlineBuilder


def make_1m_df(start, periods):
    """price = 100 + 分钟序号，便于手算聚合结果。"""
    idx = pd.date_range(start, periods=periods, freq="1min")
    prices = [100.0 + i for i in range(periods)]
    return pd.DataFrame({
        "open_time": idx,
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": 2.0,
    })


# =========================================================
# 重采样聚合正确性
# =========================================================

def test_4h_aggregation():
    # 00:00 - 07:59，整 8 小时 = 两根完整 4h K线
    builder = KlineBuilder(make_1m_df("2024-01-01 00:00", 480))
    df_4h = builder.build("4h")

    assert len(df_4h) == 2

    bar1 = df_4h.iloc[0]
    assert bar1["open"] == pytest.approx(100.0)
    assert bar1["close"] == pytest.approx(339.0)
    assert bar1["high"] == pytest.approx(339.0)
    assert bar1["low"] == pytest.approx(100.0)
    assert bar1["volume"] == pytest.approx(480.0)   # 240 根 × 2.0

    bar2 = df_4h.iloc[1]
    assert bar2["open"] == pytest.approx(340.0)
    assert bar2["close"] == pytest.approx(579.0)

    # close_time = open_time + 周期
    assert df_4h["close_time"].iloc[0] == pd.Timestamp("2024-01-01 04:00")
    assert df_4h["close_time"].iloc[1] == pd.Timestamp("2024-01-01 08:00")


# =========================================================
# 未完成 K 线裁剪
# =========================================================

def test_drop_incomplete_bar():
    # 00:00 - 08:29：第三根 4h K线 [08:00, 12:00) 未完成，应删除
    builder = KlineBuilder(make_1m_df("2024-01-01 00:00", 510))

    assert len(builder.build("4h", drop_incomplete=True)) == 2
    assert len(builder.build("4h", drop_incomplete=False)) == 3


def test_ghost_timestamp_rows_dropped():
    """损坏时间戳产出的 1677 年哨兵值幽灵行必须在清洗时丢弃，
    否则 resample 会物化数百年分箱直接 OOM。"""
    raw = make_1m_df("2024-01-01 00:00", 240)
    ghost = raw.iloc[[0]].copy()
    ghost["open_time"] = pd.Timestamp("1677-09-21 00:12:43.145224193")
    raw = pd.concat([ghost, raw], ignore_index=True)

    builder = KlineBuilder(raw)

    assert len(builder.df_1m) == 240
    assert builder.df_1m.index.min() == pd.Timestamp("2024-01-01 00:00")


def test_exact_boundary_bar_kept():
    """
    回归测试：数据恰好结束于周期边界前 1 分钟时（00:00-03:59），
    [00:00, 04:00) 这根 K 线实际已完整，不应被误删。
    """
    builder = KlineBuilder(make_1m_df("2024-01-01 00:00", 240))
    df_4h = builder.build("4h")

    assert len(df_4h) == 1
    assert df_4h["close"].iloc[0] == pytest.approx(339.0)


# =========================================================
# 高周期 → 低周期映射防未来函数
# =========================================================

def test_map_higher_to_lower_no_lookahead():
    lower_idx = pd.date_range("2024-01-01 00:00", periods=10, freq="1h")
    lower = pd.DataFrame({"close": 100.0}, index=lower_idx)

    higher = pd.DataFrame(
        {
            "close_time": [pd.Timestamp("2024-01-01 04:00"), pd.Timestamp("2024-01-01 08:00")],
            "feat": [1.0, 2.0],
        },
        index=[pd.Timestamp("2024-01-01 00:00"), pd.Timestamp("2024-01-01 04:00")],
    )

    mapped = KlineBuilder.map_higher_to_lower(lower, higher, feature_cols=["feat"])

    # 高周期收盘前不可见
    assert pd.isna(mapped.loc["2024-01-01 03:00", "feat"])
    # 04:00 收盘后第一根 4h 的特征可用
    assert mapped.loc["2024-01-01 04:00", "feat"] == pytest.approx(1.0)
    assert mapped.loc["2024-01-01 07:00", "feat"] == pytest.approx(1.0)
    # 08:00 后切换到第二根
    assert mapped.loc["2024-01-01 08:00", "feat"] == pytest.approx(2.0)
    assert mapped.loc["2024-01-01 09:00", "feat"] == pytest.approx(2.0)


def test_validate_no_future_mapping():
    lower_idx = pd.date_range("2024-01-01 00:00", periods=10, freq="1h")
    lower = pd.DataFrame({"close": 100.0}, index=lower_idx)

    higher = pd.DataFrame(
        {
            "close_time": [pd.Timestamp("2024-01-01 04:00")],
            "feat": [1.0],
        },
        index=[pd.Timestamp("2024-01-01 00:00")],
    )

    mapped = KlineBuilder.map_higher_to_lower(
        lower, higher, feature_cols=["feat"], keep_higher_close_time=True,
    )

    assert KlineBuilder.validate_no_future_mapping(mapped) is True
