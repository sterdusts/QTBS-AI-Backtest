import os
import sys
import time
import pandas as pd
from binance.client import Client

# 允许独立运行（python cryptocurrency_data/usdt_futures_K_data.py）时找到项目根
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
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return pd.DataFrame(columns=COLS)

    df = pd.read_csv(filename)

    if df.empty:
        return pd.DataFrame(columns=COLS)

    for col in COLS:
        if col not in df.columns:
            df[col] = pd.NA

    df = df[[c for c in COLS if c in df.columns]]

    # NaT 在整数转换下会变成 1677 年的哨兵值幽灵行，必须先丢弃；
    # 毫秒换算必须与 datetime64 分辨率无关（pandas 3 默认 us）
    if not pd.api.types.is_numeric_dtype(df["open_time"]):
        dt = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        df = df[dt.notna()]
        df["open_time"] = (
            (dt[dt.notna()] - pd.Timestamp(0, tz="UTC"))
            // pd.Timedelta(milliseconds=1)
        )
    else:
        df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
        df = df[df["open_time"].notna()]

    if not pd.api.types.is_numeric_dtype(df["close_time"]):
        dt = pd.to_datetime(df["close_time"], utc=True, errors="coerce")
        df = df[dt.notna()]
        df["close_time"] = (
            (dt[dt.notna()] - pd.Timestamp(0, tz="UTC"))
            // pd.Timedelta(milliseconds=1)
        )
    else:
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")

    numeric_cols = ["open", "high", "low", "close", "volume", "qav", "tbv", "tqv", "ignore"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "nt" in df.columns:
        df["nt"] = pd.to_numeric(df["nt"], errors="coerce")

    return df


def get_resume_start_ms(filename: str, default_start):
    old_df = load_existing_df(filename)

    if old_df.empty:
        return default_start

    last_open_time = old_df["open_time"].dropna().iloc[-1]
    return int(last_open_time) + ONE_MINUTE_MS


def klines_to_df(klines) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=COLS)

    if df.empty:
        return df

    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce").astype("Int64")
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("Int64")
    df["nt"] = pd.to_numeric(df["nt"], errors="coerce").astype("Int64")

    numeric_cols = ["open", "high", "low", "close", "volume", "qav", "tbv", "tqv", "ignore"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def add_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["open_datetime"] = pd.Series(dtype="datetime64[ns, UTC]")
        df["close_datetime"] = pd.Series(dtype="datetime64[ns, UTC]")
        return df

    df = df.copy()
    df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_datetime"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def drop_last_row(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) <= 1:
        return df.iloc[0:0].copy() if len(df) == 1 else df
    return df.iloc[:-1].copy()


def data_acquisition(
    stock,
    start_date='2017-01-01 UTC',
    end_date='now UTC',
    market_type='usdt_futures',
    update_mode=True,
    drop_last_unclosed=True,
    save_dir='futures_kline_data'
):
    API_KEY = ''
    API_SECRET = ''

    client = Client(API_KEY, API_SECRET)
    symbol = normalize_symbol(stock)
    interval = Client.KLINE_INTERVAL_1MINUTE

    os.makedirs(save_dir, exist_ok=True)
    filename = os.path.join(save_dir, kline_file_name(symbol))

    if update_mode:
        real_start = get_resume_start_ms(filename, start_date)
    else:
        real_start = start_date

    print(f"\n⏳ 正在拉取 {symbol}")
    print(f"市场类型: {market_type}")
    print(f"保存目录: {save_dir}")
    print(f"文件路径: {filename}")
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
        final_df = final_df.drop_duplicates(
            subset=["open_time"],
            keep="last"
        ).sort_values("open_time").reset_index(drop=True)

        final_df = add_datetime_columns(final_df)
        return final_df

    df = pd.concat([old_df, new_df], ignore_index=True)

    df = df.drop_duplicates(subset=["open_time"], keep="last")
    df = df.sort_values("open_time").reset_index(drop=True)

    if drop_last_unclosed:
        df = drop_last_row(df)

    df = add_datetime_columns(df)

    # 原子写（同现货下载器）：暂存区写完整文件再 os.replace 覆盖
    atomic_write_csv(df, filename, index=False, encoding='utf-8-sig')

    print(f"✅ 已保存到: {filename}")
    print(f"✅ 当前总行数: {len(df)}")
    print(f"✅ 本次抓取原始新增: {len(new_df)}")

    return df


if __name__ == "__main__":
    stock_list = [
        'ETH', 'BTC', 'SOL', 'DOGE', 'XRP',
        'ADA', 'XLM', 'BNB', 'TRX'
    ]

    for s in stock_list:
        start = time.perf_counter()

        data_acquisition(
            stock=s,
            start_date='2017-01-01 UTC',
            end_date='now UTC',
            market_type='usdt_futures',
            update_mode=True,
            drop_last_unclosed=True,
            save_dir='futures_kline_data'
        )

        end = time.perf_counter()
        print(f"{s} 花费时间 {end - start:.2f}s")