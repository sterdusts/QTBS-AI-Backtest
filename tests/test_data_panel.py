"""
data_panel 多资产对齐数据层测试。
"""

import os
import time

import numpy as np
import pandas as pd
import pytest

from module.modules import data_panel, fetch_queue
from module.modules.data_panel import (
    align_klines,
    filter_df_by_date,
    list_local_symbols,
    load_aligned_panel,
    load_symbol_kline,
)
from module.modules.Load_real_kline import kline_file_name


@pytest.fixture(autouse=True)
def clean_cache():
    data_panel.clear_cache()
    fetch_queue.reset()
    yield
    data_panel.clear_cache()
    fetch_queue.reset()


def write_synthetic_csv(data_dir, symbol, start, periods):
    """合成 1m CSV：price = 100 + 分钟序号。"""
    os.makedirs(data_dir, exist_ok=True)

    idx = pd.date_range(start, periods=periods, freq="1min")
    prices = 100.0 + np.arange(periods, dtype=float)

    df = pd.DataFrame({
        "open_time": idx,
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": 1.0,
    })

    # 文件命名走 Load_real_kline 单源：夹具自己手写后缀会绕开
    # 「下载/读取/扫描三方共用同一命名」的回归保护
    path = os.path.join(data_dir, kline_file_name(symbol))
    df.to_csv(path, index=False)
    return path


# =========================================================
# 单标的加载
# =========================================================

def test_load_symbol_kline(tmp_path):
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)

    df = load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)

    assert len(df) == 2
    assert df["open"].iloc[0] == pytest.approx(100.0)
    assert df["close"].iloc[0] == pytest.approx(339.0)
    assert df["open"].iloc[1] == pytest.approx(340.0)


def test_unsupported_timeframe_rejected(tmp_path):
    with pytest.raises(ValueError, match="不支持的周期"):
        load_symbol_kline("BTC", "2h", data_dir=str(tmp_path), auto_fetch=False)


def test_missing_data_without_autofetch_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_symbol_kline("BTC", "4h", data_dir=str(tmp_path), auto_fetch=False)


def test_auto_fetch_failure_raises_actionable_error(tmp_path, monkeypatch):
    """下载层吞掉异常空手而归时，必须报「拉取失败」而不是指向
    一个本就不该存在的文件的 FileNotFoundError。"""
    # 拉取由 fetch_queue 的 worker 执行：patch 它的 Obtain_K（no-op 不落盘）
    monkeypatch.setattr(fetch_queue, "Obtain_K", lambda symbol, save_dir: None)

    with pytest.raises(ValueError, match="自动拉取"):
        load_symbol_kline("BTC", "4h", data_dir=str(tmp_path), auto_fetch=True)


def _wait_queue_idle(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not fetch_queue.is_running():
            return
        time.sleep(0.02)


def test_stale_data_triggers_incremental_refresh(tmp_path, monkeypatch):
    """请求窗口超出本地覆盖：触发一次非阻塞后台增量补拉，同进程内不重复打 API。"""
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)  # 截止 01-01 07:59

    calls = []
    monkeypatch.setattr(
        fetch_queue, "Obtain_K", lambda symbol, save_dir: calls.append(symbol)
    )

    # 增量补拉现在是非阻塞后台 enqueue：回测立即返回，worker 异步拉取
    load_symbol_kline("BTC", "1h", data_dir=data_dir,
                      auto_fetch=True, required_end="2024-02-01")
    _wait_queue_idle()
    assert calls == ["BTCUSDT"]

    load_symbol_kline("BTC", "1h", data_dir=data_dir,
                      auto_fetch=True, required_end="2024-02-01")
    _wait_queue_idle()
    assert calls == ["BTCUSDT"]  # 每进程每标的最多一次


def test_covered_window_skips_refresh(tmp_path, monkeypatch):
    """本地数据已覆盖请求窗口（含当天整天）：不打 API。"""
    data_dir = str(tmp_path)
    # 2 天数据（截止 2024-01-02 23:59）覆盖 required_end=2024-01-01 的整天
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 2 * 1440)

    calls = []
    monkeypatch.setattr(
        fetch_queue, "Obtain_K", lambda symbol, save_dir: calls.append(symbol)
    )

    load_symbol_kline("BTC", "1h", data_dir=data_dir,
                      auto_fetch=True, required_end="2024-01-01")
    assert calls == []


def test_refresh_when_data_ends_within_requested_day(tmp_path, monkeypatch):
    """#32 回归：本地数据落在请求当天内（如截止当天 07:59，窗口要到当天
    23:59）必须补拉，不能因午夜口径误判已覆盖而少近一天数据。"""
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)  # 截止 01-01 07:59

    calls = []
    monkeypatch.setattr(
        fetch_queue, "Obtain_K", lambda symbol, save_dir: calls.append(symbol)
    )

    load_symbol_kline("BTC", "1h", data_dir=data_dir,
                      auto_fetch=True, required_end="2024-01-01")
    _wait_queue_idle()
    assert calls == ["BTCUSDT"]


