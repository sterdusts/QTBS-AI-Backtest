"""
K 线数据拉取队列：统一所有拉取路径的串行化、进度状态与显示。

三条拉取路径**共用同一条队列、同一份去重、同一进度计数**：
1. 启动自动更新（webUI demo.load）：扫描本地交易对依次更新（首次为空则拉默认币种）
2. 手动更新按钮（webUI）：更新本地全部交易对
3. 回测按需拉取（data_panel）：fetch_blocking 把所需币种认领/插队到队首后
   阻塞等待 worker 拉完，自己绝不另起一次下载

并发模型（持久 worker + Condition + generation）：
- 单一**持久** worker 线程：队列空时在 Condition 上 wait，被 enqueue/
  fetch_blocking/reset notify 唤醒，绝不自然退出。消除了「worker 退出与
  重启之间的 lost-wakeup / 双 worker / 批次边界 TOCTOU」一整类竞态。
- 所有共享状态读写都在 _COND（同一把 RLock）内；改状态后 notify_all。
- generation（_GEN）：reset / 开新批时自增。worker 拿任务时记下 gen，下载
  完仅当 gen 未变才计数——in-flight 下载被 reset 作废时不污染新批计数。
- 回测按需拉取（fetch_blocking）：把币种插到队首优先拉、阻塞等它拉完。
  **不再暂停后台批量**——下载器用原子写（atomic_write_csv）保证回测读文件
  永远完整，更新与回测可安全并行，无需暂停。

当前币种进度：Binance get_historical_klines 是阻塞黑盒、无中途回调，
所以「当前币种」只做活动指示（脉冲），总进度 = 已完成/总数（精确）。
"""

import threading
import time

from module.modules import data_integrity
from module.modules.Load_real_kline import (
    Obtain_K,
    has_kline_data,
    normalize_symbol,
)


# 首次使用（本地无任何数据）时默认初次拉取的币种
DEFAULT_INITIAL_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# 后台「先检查/修复缺口、再增量更新」总开关（默认开）。检查走白名单（只查未白名单月
# + 最新月），开销随检查轮次收敛；修复仅在确有缺口时拉对应区间。
_INTEGRITY_ENABLED = True


def set_integrity_enabled(enabled: bool) -> None:
    global _INTEGRITY_ENABLED
    _INTEGRITY_ENABLED = bool(enabled)

# 一批任务完成后，进度区保留"全部完成"显示的秒数
_FINISHED_HOLD_SECONDS = 6.0

# 回测按需拉取的最长等待（秒）：超时则放行回测，用现有数据
_BLOCKING_TIMEOUT = 900.0

_LOCK = threading.RLock()
_COND = threading.Condition(_LOCK)

_QUEUE: list = []          # 待拉币种（FIFO，回测按需插队首）
_QUEUED: set = set()       # 在队列中或正在拉取的币种（去重用）
_MODE: dict = {}           # symbol -> "initial" | "update"
_DIR_OF: dict = {}         # symbol -> 拉取目录（总取最新请求目录）
_ALL: list = []            # 本批登记过的全部币种（顺序，供悬停清单）
_ERRORS: list = []         # 失败币种
_CURRENT = None            # 正在拉取的币种
_TOTAL = 0                 # 本批总数
_DONE = 0                  # 已完成数（成功+失败）
_FINISHED_AT = None        # 本批完成时间戳
_GEN = 0                   # 批次代号：reset/开新批自增，作废 in-flight 计数
_WORKER = None             # 持久后台 worker 线程


def _reset_locked():
    """在锁内重置一批的计数与队列；自增 _GEN 作废 in-flight 下载的计数。"""
    global _TOTAL, _DONE, _FINISHED_AT, _GEN
    _QUEUE.clear()
    _QUEUED.clear()
    _MODE.clear()
    _DIR_OF.clear()
    _ALL.clear()
    _ERRORS.clear()
    _TOTAL = 0
    _DONE = 0
    _FINISHED_AT = None
    _GEN += 1


def _maybe_start_new_batch_locked():
    """上一批已完成且处于空闲态时，开新批前清零计数。"""
    if not _QUEUE and _CURRENT is None and _FINISHED_AT is not None:
        _reset_locked()


def _classify_mode(symbol: str, data_dir: str) -> str:
    return "update" if has_kline_data(symbol, data_dir=data_dir) else "initial"


def _register_locked(symbol: str, data_dir: str, front: bool = False) -> None:
    """在锁内把币种登记进队列（去重）。front=True 插队首（回测优先拉）。
    _DIR_OF 总取最新请求目录（同名不同目录时以最后请求方为准）。"""
    global _TOTAL, _FINISHED_AT

    # 总用最新请求目录：回测 fetch_blocking 要在它自己的目录拿数据，
    # 不能沿用批量首次登记的目录
    _DIR_OF[symbol] = data_dir

    if symbol == _CURRENT:
        return

    if symbol in _QUEUED:
        if front and symbol in _QUEUE:
            _QUEUE.remove(symbol)
            _QUEUE.insert(0, symbol)
        return

    _QUEUED.add(symbol)
    _MODE[symbol] = _classify_mode(symbol, data_dir)
    _ALL.append(symbol)
    _TOTAL += 1
    _FINISHED_AT = None
    if front:
        _QUEUE.insert(0, symbol)
    else:
        _QUEUE.append(symbol)


