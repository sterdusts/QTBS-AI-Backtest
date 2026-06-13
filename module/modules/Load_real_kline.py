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


def atomic_write_csv(df, filename, _retries: int = 8, _delay: float = 0.4, **to_csv_kwargs):
    """原子写 CSV：先在同目录 .staging 子目录写完整临时文件，再 os.replace
    覆盖目标文件。

    回测端 read_csv 读 filename 时，os.replace 是原子 rename——要么读到旧的
    完整文件、要么读到新的完整文件，绝不会读到半截或写入中的文件。这让
    「后台更新写文件」与「回测读文件」可以安全并行，无需暂停任何一方。

    Windows 上 os.replace 在目标文件正被其他句柄打开读取时可能抛
    PermissionError（read_csv 的短暂窗口）：重试若干次兜底。
    """
    target_dir = os.path.dirname(filename) or "."
    staging = os.path.join(target_dir, STAGING_DIRNAME)
    os.makedirs(staging, exist_ok=True)

    tmp = os.path.join(staging, f"{os.path.basename(filename)}.{uuid.uuid4().hex}.tmp")
    df.to_csv(tmp, **to_csv_kwargs)

    last_err = None
    for attempt in range(_retries):
        try:
            os.replace(tmp, filename)
            return
        except PermissionError as e:  # Windows：目标被回测的 read 句柄占用
            last_err = e
            time.sleep(_delay)

    # 始终失败：清理临时文件并抛出，由下载器/worker 记为失败、下次重试
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last_err


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
