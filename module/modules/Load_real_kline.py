"""
标的归一化与本地 K 线数据文件辅助函数。

历史上的多周期加载函数 load_real_kline 已被
data_panel.load_symbol_kline（按需重采样 + 缓存）取代。
"""

import os
import time
import uuid


# 本地 K 线文件命名的唯一出处：下载器写文件、路径查找读文件、目录扫描
# 匹配文件三方共用同一套。任何一处单独改动都会让「下载成功但读取
# FileNotFoundError、且 has_kline_data 永远 False 导致每次回测重新全量
# 下载」——所以不要在别处再手写这个后缀。
KLINE_FILE_SUFFIX = "_1MIN_data.csv"

# 原子写的暂存子目录（建在数据目录内，保证与目标文件同一文件系统，
# os.replace 才是原子 rename）。平时为空：写完即 replace 移走临时文件。
STAGING_DIRNAME = ".staging"

# 原子写重试预算（指数退避，仅针对 os.replace 的 PermissionError）。
# 生产单文件 600-818MB，回测端 read_csv 整读会持有共享读句柄 3-5s，
# Windows os.replace 在此期间持续抛 PermissionError。原先固定 0.4s×8≈3.2s
# 兜底远小于一次整读耗时（实测 57/57 次失败），导致刚下好的新数据被当成
# 「拉取失败」丢弃、下次回测仍用旧数据。这里把退避做成指数级、总预算放大
# 到数十秒级，覆盖正常并发读窗口后才真正放弃。
_ATOMIC_RETRY_INITIAL_DELAY = 0.1     # 首次退避秒数
_ATOMIC_RETRY_MAX_DELAY = 3.0         # 单次退避上限（秒）
_ATOMIC_RETRY_TOTAL_BUDGET = 45.0     # 总退避预算（秒）

# 过期孤儿 .tmp 的清扫阈值：早于此秒数的 .tmp 视为前次进程崩溃/中断遗留，
# 首次 atomic_write_csv 时清扫一次。阈值要明显大于一次正常写盘（数秒）+
# 并发读窗口，避免误删别的进程正在写的临时文件。
_STAGING_TMP_STALE_SECONDS = 600.0

# 模块级一次性标志：进程内首次 atomic_write_csv 调用时对每个 .staging
# 目录清扫一次过期孤儿 .tmp（自包含，不依赖 webUI / 任何外部调度）。
_SWEPT_STAGING_DIRS: set = set()


class AtomicWriteConflict(OSError):
    """原子写在重试预算耗尽后仍无法 os.replace（目标长期被读句柄占用）。

    独立异常类型让调用方能把「写冲突（可重试，数据其实已下好）」与「真正
    的拉取失败（标的不存在/网络错误）」区分开——前者不应被永久标记失败并
    被去重缓存抑制。仍继承 OSError，旧的 `except OSError` / `except Exception`
    捕获路径行为不变（向后兼容）。
    """


