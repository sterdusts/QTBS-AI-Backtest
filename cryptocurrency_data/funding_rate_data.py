"""
永续合约资金费率（funding rate）历史下载器（契约 §10.8）。

与 K 线下载器（usdt_futures_K_data.py）分离：funding 是每 8h 一次的稀疏结算
序列，schema 与 OHLCV 不同，存独立并行文件 funding_data/{SYMBOL}_FUNDING.csv，
不并入 K 线 CSV（KlineBuilder 清洗会裁掉 OHLCV 之外的列）。

数据来源：Binance USDS-M `futures_funding_rate`（公开数据，匿名 client 即可）。
该接口单次最多 1000 条、需按 fundingTime 手动分页（不像 K 线接口自动翻页）。
"""

import os
import sys
import time

import pandas as pd
from binance.client import Client

# 允许独立运行（python cryptocurrency_data/funding_rate_data.py）时找到项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 文件命名/原子写单源在 Load_real_kline：下载侧与 data_panel 读取侧共用，
# 任一边单独改后缀都会让对方找不到文件
from module.modules.Load_real_kline import (
    DEFAULT_FUNDING_DIR,
    atomic_write_csv,
    funding_file_name,
    normalize_symbol,
)

# 资金费率历史文件 schema（契约 §10.8）：funding_time(ms 整数), funding_rate(float)
FUNDING_COLS = ["funding_time", "funding_rate"]

_FUNDING_PAGE_LIMIT = 1000  # futures_funding_rate 单页上限


def _to_ms(value):
    """日期字符串 → epoch 毫秒；'now'/'now UTC' 取当前时刻；naive 视为 UTC。"""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower().startswith("now"):
        return int(time.time() * 1000)
    # pd.Timestamp 不认 "2019-09-01 UTC" 这种尾缀，剥掉后统一按 UTC
    if s.upper().endswith("UTC"):
        s = s[:-3].strip()
    ts = pd.Timestamp(s)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


def load_existing_funding_df(filename: str) -> pd.DataFrame:
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return pd.DataFrame(columns=FUNDING_COLS)

    df = pd.read_csv(filename)
    if df.empty:
        return pd.DataFrame(columns=FUNDING_COLS)

    for col in FUNDING_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[FUNDING_COLS]

    # funding_time 统一为 ms 整数：与 K 线下载器同口径——字符串时间戳（旧/外部
    # 导入）先转 datetime 再换算，NaT 幽灵行丢弃，毫秒换算与 datetime64 分辨率无关
    if not pd.api.types.is_numeric_dtype(df["funding_time"]):
        dt = pd.to_datetime(df["funding_time"], utc=True, errors="coerce")
        df = df[dt.notna()]
        df["funding_time"] = (
            (dt[dt.notna()] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
        )
    else:
        df["funding_time"] = pd.to_numeric(df["funding_time"], errors="coerce")
        df = df[df["funding_time"].notna()]

    df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
    df = df[df["funding_rate"].notna()]
    return df


def get_resume_start_ms(filename, default_start_ms):
    old = load_existing_funding_df(filename)
    if old.empty:
        return default_start_ms
    last = old["funding_time"].dropna().iloc[-1]
    return int(last) + 1


def funding_records_to_df(records) -> pd.DataFrame:
    """Binance 返回的记录列表 → 标准 schema。每条含 fundingTime(ms)/fundingRate(str)。"""
    if not records:
        return pd.DataFrame(columns=FUNDING_COLS)

    df = pd.DataFrame(records)
    # schema 漂移（API 返回缺 fundingTime/fundingRate 键的记录）时退化为空表，
    # 而非让 pd.to_numeric(None) 抛 TypeError（符合 funding_acquisition「失败返回
    # 空 DataFrame」承诺；该函数在 _fetch_funding_pages 的 try 之外被调用）
    if "fundingTime" not in df.columns or "fundingRate" not in df.columns:
        return pd.DataFrame(columns=FUNDING_COLS)
    out = pd.DataFrame({
        "funding_time": pd.to_numeric(df["fundingTime"], errors="coerce").astype("Int64"),
        "funding_rate": pd.to_numeric(df["fundingRate"], errors="coerce"),
    })
    return out[out["funding_time"].notna() & out["funding_rate"].notna()]


def _fetch_funding_pages(client, symbol, start_ms, end_ms):
    """按 fundingTime 递增手动分页拉取，直到无更多或越过 end。"""
    records = []
    cur = start_ms
    while True:
        batch = client.futures_funding_rate(
            symbol=symbol, startTime=cur, endTime=end_ms, limit=_FUNDING_PAGE_LIMIT
        )
        if not batch:
            break
        records.extend(batch)
        if len(batch) < _FUNDING_PAGE_LIMIT:
            break
        nxt = int(batch[-1]["fundingTime"]) + 1
        if nxt <= cur:  # 时间不前进则停，防死循环
            break
        cur = nxt
        if end_ms is not None and cur > end_ms:
            break
    return records


def funding_acquisition(
    stock,
    start_date="2019-09-01 UTC",   # 币安 USDT 永续资金费率历史起点约在此
    end_date="now UTC",
    update_mode=True,
    save_dir=DEFAULT_FUNDING_DIR,
    client=None,
):
    """拉取某标的的历史资金费率，原子写到 funding_data/{SYMBOL}_FUNDING.csv。

    client 可注入（测试用）；默认创建匿名 Binance Client（funding 历史是公开数据）。
    返回最终落盘的 DataFrame（失败返回空 DataFrame，不抛断上层）。
    """
    if client is None:
        client = Client("", "")

    symbol = normalize_symbol(stock)
    os.makedirs(save_dir, exist_ok=True)
    filename = os.path.join(save_dir, funding_file_name(symbol))

    default_start_ms = _to_ms(start_date)
    end_ms = _to_ms(end_date)
    start_ms = (
        get_resume_start_ms(filename, default_start_ms) if update_mode else default_start_ms
    )

    try:
        records = _fetch_funding_pages(client, symbol, start_ms, end_ms)
    except Exception as e:
        print(f"❌ {symbol} 资金费率拉取失败: {e}")
        return pd.DataFrame()

    old_df = (
        load_existing_funding_df(filename) if update_mode
        else pd.DataFrame(columns=FUNDING_COLS)
    )
    new_df = funding_records_to_df(records)

    combined = pd.concat([old_df, new_df], ignore_index=True)
    if combined.empty:
        print(f"✅ {symbol} 没有资金费率数据")
        return combined

    combined = (
        combined.dropna(subset=["funding_time"])
        .drop_duplicates(subset=["funding_time"], keep="last")
        .sort_values("funding_time")
        .reset_index(drop=True)
    )
    combined["funding_time"] = combined["funding_time"].astype("int64")

    atomic_write_csv(combined, filename, index=False, encoding="utf-8-sig")
    print(f"✅ 已保存资金费率: {filename}（{len(combined)} 条）")
    return combined


if __name__ == "__main__":
    for s in ["ETH", "BTC", "SOL", "DOGE", "XRP", "ADA", "XLM", "BNB", "TRX"]:
        start = time.perf_counter()
        funding_acquisition(stock=s)
        print(f"{s} 花费时间 {time.perf_counter() - start:.2f}s")
