"""
多资产对齐数据层（契约 v2 的数据输入，见 STRATEGY_CONTRACT.md）。

职责：
1. 按需加载单标的、单周期 K 线（只重采样需要的周期，带进程内缓存）
2. 把多个标的的 K 线对齐到统一时间轴（索引并集）

对齐原则：
- 索引取所有标的的并集：早上市的标的保留完整历史
- 晚上市的标的在上市前为 NaN：不伪造数据，不前向填充价格
- 引擎负责把 NaN 视为「不可交易」（权重强制为 0）

缓存原则：
- 按 (symbol, timeframe) 缓存重采样结果，以 CSV 文件 mtime 判断失效
- 清洗后的 1m 帧只保留最近一份（单槽）：同一标的换周期回测不必
  重新解析数百 MB 的 CSV，又不会随标的数量线性占用内存
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pandas as pd

# 数据层与引擎全链路用浅拷贝防策略篡改，正确性依赖写时复制（CoW）：
# pandas 3.0 起强制开启；2.x 默认关闭，必须在这里显式打开，
# 否则策略的就地写入会穿透浅拷贝污染进程级缓存帧
if int(pd.__version__.split(".")[0]) < 3:
    pd.set_option("mode.copy_on_write", True)

from module.modules.kline_builder import KlineBuilder
from module.modules.Load_real_kline import (
    KLINE_FILE_SUFFIX,
    Obtain_K,
    get_kline_file_path,
    has_kline_data,
    normalize_symbol,
)


DEFAULT_DATA_DIR = os.path.join("cryptocurrency_data", "kline_data")

# webUI 周期 -> pandas resample 别名
# 注意：pandas 4 起日线必须用大写 "1D"
TIMEFRAME_TO_PANDAS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}

# (symbol, timeframe, data_dir) -> {"mtime": float, "df": DataFrame}
# 插入序即近期使用序：超过上限时淘汰最早的条目，长驻进程不无界增长
_KLINE_CACHE: dict = {}
_KLINE_CACHE_MAX_ENTRIES = 32

# 最近一次解析出的清洗后 1m 数据（单槽）。
# 槽位必须整体存取单个 (key, builder) 元组：分两个字段写入在并行
# 加载下会交错出「key 是 B、builder 是 A」的错配槽，后续命中会把
# A 标的的数据当成 B 返回并永久毒化重采样缓存。
_LAST_BUILDER: dict = {"slot": None}


def clear_cache() -> None:
    """清空 K 线缓存（主要用于测试）。"""
    _KLINE_CACHE.clear()
    _LAST_BUILDER["slot"] = None


def list_local_symbols(data_dir: str = DEFAULT_DATA_DIR) -> list:
    """
    列出本地已有 K 线数据的标的（给 AI 生成 prompt 提供可用标的清单）。
    """

    if not os.path.isdir(data_dir):
        return []

    symbols = []

    for name in sorted(os.listdir(data_dir)):
        if name.endswith(KLINE_FILE_SUFFIX):
            symbols.append(name[: -len(KLINE_FILE_SUFFIX)])

    return symbols


def _get_builder(symbol: str, file_path: str, mtime: float, data_dir: str) -> KlineBuilder:
    """取清洗后的 1m 构造器：命中单槽缓存则跳过整个 CSV 解析。"""

    key = (symbol, mtime, os.path.abspath(data_dir))

    # 单次读出整个槽位（GIL 下原子），与 clear_cache/其他线程的整槽
    # 写入不会交错出半新半旧的状态
    slot = _LAST_BUILDER["slot"]
    if slot is not None and slot[0] == key:
        return slot[1]

    builder = KlineBuilder(pd.read_csv(file_path))
    _LAST_BUILDER["slot"] = (key, builder)

    return builder


def _ensure_local_data(symbol: str, data_dir: str, auto_fetch: bool) -> None:
    """确保本地有该标的的 1m 数据；拉取后必须验证文件确实落盘。"""

    if has_kline_data(symbol, data_dir=data_dir):
        return

    if not auto_fetch:
        raise FileNotFoundError(
            f"找不到 {symbol} 的本地 K 线数据（目录: {data_dir}），且 auto_fetch=False"
        )

    print(f"正在拉取 {symbol} 数据")
    # 必须把 data_dir 透传给拉取函数，否则数据会下载到默认目录、
    # 随后在 data_dir 里仍然找不到文件
    Obtain_K(symbol, save_dir=data_dir)

    # 下载层吞掉网络异常时只会留下空手而归的目录：必须在这里把
    # 「拉取失败」翻译成可行动的报错，而不是放任后续抛出指向
    # 一个本就不该存在的文件的 FileNotFoundError
    if not has_kline_data(symbol, data_dir=data_dir):
        raise ValueError(
            f"自动拉取 {symbol} 数据失败（交易对不存在或网络错误），"
            "请检查标的名称与网络连接后重试。"
        )


def load_symbol_kline(
    symbol: str,
    timeframe: str,
    data_dir: str = DEFAULT_DATA_DIR,
    auto_fetch: bool = True,
) -> pd.DataFrame:
    """
    加载单标的、单周期 K 线。

    - 只构造请求的周期（多标的场景下避免 6 倍重采样开销）
    - 重采样结果按 CSV mtime 缓存，重复回测不再重读 CSV
    """

    symbol = normalize_symbol(symbol)

    if timeframe not in TIMEFRAME_TO_PANDAS:
        raise ValueError(
            f"不支持的周期: {timeframe}，可用周期: {list(TIMEFRAME_TO_PANDAS.keys())}"
        )

    _ensure_local_data(symbol, data_dir, auto_fetch)

    file_path = get_kline_file_path(symbol, data_dir=data_dir)
    mtime = os.path.getmtime(file_path)

    cache_key = (symbol, timeframe, os.path.abspath(data_dir))
    cached = _KLINE_CACHE.get(cache_key)

    if cached is not None and cached["mtime"] == mtime:
        # 浅拷贝即可：pandas 写时复制下调用方的写入不会污染缓存
        return cached["df"].copy(deep=False)

    builder = _get_builder(symbol, file_path, mtime, data_dir)

    if timeframe == "1m":
        # 1m 原始级数据量大（数百 MB/标的），不进重采样缓存。
        # 浅拷贝不能省：CoW 只保护副本，对同一对象的直接修改
        # 仍会污染缓存 builder 内部的 1m 帧
        return builder.get_1m().copy(deep=False)

    df = builder.build(TIMEFRAME_TO_PANDAS[timeframe])

    _KLINE_CACHE[cache_key] = {"mtime": mtime, "df": df}

    # 简单容量上限：超限淘汰最早插入的条目，长驻进程不无界增长
    while len(_KLINE_CACHE) > _KLINE_CACHE_MAX_ENTRIES:
        _KLINE_CACHE.pop(next(iter(_KLINE_CACHE)))

    return df.copy(deep=False)


def align_klines(kline_map: dict) -> dict:
    """
    把多个标的的 K 线对齐到统一时间轴（索引并集）。

    输入：{symbol: DataFrame}（各自独立索引）
    输出：{symbol: DataFrame}（索引完全相同；标的未上市/缺数据的行为 NaN）

    不做任何价格填充：NaN 即「该 K 线该标的无数据」，由引擎处理。
    """

    if not kline_map:
        raise ValueError("kline_map 不能为空")

    union_index = None

    for symbol, df in kline_map.items():
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError(f"{symbol} 的 K 线 index 必须是 DatetimeIndex")

        union_index = df.index if union_index is None else union_index.union(df.index)

    # 单调索引的 union 本身就有序：只对真正乱序的输入兜底排序
    if not union_index.is_monotonic_increasing:
        union_index = union_index.sort_values()

    return {
        symbol: df.reindex(union_index)
        for symbol, df in kline_map.items()
    }


def load_aligned_panel(
    symbols: list,
    timeframe: str,
    data_dir: str = DEFAULT_DATA_DIR,
    auto_fetch: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    加载多标的对齐数据面板：契约 v2 中 generate_signals(data) 的标准输入。

    日期过滤在对齐之前逐标的执行：过滤是纯索引谓词，先过滤后对齐与
    先对齐后过滤结果完全等价，但对齐规模从全历史降到回测窗口。

    返回：{symbol: DataFrame}，所有 DataFrame 索引完全相同。
    """

    if not symbols:
        raise ValueError("symbols 不能为空")

    # 归一化 + 去重（保持顺序）
    normalized = []
    for s in symbols:
        s = normalize_symbol(s)
        if s not in normalized:
            normalized.append(s)

    # 缺数据的标的先串行拉取：并行打 Binance 接口会叠加请求权重
    # 触发限频/封禁，下载失败的报错也要在进池之前就抛清楚
    for s in normalized:
        _ensure_local_data(s, data_dir, auto_fetch)

    if len(normalized) == 1:
        s = normalized[0]
        kline_map = {s: load_symbol_kline(s, timeframe, data_dir, auto_fetch)}
    else:
        # read_csv（C 引擎）会释放 GIL，多标的并行解析；
        # 进程内缓存的写入是整槽/单项的 dict 赋值，GIL 下原子
        with ThreadPoolExecutor(max_workers=min(4, len(normalized))) as pool:
            futures = {
                s: pool.submit(load_symbol_kline, s, timeframe, data_dir, auto_fetch)
                for s in normalized
            }
            kline_map = {s: f.result() for s, f in futures.items()}

    if start_date or end_date:
        kline_map = {
            s: filter_df_by_date(df, start_date, end_date)
            for s, df in kline_map.items()
        }

    return align_klines(kline_map)


def filter_df_by_date(df: pd.DataFrame, start_str: str, end_str: str) -> pd.DataFrame:
    """
    按 index 时间过滤 K线（v1 单标的与 v2 面板共用的唯一实现）。

    要求 df.index 是已排序的 DatetimeIndex。
    结束日期按整天处理：包含 end_str 当天的所有 K线，
    否则 "2024-01-15" 只会匹配到当天 00:00 这一根。
    """

    start = start_str if start_str else None

    end = None
    if end_str:
        end_ts = pd.Timestamp(end_str)
        if end_ts == end_ts.normalize():
            # 纯日期：包含当天整天（.loc 切片对 DatetimeIndex 是闭区间，
            # 取「次日 0 点的前一瞬」）
            end = end_ts + timedelta(days=1) - timedelta(microseconds=1)
        else:
            # 带时间分量：按精确时刻截止
            end = end_ts

    # 有序 DatetimeIndex 上 .loc 切片是 O(log n) 定位 + 惰性共享数据，
    # 不像布尔掩码那样物化两份全量拷贝
    return df.loc[start:end]
