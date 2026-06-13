"""
下载器数据清洗测试：损坏时间戳不得变成 1677 年哨兵值幽灵行。

现货与合约两个下载器各有一份近似 load_existing_df，同一修复在两处都做了——
参数化覆盖两者，避免只改/只测一侧时另一侧带着旧 bug 继续落盘。
"""

import os
import threading
import time

import pandas as pd
import pytest

from cryptocurrency_data import obtain_K_data, usdt_futures_K_data
from module.modules.Load_real_kline import STAGING_DIRNAME, atomic_write_csv


@pytest.fixture(params=[obtain_K_data, usdt_futures_K_data],
                ids=["spot", "futures"])
def downloader(request):
    return request.param


def test_load_existing_df_drops_corrupted_timestamps(tmp_path, downloader):
    # 表头从模块 COLS 派生：增删列时表头自动跟随，不会与生产写盘格式分叉
    header = ",".join(downloader.COLS) + "\n"
    good = "2024-01-01 00:00:00,1,1,1,1,1,2024-01-01 00:00:59,0,0,0,0,0\n"
    bad = "GARBAGE,1,1,1,1,1,2024-01-01 00:01:59,0,0,0,0,0\n"

    path = tmp_path / "BTCUSDT_1MIN_data.csv"
    path.write_text(header + good + bad, encoding="utf-8")

    df = downloader.load_existing_df(str(path))

    # 损坏行被丢弃而不是变成 1677 年哨兵值
    assert len(df) == 1
    expected_ms = int(pd.Timestamp("2024-01-01", tz="UTC").value // 10**6)
    assert int(df["open_time"].iloc[0]) == expected_ms
    assert (df["open_time"] > 0).all()


# =========================================================
# 原子写：回测读文件与后台更新写文件并行安全
# =========================================================

def test_atomic_write_produces_complete_file_and_empty_staging(tmp_path):
    target = tmp_path / "BTCUSDT_1MIN_data.csv"
    df = pd.DataFrame({"open_time": range(1000), "close": range(1000)})

    atomic_write_csv(df, str(target), index=False)

    assert target.exists()
    back = pd.read_csv(target)
    assert len(back) == 1000
    # staging 子目录写完即空（临时文件被 os.replace 移走）
    staging = tmp_path / STAGING_DIRNAME
    assert not staging.exists() or not list(staging.iterdir())


def test_atomic_write_reader_never_sees_partial(tmp_path):
    """并发：一个线程反复 atomic_write 覆盖，多个线程反复 read_csv，
    读侧永远拿到完整可解析文件（行数恒为 N），绝不读到半截。"""
    target = str(tmp_path / "BTCUSDT_1MIN_data.csv")
    N = 5000
    df = pd.DataFrame({"open_time": range(N), "close": range(N)})
    atomic_write_csv(df, target, index=False)

    stop = threading.Event()
    errors = []

    def writer():
        while not stop.is_set():
            try:
                atomic_write_csv(df, target, index=False)
            except Exception as e:  # 占用重试耗尽等
                errors.append(("write", repr(e)))
            time.sleep(0.001)

    def reader():
        for _ in range(40):
            try:
                back = pd.read_csv(target)
                if len(back) != N:  # 读到半截 → 行数不对
                    errors.append(("partial", len(back)))
            except Exception as e:
                errors.append(("read", repr(e)))
            time.sleep(0.002)

    w = threading.Thread(target=writer, daemon=True)
    w.start()
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for t in readers:
        t.join()
    stop.set()
    w.join(timeout=2)

    assert errors == [], errors[:5]