def test_cache_avoids_rereading_csv(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    path = write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)

    calls = {"n": 0}
    original_read_csv = pd.read_csv

    def counting_read_csv(*args, **kwargs):
        calls["n"] += 1
        return original_read_csv(*args, **kwargs)

    monkeypatch.setattr(data_panel.pd, "read_csv", counting_read_csv)

    load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)
    load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 1  # 第二次命中缓存

    # mtime 变化 → 缓存失效，重新读取
    stat = os.stat(path)
    os.utime(path, (stat.st_atime + 10, stat.st_mtime + 10))

    load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 2


def test_single_slot_builder_cache(tmp_path, monkeypatch):
    """
    清洗后的 1m 帧只保留最近一份（单槽）：
    同一标的换周期不重新解析 CSV，换标的则旧槽被顶替。
    """
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)
    write_synthetic_csv(data_dir, "ETHUSDT", "2024-01-01 00:00", 480)

    calls = {"n": 0}
    original_read_csv = pd.read_csv

    def counting_read_csv(*args, **kwargs):
        calls["n"] += 1
        return original_read_csv(*args, **kwargs)

    monkeypatch.setattr(data_panel.pd, "read_csv", counting_read_csv)

    # 同一标的：1m → 4h 共用同一次解析
    df_1m = load_symbol_kline("BTC", "1m", data_dir=data_dir, auto_fetch=False)
    df_4h = load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 1
    assert len(df_1m) == 480
    assert len(df_4h) == 2

    # 换标的顶替槽位，再回到原标的需要重新解析（4h 仍命中重采样缓存）
    load_symbol_kline("ETH", "1m", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 2
    load_symbol_kline("BTC", "1m", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 3
    load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)
    assert calls["n"] == 3


# =========================================================
# 对齐
# =========================================================

def test_align_klines_union_index():
    btc_idx = pd.date_range("2024-01-01 00:00", periods=10, freq="1h")
    eth_idx = btc_idx[5:]  # ETH 晚上市

    btc = pd.DataFrame({"close": 100.0}, index=btc_idx)
    eth = pd.DataFrame({"close": 10.0}, index=eth_idx)

    aligned = align_klines({"BTCUSDT": btc, "ETHUSDT": eth})

    assert aligned["BTCUSDT"].index.equals(aligned["ETHUSDT"].index)
    assert len(aligned["ETHUSDT"]) == 10

    # ETH 上市前为 NaN，不伪造数据
    assert aligned["ETHUSDT"]["close"].iloc[:5].isna().all()
    assert (aligned["ETHUSDT"]["close"].iloc[5:] == 10.0).all()

    # BTC 完整历史保留
    assert (aligned["BTCUSDT"]["close"] == 100.0).all()


def test_load_aligned_panel(tmp_path):
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)  # 00:00-07:59
    write_synthetic_csv(data_dir, "ETHUSDT", "2024-01-01 04:00", 240)  # 04:00-07:59

    panel = load_aligned_panel(["btc", "ETH"], "1h", data_dir=data_dir, auto_fetch=False)

    assert set(panel.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert panel["BTCUSDT"].index.equals(panel["ETHUSDT"].index)
    assert len(panel["BTCUSDT"]) == 8

    # ETH 前 4 小时未上市 → NaN
    assert panel["ETHUSDT"]["close"].iloc[:4].isna().all()
    assert panel["ETHUSDT"]["close"].iloc[4:].notna().all()


def test_panel_symbol_dedup(tmp_path):
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 240)

    panel = load_aligned_panel(["BTC", "btc", "BTCUSDT"], "1h",
                               data_dir=data_dir, auto_fetch=False)
    assert list(panel.keys()) == ["BTCUSDT"]


# =========================================================
# 本地标的清单
# =========================================================

def test_list_local_symbols(tmp_path):
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 60)
    write_synthetic_csv(data_dir, "ETHUSDT", "2024-01-01 00:00", 60)

    assert list_local_symbols(data_dir) == ["BTCUSDT", "ETHUSDT"]


def test_list_local_symbols_missing_dir(tmp_path):
    assert list_local_symbols(str(tmp_path / "nope")) == []


# =========================================================
# 时间过滤
# =========================================================

def test_filter_df_includes_full_end_day():
    idx = pd.date_range("2024-01-01", "2024-01-15 23:00", freq="1h")
    df = pd.DataFrame({"close": 1.0}, index=idx)

    filtered = filter_df_by_date(df, "2024-01-10", "2024-01-15")

    assert filtered.index.min() == pd.Timestamp("2024-01-10 00:00")
    assert filtered.index.max() == pd.Timestamp("2024-01-15 23:00")