def _ensure_worker_locked():
    """启动持久 worker（仅首次 / 崩溃恢复）。"""
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    _WORKER = threading.Thread(target=_run, name="qtbs-fetch-worker", daemon=True)
    _WORKER.start()


def enqueue(symbols, data_dir) -> list:
    """批量登记币种并确保 worker 运行。返回真正新入队的币种。"""
    with _COND:
        _maybe_start_new_batch_locked()
        added = []
        for raw in symbols:
            symbol = normalize_symbol(raw)
            if symbol not in _QUEUED and symbol != _CURRENT:
                added.append(symbol)
            _register_locked(symbol, data_dir)
        _ensure_worker_locked()
        _COND.notify_all()
        return added


def fetch_blocking(symbol, data_dir, timeout: float = _BLOCKING_TIMEOUT) -> None:
    """回测按需拉取（仅用于本地完全无该币种数据时）：把币种插到队首优先拉，
    阻塞等 worker 拉完。

    自己绝不另起下载——与批量更新共用队列/去重/计数，不会重复拉、计数也
    只由 worker 加一次。不暂停后台批量（原子写保证读写并行安全）。
    超时后放行回测（用现有数据，见模块文档与契约）。
    """
    symbol = normalize_symbol(symbol)

    with _COND:
        _maybe_start_new_batch_locked()
        _register_locked(symbol, data_dir, front=True)
        _ensure_worker_locked()
        _COND.notify_all()

    deadline = time.time() + timeout
    with _COND:
        while (symbol in _QUEUE) or (_CURRENT == symbol):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _COND.wait(remaining)


def _run():
    """持久 worker：串行消费队列（FIFO，回测插队首优先），绝不自然退出。"""
    global _CURRENT, _DONE, _FINISHED_AT

    while True:
        with _COND:
            while not _QUEUE:
                _COND.wait()
            cand = _QUEUE.pop(0)
            _CURRENT = cand
            gen = _GEN
            data_dir = _DIR_OF.get(cand)

        ok = False
        try:
            # 先检查/修复缺口（白名单控开销），再增量更新。完整性这步最佳努力——
            # 失败只吞掉、绝不阻断正常更新（数据可用性优先）。
            if _INTEGRITY_ENABLED and data_dir:
                try:
                    data_integrity.check_and_repair(cand, data_dir)
                except Exception:
                    pass
            Obtain_K(cand, save_dir=data_dir)
            ok = bool(has_kline_data(cand, data_dir=data_dir))
        except Exception:
            # 注：原子写在重试预算耗尽时会抛 Load_real_kline.AtomicWriteConflict
            # （OSError 子类，代表「数据其实已下好、只是 os.replace 被读句柄
            # 长期占用」），语义上应可重试而非永久失败。但「冲突标的免疫去重
            # 抑制并重排队」需改动 data_panel._REFRESHED_SYMBOLS（非本文件归属），
            # 故此处暂统一记为失败；指数退避预算（~45s）已让正常并发读期间的
            # 写入几乎总能成功，把冲突落到这里的概率压到极低。
            ok = False

        with _COND:
            # 仅当本批未被 reset（gen 未变）才计数，避免作废批污染
            if gen == _GEN:
                _QUEUED.discard(cand)
                _DONE += 1
                if not ok and cand not in _ERRORS:
                    _ERRORS.append(cand)
                if _CURRENT == cand:
                    _CURRENT = None
                # 在同一锁块内同时清 _CURRENT 与设 _FINISHED_AT，
                # 不留「_CURRENT 已清但 _FINISHED_AT 未设」的对外窗口
                if not _QUEUE and _CURRENT is None:
                    _FINISHED_AT = time.time()
            _COND.notify_all()


# ---------------------------------------------------------
# 状态查询
# ---------------------------------------------------------

def is_running() -> bool:
    with _LOCK:
        return bool(_QUEUE) or (_CURRENT is not None)


def snapshot() -> dict:
    """线程安全的状态快照，供 webUI Timer 轮询渲染。"""
    with _LOCK:
        running = bool(_QUEUE) or (_CURRENT is not None)
        recently_done = (
            not running
            and _FINISHED_AT is not None
            and (time.time() - _FINISHED_AT) < _FINISHED_HOLD_SECONDS
        )
        return {
            "running": running,
            "recently_done": recently_done,
            "total": _TOTAL,
            "done": _DONE,
            "current": _CURRENT,
            "current_mode": _MODE.get(_CURRENT) if _CURRENT else None,
            "symbols": [(s, _MODE.get(s, "update")) for s in _ALL],
            "errors": list(_ERRORS),
        }


def reset() -> None:
    """清空全部状态（主要用于测试隔离）。

    不停止/join 持久 worker（它在 Condition 上 wait）：清状态 + 自增 _GEN
    作废任何 in-flight 下载的计数，再 notify 让 worker 重新评估（队列已空则
    继续 wait）。下一次 enqueue 复用同一 worker，单 worker 不变式不破。
    """
    global _CURRENT
    with _COND:
        _reset_locked()
        _CURRENT = None
        _COND.notify_all()
