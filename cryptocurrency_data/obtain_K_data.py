import os
import sys
import time
import pandas as pd
from binance.client import Client

# 允许独立运行（python cryptocurrency_data/obtain_K_data.py）时找到项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 符号归一化与文件命名单源在 Load_real_kline：下载侧与读取侧
# 任何一边单独改动都会让对方找不到文件，不要在本文件重新定义
from module.modules.Load_real_kline import (
    atomic_write_csv,
    kline_file_name,
    normalize_symbol,
)

COLS = [
    'open_time', 'open', 'high', 'low', 'close', 'volume',
    'close_time', 'qav', 'nt', 'tbv', 'tqv', 'ignore'
]

ONE_MINUTE_MS = 60_000


def load_existing_df(filename: str) -> pd.DataFrame:
    """
    读取已有CSV，并统一字段格式：
    - open_time / close_time -> 毫秒整数
    - 保证包含 COLS 中的字段
    """
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return pd.DataFrame(columns=COLS)

    df = pd.read_csv(filename)

    if df.empty:
        return pd.DataFrame(columns=COLS)

    # 补齐缺列
    for col in COLS:
        if col not in df.columns:
            df[col] = pd.NA

    df = df[[c for c in COLS if c in df.columns]]

    # 统一 open_time。NaT 在 astype(int64) 下会变成 1677 年的哨兵值，
    # 幽灵行一旦随增量更新落盘，重采样会物化数百年分箱直接 OOM——
    # 损坏行必须先丢弃
    # 毫秒换算必须与 datetime64 分辨率无关：pandas 3 默认 us 分辨率，
    # astype(int64)//10**6 会把毫秒错算成秒（错 1000 倍）
    epoch = pd.Timestamp(0, tz="UTC")
    one_ms = pd.Timedelta(milliseconds=1)

    if not pd.api.types.is_numeric_dtype(df["open_time"]):
        dt = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        df = df[dt.notna()]
        df["open_time"] = (dt[dt.notna()] - epoch) // one_ms
    else:
        df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
        df = df[df["open_time"].notna()]

    # 统一 close_time（同样丢弃损坏行）
    if not pd.api.types.is_numeric_dtype(df["close_time"]):
        dt = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
        df = df[dt.notna()]
        df["close_time"] = (dt[dt.notna()] - epoch) // one_ms
    else:
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")

    # 其他数值列
    numeric_cols = ["open", "high", "low", "close", "volume", "qav", "tbv", "tqv", "ignore"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "nt" in df.columns:
        df["nt"] = pd.to_numeric(df["nt"], errors="coerce")

    return df


def get_resume_start_ms(filename: str, default_start):
    """
    返回本次续拉起点：
    - 文件不存在 -> default_start
    - 文件存在 -> 最后一根 open_time + 1分钟
    """
    old_df = load_existing_df(filename)

    if old_df.empty:
        return default_start

    last_open_time = old_df["open_time"].dropna().iloc[-1]
    return int(last_open_time) + ONE_MINUTE_MS


def klines_to_df(klines) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=COLS)

    if df.empty:
        return df

    # 时间戳保留为毫秒整数
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce").astype("Int64")
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("Int64")
    df["nt"] = pd.to_numeric(df["nt"], errors="coerce").astype("Int64")

    numeric_cols = ["open", "high", "low", "close", "volume", "qav", "tbv", "tqv", "ignore"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def add_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    在保留毫秒整数时间戳的前提下，额外增加可读时间列
    """
    if df.empty:
        df["open_datetime"] = pd.Series(dtype="datetime64[ns, UTC]")
        df["close_datetime"] = pd.Series(dtype="datetime64[ns, UTC]")
        return df

    df = df.copy()
    df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_datetime"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def drop_last_row(df: pd.DataFrame) -> pd.DataFrame:
    """
    故意丢掉最后一根，避免把当前尚未完全收盘的K线存入长期数据库
    """
    if df.empty or len(df) <= 1:
        return df.iloc[0:0].copy() if len(df) == 1 else df
    return df.iloc[:-1].copy()


def data_acquisition(
    stock,
    start_date='2017-01-01 UTC',
    end_date='now UTC',
    market_type='spot',      # "spot" 现货/ "usdt_futures"合约 / "coin_futures"币本位合约
    update_mode=True,
    drop_last_unclosed=True,
    save_dir='kline_data'
):
    API_KEY = ''
    API_SECRET = ''

    client = Client(API_KEY, API_SECRET)
    symbol = normalize_symbol(stock)
    interval = Client.KLINE_INTERVAL_1MINUTE

    os.makedirs(save_dir, exist_ok=True)
    filename = os.path.join(save_dir, kline_file_name(symbol))

    # 决定起点：全量 or 增量
    if update_mode:
        real_start = get_resume_start_ms(filename, start_date)
    else:
        real_start = start_date

    print(f"\n⏳ 正在拉取 {symbol}")
    print(f"起点: {real_start}")
    print(f"终点: {end_date}")

    try:
        if market_type == 'spot':
            klines = client.get_historical_klines(
                symbol,
                interval=interval,
                start_str=real_start,
                end_str=end_date
            )
        elif market_type == 'usdt_futures':
            klines = client.futures_historical_klines(
                symbol=symbol,
                interval=interval,
                start_str=real_start,
                end_str=end_date
            )
        elif market_type == 'coin_futures':
            klines = client.futures_coin_historical_klines(
                symbol=symbol,
                interval=interval,
                start_str=real_start,
                end_str=end_date
            )
        else:
            raise ValueError("market_type 只能是 'spot' / 'usdt_futures' / 'coin_futures'")

    except Exception as e:
        print(f"❌ {symbol} 拉取失败: {e}")
        return pd.DataFrame()

    old_df = load_existing_df(filename) if update_mode else pd.DataFrame(columns=COLS)
    new_df = klines_to_df(klines)

    if new_df.empty:
        print(f"✅ {symbol} 没有新数据")
        final_df = old_df.copy()
        final_df = final_df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
        final_df = add_datetime_columns(final_df)
        return final_df

    # 合并
    df = pd.concat([old_df, new_df], ignore_index=True)

    # 按 open_time 去重 + 排序
    df = df.drop_duplicates(subset=["open_time"], keep="last")
    df = df.sort_values("open_time").reset_index(drop=True)

    # 每次保存前故意丢掉最后一根
    if drop_last_unclosed:
        df = drop_last_row(df)

    # 增加可读时间列，但不替代原始毫秒列
    df = add_datetime_columns(df)

    # 原子写：在 .staging 暂存区写完整文件再 os.replace 覆盖原文件，
    # 让回测读该文件可与本次更新安全并行（永远读到完整文件）
    atomic_write_csv(df, filename, index=False, encoding='utf-8-sig')
    print(f"✅ 已保存到: {filename}")
    print(f"✅ 当前总行数: {len(df)} | 本次抓取原始新增: {len(new_df)}")

    return df


def _historical_klines(client, symbol, market_type, start, end):
    """按市场类型取历史 K 线。start/end 接受毫秒整数或可读时间串。"""
    interval = Client.KLINE_INTERVAL_1MINUTE
    if market_type == "spot":
        return client.get_historical_klines(symbol, interval=interval, start_str=start, end_str=end)
    if market_type == "usdt_futures":
        return client.futures_historical_klines(symbol=symbol, interval=interval, start_str=start, end_str=end)
    if market_type == "coin_futures":
        return client.futures_coin_historical_klines(symbol=symbol, interval=interval, start_str=start, end_str=end)
    raise ValueError("market_type 只能是 'spot' / 'usdt_futures' / 'coin_futures'")


def fetch_and_merge_range(
    symbol, start_ms, end_ms, save_dir="kline_data", market_type="spot",
):
    """缺口修复：拉取 [start_ms, end_ms]（闭区间，毫秒）并【合并】进已有文件——保留
    全部旧数据，只补这一段（不像 update_mode=False 那样覆盖、也不像 update_mode=True
    那样只从末尾续拉）。去重(open_time)+排序后原子落盘。返回本次净新增行数。

    回补历史中段，**不丢末根**（end_ms 是历史时点、非"now"）。

    返回值区分三态（调用方据此决定是否封"已知空洞"）：
    - 整数 >0：净新增行数（成功补到数据）
    - 0：拉取**成功但币安确无**该段数据 ⇒ 调用方可据此记为已知空洞、不再重试
    - **None：拉取异常**（瞬时网络错误/超时等）⇒ 调用方【不得】封洞，须留待下次重试
      （否则一次瞬时失败会把可补的缺口永久埋死——审查发现的高危）。
    """
    client = Client("", "")
    sym = normalize_symbol(symbol)
    os.makedirs(save_dir, exist_ok=True)
    filename = os.path.join(save_dir, kline_file_name(sym))

    old_df = load_existing_df(filename)
    try:
        klines = _historical_klines(client, sym, market_type, int(start_ms), int(end_ms))
    except Exception as e:
        print(f"❌ {sym} 缺口回补拉取失败 [{start_ms},{end_ms}]: {e}")
        return None   # 异常 ≠ 确无：返回 None，调用方不封洞、下次重试

    new_df = klines_to_df(klines)
    if new_df.empty:
        return 0      # 成功但币安确无该段 ⇒ 可封为已知空洞

    before = len(old_df)
    df = pd.concat([old_df, new_df], ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
    df = add_datetime_columns(df)
    atomic_write_csv(df, filename, index=False, encoding="utf-8-sig")
    return max(0, len(df) - before)


if False:
    # ===== 批量更新示例 =====
    stock_list = ['ETH', 'BTC', 'SOL', 'DOGE', 'XRP',
                  'ADA', 'XLM', 'BNB', 'TRX']

    for s in stock_list:
        start = time.perf_counter()
        data_acquisition(
            stock=s,
            start_date='2017-01-01 UTC',
            end_date='now UTC',
            market_type='spot',
            update_mode=True,
            drop_last_unclosed=True
        )
        end = time.perf_counter()
        print(f"{s} 花费时间 {end - start:.2f}s")