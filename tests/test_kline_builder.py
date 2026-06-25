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

def test_object_dtype_open_time_does_not_crash():
    """审查发现：object 列里的数值毫秒字符串经无 errors='coerce' 的 to_datetime 会
    OutOfBoundsDatetime 崩溃。现与 load_existing_df 同口径：坏行 coerce→NaT→丢弃，
    不再让回测在数据加载即崩。"""
    raw = make_1m_df("2024-01-01 00:00", 5)
    # 强制 open_time 为 object 且为数值毫秒字符串（损坏 CSV 的典型形态）
    raw["open_time"] = raw["open_time"].astype("int64").astype(str).astype(object)
    KlineBuilder(raw).build("4h")   # 不抛异常即通过（坏行被丢弃，可能为空）


def test_1d_aggregation_does_not_raise_on_day_offset():
    """回归：pandas 4 下 to_offset("1D") 是日历偏移 Day，旧代码 pd.Timedelta(Day)
    抛 'Value must be ... not Day' ⇒ 日线回测在数据加载即崩溃（分钟/小时正常）。
    完整 2 天 1m 必须聚合成 2 根完整日线，且各 1440 根的完整性校验通过。"""
    builder = KlineBuilder(make_1m_df("2024-01-01 00:00", 2880))  # 2 整天
    df_1d = builder.build("1D")
    assert len(df_1d) == 2
    assert df_1d["close_time"].iloc[0] == pd.Timestamp("2024-01-02 00:00")
    assert df_1d["close_time"].iloc[1] == pd.Timestamp("2024-01-03 00:00")


def test_1d_incomplete_day_dropped():
    """日线完整性校验同样按 1440 根计：缺分钟的当天不应伪装成完整日线。"""
    builder = KlineBuilder(make_1m_df("2024-01-01 00:00", 1440 + 720))  # 1 整天 + 半天
    assert len(builder.build("1D", drop_incomplete=True)) == 1     # 半天那根被删
    assert len(builder.build("1D", drop_incomplete=False)) == 2    # 显式保留尾 bar


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


def test_tz_aware_string_open_time_stripped_to_naive():
    """高危 #3 回归：遗留 tz-aware CSV（ISO "...+00:00"）的 open_time 必须在
    清洗阶段统一剥成 tz-naive UTC，否则下游 2010 幽灵行过滤用 tz-naive
    Timestamp 与 tz-aware 索引比较会在 pandas 3.0 抛 TypeError，回测在数据
    加载即崩溃。"""
    # 模拟 pd.read_csv 读出的带时区 ISO 字符串列（下载器写 numeric ms，
    # 但遗留 CSV 可能是这种格式）
    raw = make_1m_df("2024-01-01 00:00", 240)
    raw["open_time"] = [
        f"{t.strftime('%Y-%m-%d %H:%M:%S')}+00:00" for t in raw["open_time"]
    ]

    # 加载不崩溃
    builder = KlineBuilder(raw)

    # 索引统一为 tz-naive
    assert isinstance(builder.df_1m.index, pd.DatetimeIndex)
    assert builder.df_1m.index.tz is None
    assert builder.df_1m.index.min() == pd.Timestamp("2024-01-01 00:00")
    assert len(builder.df_1m) == 240


def test_tz_aware_offset_normalized_to_utc():
    """非 UTC 时区偏移（如 +08:00）剥 tz 前先 tz_convert('UTC')：
    08:00+08:00 应落到 00:00 UTC，而不是把本地墙钟时间当成 UTC。"""
    raw = make_1m_df("2024-01-01 08:00", 60)
    raw["open_time"] = [
        f"{t.strftime('%Y-%m-%d %H:%M:%S')}+08:00" for t in raw["open_time"]
    ]

    builder = KlineBuilder(raw)

    assert builder.df_1m.index.tz is None
    # +08:00 的 08:00 == UTC 00:00
    assert builder.df_1m.index.min() == pd.Timestamp("2024-01-01 00:00")


def test_tz_aware_ghost_row_filter_still_works():
    """剥 tz 后 2010 幽灵行过滤照常生效：tz-aware 输入也不会让 1677 哨兵行
    漏过过滤。"""
    raw = make_1m_df("2024-01-01 00:00", 240)
    ghost = raw.iloc[[0]].copy()
    ghost["open_time"] = pd.Timestamp("1677-09-21 00:12:43.145224193")
    raw = pd.concat([ghost, raw], ignore_index=True)
    raw["open_time"] = [
        f"{pd.Timestamp(t).strftime('%Y-%m-%d %H:%M:%S.%f')}+00:00"
        for t in raw["open_time"]
    ]

    builder = KlineBuilder(raw)

    assert builder.df_1m.index.tz is None
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


def test_internal_partial_bar_dropped():
    raw = make_1m_df("2024-01-01 00:00", 480)
    # 第一根 4h 中间缺一分钟；第二根完整。
    raw = raw[raw["open_time"] != pd.Timestamp("2024-01-01 02:00")]
    builder = KlineBuilder(raw)

    df_4h = builder.build("4h", drop_incomplete=False)

    assert list(df_4h.index) == [pd.Timestamp("2024-01-01 04:00")]


def test_impossible_ohlc_row_dropped():
    raw = make_1m_df("2024-01-01 00:00", 3)
    raw.loc[1, "high"] = raw.loc[1, "open"] - 1

    builder = KlineBuilder(raw)

    assert len(builder.df_1m) == 2


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