def test_filter_df_end_with_time_component():
    """end 带时间分量时按精确时刻截止（不再只接受 %Y-%m-%d 一种格式）。"""
    idx = pd.date_range("2024-01-01", periods=48, freq="1h")
    df = pd.DataFrame({"close": 1.0}, index=idx)

    filtered = filter_df_by_date(df, "2024-01-01", "2024-01-01 12:00")

    assert filtered.index.max() == pd.Timestamp("2024-01-01 12:00")


def write_tz_aware_csv(data_dir, symbol, start, periods, tz_suffix="+00:00"):
    """合成带时区 ISO 字符串 open_time 的遗留 CSV（高危 #3 复现）。"""
    os.makedirs(data_dir, exist_ok=True)

    idx = pd.date_range(start, periods=periods, freq="1min")
    prices = 100.0 + np.arange(periods, dtype=float)

    df = pd.DataFrame({
        "open_time": [f"{t.strftime('%Y-%m-%d %H:%M:%S')}{tz_suffix}" for t in idx],
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": 1.0,
    })

    path = os.path.join(data_dir, kline_file_name(symbol))
    df.to_csv(path, index=False)
    return path


def test_load_tz_aware_legacy_csv_does_not_crash(tmp_path):
    """高危 #3 端到端回归：遗留 tz-aware CSV 经 KlineBuilder 剥 tz 后，
    load_symbol_kline 全链路（清洗→重采样→缓存）不崩溃，索引为 tz-naive。"""
    data_dir = str(tmp_path)
    write_tz_aware_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 480)

    df = load_symbol_kline("BTC", "4h", data_dir=data_dir, auto_fetch=False)

    assert df.index.tz is None
    assert len(df) == 2
    assert df["open"].iloc[0] == pytest.approx(100.0)
    assert df["close"].iloc[0] == pytest.approx(339.0)


def test_filter_and_align_after_tz_strip(tmp_path):
    """剥 tz 后 filter_df_by_date（tz-naive start/end 切片）与 align_klines
    （索引并集仍是 DatetimeIndex）对遗留 tz-aware 数据正常工作。"""
    data_dir = str(tmp_path)
    write_tz_aware_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 2 * 1440)
    write_tz_aware_csv(data_dir, "ETHUSDT", "2024-01-02 00:00", 1440)

    panel = load_aligned_panel(
        ["BTC", "ETH"], "1h", data_dir=data_dir, auto_fetch=False,
        start_date="2024-01-02", end_date="2024-01-02",
    )

    for df in panel.values():
        assert df.index.tz is None
        assert df.index.min() == pd.Timestamp("2024-01-02 00:00")
        assert df.index.max() == pd.Timestamp("2024-01-02 23:00")

    assert isinstance(panel["BTCUSDT"].index, pd.DatetimeIndex)
    assert panel["BTCUSDT"].index.equals(panel["ETHUSDT"].index)


def test_align_klines_rejects_mixed_tz_index():
    """兜底断言：万一混入 tz-aware 与 tz-naive 索引使 union 退化成 object
    索引，align_klines 必须立刻报清楚而不是让错误潜伏到下游。"""
    naive_idx = pd.date_range("2024-01-01 00:00", periods=5, freq="1h")
    aware_idx = pd.date_range("2024-01-01 00:00", periods=5, freq="1h", tz="UTC")

    naive = pd.DataFrame({"close": 100.0}, index=naive_idx)
    aware = pd.DataFrame({"close": 10.0}, index=aware_idx)

    with pytest.raises(ValueError, match="DatetimeIndex"):
        align_klines({"BTCUSDT": naive, "ETHUSDT": aware})


def test_load_aligned_panel_date_window(tmp_path):
    """日期过滤下沉到对齐之前，窗口语义包含 end 当天整天，对齐不受影响。"""
    data_dir = str(tmp_path)
    write_synthetic_csv(data_dir, "BTCUSDT", "2024-01-01 00:00", 3 * 1440)
    write_synthetic_csv(data_dir, "ETHUSDT", "2024-01-02 00:00", 2 * 1440)

    panel = load_aligned_panel(
        ["BTC", "ETH"], "1h", data_dir=data_dir, auto_fetch=False,
        start_date="2024-01-02", end_date="2024-01-02",
    )

    for df in panel.values():
        assert df.index.min() == pd.Timestamp("2024-01-02 00:00")
        assert df.index.max() == pd.Timestamp("2024-01-02 23:00")

    assert panel["BTCUSDT"].index.equals(panel["ETHUSDT"].index)
    assert panel["ETHUSDT"]["close"].notna().all()