def _sweep_stale_staging_once(staging: str) -> None:
    """进程内对某个 .staging 目录清扫一次过期孤儿 .tmp（自包含、幂等）。

    写 .tmp 期间或写完未 replace 前发生 crash / Ctrl-C / 异常逃逸会永久遗留
    .tmp（实测生产残留 635MB）。仅删早于 _STAGING_TMP_STALE_SECONDS 的，
    避免误删并发写者正在生成的临时文件。任何 OS 错误都吞掉——清扫是尽力而
    为，绝不能让它打断真正的写盘。
    """
    if staging in _SWEPT_STAGING_DIRS:
        return
    _SWEPT_STAGING_DIRS.add(staging)
    try:
        now = time.time()
        for name in os.listdir(staging):
            if not name.endswith(".tmp"):
                continue
            path = os.path.join(staging, name)
            try:
                if now - os.path.getmtime(path) > _STAGING_TMP_STALE_SECONDS:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def atomic_write_csv(df, filename, _retries=None, _delay=None, **to_csv_kwargs):
    """原子写 CSV：先在同目录 .staging 子目录写完整临时文件，再 os.replace
    覆盖目标文件。

    回测端 read_csv 读 filename 时，os.replace 是原子 rename——要么读到旧的
    完整文件、要么读到新的完整文件，绝不会读到半截或写入中的文件。这让
    「后台更新写文件」与「回测读文件」可以安全并行，无需暂停任何一方。

    Windows 上 os.replace 在目标文件正被其他句柄打开读取时可能抛
    PermissionError（read_csv 的窗口，大文件可达数秒）：用指数退避重试、
    总预算 ~数十秒级兜底，覆盖正常并发读窗口后才放弃（抛 AtomicWriteConflict）。

    `_retries` / `_delay` 为历史签名保留：若显式传入则退回「固定 _delay×_retries
    次」的旧行为（测试 / 极端环境可用），否则走指数退避预算。
    """
    target_dir = os.path.dirname(filename) or "."
    staging = os.path.join(target_dir, STAGING_DIRNAME)
    os.makedirs(staging, exist_ok=True)

    # 首次触达该 .staging：清扫一次前次进程遗留的过期孤儿 .tmp
    _sweep_stale_staging_once(staging)

    tmp = os.path.join(staging, f"{os.path.basename(filename)}.{uuid.uuid4().hex}.tmp")

    last_err = None
    # try/finally 包住整个写 + replace：无论 to_csv 抛异常、replace 始终失败、
    # 还是被 KeyboardInterrupt 等打断逃逸，finally 都保证清掉本次的 tmp，
    # 不留孤儿（成功 replace 后 tmp 已不在原位，os.remove 走 except 静默跳过）。
    try:
        df.to_csv(tmp, **to_csv_kwargs)

        if _retries is not None or _delay is not None:
            # 兼容旧固定重试语义（显式传参时）
            retries = int(_retries) if _retries is not None else 8
            delay = float(_delay) if _delay is not None else 0.4
            for _ in range(max(1, retries)):
                try:
                    os.replace(tmp, filename)
                    return
                except PermissionError as e:
                    last_err = e
                    time.sleep(delay)
        else:
            # 指数退避 + 总预算：覆盖大文件整读期间的持续 PermissionError
            deadline = time.time() + _ATOMIC_RETRY_TOTAL_BUDGET
            delay = _ATOMIC_RETRY_INITIAL_DELAY
            while True:
                try:
                    os.replace(tmp, filename)
                    return
                except PermissionError as e:  # 目标被回测 read 句柄占用
                    last_err = e
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    time.sleep(min(delay, _ATOMIC_RETRY_MAX_DELAY, remaining))
                    delay = min(delay * 2, _ATOMIC_RETRY_MAX_DELAY)

        # 预算耗尽仍冲突：抛独立的 AtomicWriteConflict 让调用方可区分重试。
        raise AtomicWriteConflict(
            f"os.replace 重试预算耗尽，目标长期被占用: {filename}"
        ) from last_err
    finally:
        # 唯一的 tmp 清理点：成功（已 replace 移走）走 except 静默；失败 /
        # 异常逃逸时删掉残留 tmp，保证 .staging 平时为空、不积累孤儿。
        try:
            os.remove(tmp)
        except OSError:
            pass


def kline_file_name(symbol: str) -> str:
    return f"{normalize_symbol(symbol)}{KLINE_FILE_SUFFIX}"


def get_kline_file_path(
    symbol: str,
    data_dir: str = "cryptocurrency_data/kline_data"
) -> str:
    return os.path.join(data_dir, kline_file_name(symbol))


def normalize_symbol(user_input: str, quote: str = "USDT") -> str:
    """
    把用户输入统一转换成交易对格式。
    btc      -> BTCUSDT
    BTC      -> BTCUSDT
    btcusdt  -> BTCUSDT
    BTCUSDT  -> BTCUSDT
    """

    symbol = user_input.strip().upper()

    if not symbol:
        raise ValueError("币种不能为空，请输入 BTC、ETH 这类币种。")

    if symbol.endswith(quote):
        return symbol

    return symbol + quote


def get_base_asset(symbol: str, quote: str = "USDT") -> str:
    """
    BTCUSDT -> BTC
    ETHUSDT -> ETH
    """

    symbol = normalize_symbol(symbol, quote)

    if symbol.endswith(quote):
        return symbol[:-len(quote)]

    return symbol


def Obtain_K(symbol: str, save_dir: str = "cryptocurrency_data/kline_data"):
    """
    拉取K线数据。

    注意：
    1. data_acquisition(stock=...) 接收 BTC / ETH 这种基础币种。
    2. save_dir 必须与调用方读取数据的目录一致：
       data_acquisition 自己的默认值是 "kline_data"（相对 CWD），
       不透传会导致数据下载到错误位置，读取时 FileNotFoundError。
    """

    from cryptocurrency_data.obtain_K_data import data_acquisition

    base_asset = get_base_asset(symbol)

    data_acquisition(
        stock=base_asset,
        save_dir=save_dir,
    )


def has_kline_data(
    symbol: str,
    data_dir: str = "cryptocurrency_data/kline_data"
) -> bool:
    """
    检查本地是否存在某个币种的K线数据文件。
    """

    return os.path.exists(get_kline_file_path(symbol, data_dir=data_dir))
