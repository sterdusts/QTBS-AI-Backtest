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
from module.modules import Load_real_kline
from module.modules.Load_real_kline import (
    AtomicWriteConflict,
    STAGING_DIRNAME,
    atomic_write_csv,
)


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


# =========================================================
# 原子写鲁棒性：重试预算（退避）+ 孤儿 tmp 清理
# =========================================================

def _empty_staging(tmp_path):
    staging = tmp_path / STAGING_DIRNAME
    return (not staging.exists()) or (not list(staging.iterdir()))


def test_atomic_write_retries_until_replace_succeeds(tmp_path, monkeypatch):
    """模拟 os.replace 前若干次抛 PermissionError 后成功（生产中回测整读
    大文件占用读句柄数秒的场景）：atomic_write 应在退避预算内最终落地完整
    文件，而不是 3.2s 兜底用尽就把刚下好的数据当成失败丢弃。"""
    target = tmp_path / "BTCUSDT_1MIN_data.csv"
    df = pd.DataFrame({"open_time": range(500), "close": range(500)})

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 5:  # 前 5 次模拟被读句柄占用
            raise PermissionError("WinError 32: 文件正被另一进程使用")
        return real_replace(src, dst)

    # 缩短退避常量，避免测试真的睡几十秒（语义不变：仍是指数退避+总预算）
    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_INITIAL_DELAY", 0.001)
    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_MAX_DELAY", 0.01)
    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_TOTAL_BUDGET", 5.0)
    monkeypatch.setattr(Load_real_kline.os, "replace", flaky_replace)

    atomic_write_csv(df, str(target), index=False)

    assert calls["n"] == 6                       # 5 次失败 + 1 次成功
    assert target.exists()
    assert len(pd.read_csv(target)) == 500       # 完整落地
    assert _empty_staging(tmp_path)              # tmp 已被 replace 移走，staging 空


def test_atomic_write_conflict_after_budget_exhausted(tmp_path, monkeypatch):
    """os.replace 始终冲突：退避预算耗尽后抛独立的 AtomicWriteConflict
    （OSError 子类，可被调用方区分为「可重试的写冲突」），且 tmp 被清掉、
    staging 不残留孤儿。"""
    target = tmp_path / "BTCUSDT_1MIN_data.csv"
    df = pd.DataFrame({"open_time": range(10), "close": range(10)})

    def always_blocked(src, dst):
        raise PermissionError("WinError 32: 文件正被另一进程使用")

    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_INITIAL_DELAY", 0.001)
    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_MAX_DELAY", 0.005)
    monkeypatch.setattr(Load_real_kline, "_ATOMIC_RETRY_TOTAL_BUDGET", 0.05)
    monkeypatch.setattr(Load_real_kline.os, "replace", always_blocked)

    with pytest.raises(AtomicWriteConflict):
        atomic_write_csv(df, str(target), index=False)

    assert isinstance(AtomicWriteConflict("x"), OSError)  # 向后兼容：仍是 OSError
    assert _empty_staging(tmp_path)               # finally 清掉了本次 tmp


def test_atomic_write_to_csv_failure_cleans_tmp(tmp_path, monkeypatch):
    """to_csv 写到一半抛异常（磁盘满 / 序列化错误等）：try/finally 必须把
    残留 tmp 删掉，绝不在 .staging 留下半截孤儿文件。"""
    target = tmp_path / "BTCUSDT_1MIN_data.csv"

    class Boom(Exception):
        pass

    class ExplodingDF:
        def to_csv(self, tmp, **kwargs):
            # 模拟写了半截才炸：先落一个文件再抛
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("partial")
            raise Boom("disk full")

    with pytest.raises(Boom):
        atomic_write_csv(ExplodingDF(), str(target), index=False)

    assert not target.exists()                    # 目标文件未被破坏/创建
    assert _empty_staging(tmp_path)               # 半截 tmp 已被 finally 清掉


def test_atomic_write_sweeps_stale_orphan_tmp(tmp_path, monkeypatch):
    """首次 atomic_write_csv 触达某 .staging 时，清扫前次进程崩溃遗留的
    过期孤儿 .tmp（按 mtime 阈值）；但不误删「新近」的 tmp（可能是别的
    并发写者正在生成的）。"""
    staging = tmp_path / STAGING_DIRNAME
    staging.mkdir()

    stale = staging / "BTCUSDT_1MIN_data.csv.deadbeef.tmp"
    stale.write_text("orphan from a crashed run", encoding="utf-8")
    fresh = staging / "ETHUSDT_1MIN_data.csv.cafef00d.tmp"
    fresh.write_text("a peer writer's in-progress tmp", encoding="utf-8")

    # 把 stale 的 mtime 推到很久以前（超过清扫阈值），fresh 保持现在
    old_mtime = time.time() - (Load_real_kline._STAGING_TMP_STALE_SECONDS + 100)
    os.utime(stale, (old_mtime, old_mtime))

    # 进程内可能已对别的 staging 清扫过，但本 tmp_path 下的 .staging 是新的；
    # 显式清掉一次性标志中本目录条目，确保本次会执行清扫（防测试间串扰）
    Load_real_kline._SWEPT_STAGING_DIRS.discard(str(staging))

    target = tmp_path / "DOGEUSDT_1MIN_data.csv"
    atomic_write_csv(pd.DataFrame({"open_time": [1], "close": [2]}), str(target), index=False)

    assert not stale.exists()                     # 过期孤儿被清扫
    assert fresh.exists()                          # 新近 tmp 不误删
    assert target.exists()                         # 本次写盘成功
