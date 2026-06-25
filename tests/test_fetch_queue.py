"""
fetch_queue 拉取队列状态机测试（mock Obtain_K，不真打 Binance）。
"""

import time

import pytest

from module.modules import fetch_queue
from module.modules.Load_real_kline import kline_file_name


@pytest.fixture(autouse=True)
def clean_queue():
    fetch_queue.reset()
    yield
    fetch_queue.reset()


def _touch(data_dir, symbol):
    """造一个已存在的本地数据文件（让 has_kline_data 为 True）。"""
    path = data_dir / kline_file_name(symbol)
    path.write_text("open_time\n", encoding="utf-8")


def _wait_idle(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not fetch_queue.is_running():
            return True
        time.sleep(0.02)
    return False


def test_integrity_check_runs_with_force_when_requested(tmp_path, monkeypatch):
    """手动「检查并修复」：enqueue(force_integrity=True) ⇒ worker 在更新前以
    force=True 调 check_and_repair（忽略白名单全量查）。"""
    calls = []
    monkeypatch.setattr(fetch_queue, "Obtain_K",
                        lambda symbol, save_dir: _touch(tmp_path, symbol))
    monkeypatch.setattr(fetch_queue.data_integrity, "check_and_repair",
                        lambda symbol, data_dir, force=False, **kw: calls.append((symbol, force)))

    fetch_queue.enqueue(["BTC"], str(tmp_path), force_integrity=True)
    assert _wait_idle()
    assert calls == [("BTCUSDT", True)]


def test_integrity_check_force_false_by_default(tmp_path, monkeypatch):
    """常规后台更新：check_and_repair 以 force=False（走白名单，省开销）先于更新执行。"""
    calls = []
    monkeypatch.setattr(fetch_queue, "Obtain_K",
                        lambda symbol, save_dir: _touch(tmp_path, symbol))
    monkeypatch.setattr(fetch_queue.data_integrity, "check_and_repair",
                        lambda symbol, data_dir, force=False, **kw: calls.append((symbol, force)))

    fetch_queue.enqueue(["ETH"], str(tmp_path))
    assert _wait_idle()
    assert calls == [("ETHUSDT", False)]


def test_enqueue_runs_worker_and_completes(tmp_path, monkeypatch):
    fetched = []

    def fake_obtain(symbol, save_dir):
        fetched.append(symbol)
        # 造出落盘文件，让 worker 判定成功
        (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(fetch_queue, "Obtain_K", fake_obtain)

    added = fetch_queue.enqueue(["BTC", "ETH"], str(tmp_path))
    assert added == ["BTCUSDT", "ETHUSDT"]

    assert _wait_idle()
    snap = fetch_queue.snapshot()
    assert snap["total"] == 2
    assert snap["done"] == 2
    assert snap["errors"] == []
    assert sorted(fetched) == ["BTCUSDT", "ETHUSDT"]


def test_mode_initial_vs_update(tmp_path, monkeypatch):
    # BTC 已有数据 → update；ETH 没有 → initial
    _touch(tmp_path, "BTCUSDT")
    monkeypatch.setattr(
        fetch_queue, "Obtain_K",
        lambda symbol, save_dir: (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8"),
    )

    fetch_queue.enqueue(["BTC", "ETH"], str(tmp_path))
    assert _wait_idle()

    modes = dict(fetch_queue.snapshot()["symbols"])
    assert modes["BTCUSDT"] == "update"
    assert modes["ETHUSDT"] == "initial"


def test_enqueue_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fetch_queue, "Obtain_K",
        lambda symbol, save_dir: (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8"),
    )
    # 同名只入队一次（含归一化 BTC == BTCUSDT）
    fetch_queue.enqueue(["BTC", "BTCUSDT"], str(tmp_path))
    assert _wait_idle()
    assert fetch_queue.snapshot()["total"] == 1


def test_failed_fetch_recorded(tmp_path, monkeypatch):
    # Obtain_K 什么都不做（不落盘）→ has_kline_data 仍 False → 计入 errors
    monkeypatch.setattr(fetch_queue, "Obtain_K", lambda symbol, save_dir: None)

    fetch_queue.enqueue(["DOGE"], str(tmp_path))
    assert _wait_idle()

    snap = fetch_queue.snapshot()
    assert snap["done"] == 1
    assert snap["errors"] == ["DOGEUSDT"]


def test_fetch_blocking_new_symbol(tmp_path, monkeypatch):
    # 回测按需拉取全新币种：入队首、worker 拉、阻塞等到完成
    monkeypatch.setattr(
        fetch_queue, "Obtain_K",
        lambda symbol, save_dir: (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8"),
    )

    fetch_queue.fetch_blocking("SOL", str(tmp_path))

    snap = fetch_queue.snapshot()
    assert snap["current"] is None        # 已拉完
    assert snap["total"] == 1
    assert snap["done"] == 1
    assert snap["errors"] == []
    assert (tmp_path / kline_file_name("SOLUSDT")).exists()


def test_fetch_blocking_does_not_duplicate_queued_symbol(tmp_path, monkeypatch):
    # 关键回归：批量已登记 BTC，回测又要 BTC —— 只能拉一次、计数不超总数
    pulled = []

    def slow_obtain(symbol, save_dir):
        time.sleep(0.1)
        pulled.append(symbol)
        (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(fetch_queue, "Obtain_K", slow_obtain)

    # 批量入队 BTC + ETH（worker 开始拉 BTC）
    fetch_queue.enqueue(["BTC", "ETH"], str(tmp_path))
    # 回测立刻也要 BTC：应认领同一次拉取，不另起
    fetch_queue.fetch_blocking("BTC", str(tmp_path))

    assert _wait_idle()
    snap = fetch_queue.snapshot()
    assert pulled.count("BTCUSDT") == 1   # BTC 只被拉一次
    assert snap["total"] == 2             # 计数不超总数（修复 11/10）
    assert snap["done"] == 2


def test_backtest_front_insert_priority(tmp_path, monkeypatch):
    """回测按需拉取插队首优先（不暂停批量）：worker 拉完当前币种后先拉回测
    要的币种，排在剩余批量之前。"""
    order = []

    def obtain(symbol, save_dir):
        time.sleep(0.05)
        order.append(symbol)
        (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(fetch_queue, "Obtain_K", obtain)

    # 批量入队 3 个币（worker 开始拉 AAA）
    fetch_queue.enqueue(["AAA", "BBB", "CCC"], str(tmp_path))
    time.sleep(0.02)
    # 回测要 ZZZ（全新）：插队首，worker 拉完当前 AAA 后先拉 ZZZ
    fetch_queue.fetch_blocking("ZZZ", str(tmp_path))
    assert "ZZZUSDT" in order  # 返回时 ZZZ 已拉完

    assert _wait_idle()
    # ZZZ 插在 BBB/CCC 之前被拉
    assert order.index("ZZZUSDT") < order.index("BBBUSDT")
    assert order.index("ZZZUSDT") < order.index("CCCUSDT")

    snap = fetch_queue.snapshot()
    assert snap["done"] == snap["total"] == 4
    assert "paused" not in snap  # 暂停机制已移除


def test_reset_during_download_no_double_worker_no_count_corruption(tmp_path, monkeypatch):
    """worker 正在下载时 reset：generation 作废 in-flight 计数，不污染新批，
    且不会启动第二个 worker（持久单 worker 不变式）。"""
    import threading

    release = threading.Event()

    def blocking_obtain(symbol, save_dir):
        release.wait(timeout=5)  # 卡住 worker，模拟长下载
        (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(fetch_queue, "Obtain_K", blocking_obtain)

    fetch_queue.enqueue(["AAA"], str(tmp_path))
    # 等 worker 进入下载（_CURRENT 被设）
    for _ in range(100):
        if fetch_queue.snapshot()["current"] == "AAAUSDT":
            break
        time.sleep(0.02)
    worker1 = fetch_queue._WORKER

    # 下载进行中 reset：清状态 + gen++（作废这次 in-flight 计数）
    fetch_queue.reset()
    assert fetch_queue.snapshot()["total"] == 0

    # 新批入队
    fetch_queue.enqueue(["BBB"], str(tmp_path))
    release.set()  # 放行卡住的下载
    assert _wait_idle()

    # 同一 worker（未启动第二个），计数只反映新批 BBB（AAA 的 in-flight 被作废）
    assert fetch_queue._WORKER is worker1
    snap = fetch_queue.snapshot()
    assert snap["done"] == snap["total"] == 1
    assert snap["done"] <= snap["total"]


def test_enqueue_after_idle_reuses_worker(tmp_path, monkeypatch):
    """批次完成进入空闲后，再次 enqueue 复用同一持久 worker，不滞留币种。"""
    monkeypatch.setattr(
        fetch_queue, "Obtain_K",
        lambda symbol, save_dir: (tmp_path / kline_file_name(symbol)).write_text("x\n", encoding="utf-8"),
    )

    fetch_queue.enqueue(["AAA"], str(tmp_path))
    assert _wait_idle()
    worker1 = fetch_queue._WORKER

    # 空闲后再入队：新批开始，同一 worker 消费，不会 lost-wakeup 滞留
    fetch_queue.enqueue(["BBB"], str(tmp_path))
    assert _wait_idle()
    assert fetch_queue._WORKER is worker1
    snap = fetch_queue.snapshot()
    assert snap["done"] == snap["total"] == 1  # 新批已重置计数
