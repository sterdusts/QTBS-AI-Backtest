# modules/kline_builder.py

import pandas as pd


class KlineBuilder:
    """
    K线构造器。

    核心职责：
    1. 清洗 1m 原始K线
    2. 从 1m 构造任意周期K线
    3. 给高周期K线添加 close_time
    4. 把高周期特征安全映射回低周期，防未来函数

    设计原则：
    - 1m 是唯一原始数据源
    - 其他周期全部由 1m 重采样得到
    - 高周期信号只能在高周期K线收盘后使用
    """

    def __init__(self, raw_df: pd.DataFrame):
        """
        raw_df 要求至少包含：
        open_time, open, high, low, close, volume

        可选包含：
        close_time, qav, nt, tbv, tqv, ignore
        """

        # 不保留原始帧引用：清洗结果是唯一需要的状态，
        # 原始 1m CSV 可达数百 MB，多存一份纯属浪费
        self.df_1m = self._prepare_1m_klines(raw_df)

    def _prepare_1m_klines(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗 1m 原始K线数据。
        """

        required_cols = ["open_time", "open", "high", "low", "close", "volume"]

        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"原始数据缺少必要字段: {col}")

        # 浅拷贝即可：写时复制下对 open_time 的赋值只复制该列，不动原始帧
        df = df.copy(deep=False)

        # 兼容 Binance 毫秒时间戳 / 字符串时间
        if pd.api.types.is_numeric_dtype(df["open_time"]):
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        else:
            df["open_time"] = pd.to_datetime(df["open_time"])

        # 遗留 tz-aware CSV（ISO "...+00:00"）会得到带时区的 datetime：统一剥成
        # tz-naive UTC（与 data_panel._local_data_end 同口径），否则下游用
        # tz-naive Timestamp 的比较（如 2010 幽灵行过滤、filter_df_by_date 切片、
        # align_klines 的索引并集）在 pandas 3.0 会抛 TypeError 让回测在数据
        # 加载即崩溃。numeric ms 路径产出的本就是 tz-naive，不受影响。
        if getattr(df["open_time"].dt, "tz", None) is not None:
            df["open_time"] = df["open_time"].dt.tz_convert("UTC").dt.tz_localize(None)

        # 只保留核心 OHLCV
        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        # 排序、去重
        df = df.sort_values("open_time")
        df = df.drop_duplicates(subset=["open_time"], keep="last")

        # 设置时间索引
        df = df.set_index("open_time")

        # 转成数值
        numeric_cols = ["open", "high", "low", "close", "volume"]

        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 删除空值
        df = df.dropna(subset=numeric_cols)

        # 删除异常价格
        df = df[
            (df["open"] > 0) &
            (df["high"] > 0) &
            (df["low"] > 0) &
            (df["close"] > 0)
        ]

        # 时间戳合理性底线：损坏数据产出的 1677/1970 年幽灵索引会让
        # resample 物化数百年的分箱直接 OOM
        df = df[df.index >= pd.Timestamp("2010-01-01")]

        return df

    def get_1m(self) -> pd.DataFrame:
        """
        返回清洗后的 1m K线。

        直接返回内部帧（数百 MB 级，拷贝是纯浪费）：
        调用方在写时复制语义下的任何写入都不会影响本对象。
        """

        return self.df_1m

    def build(self, interval: str, drop_incomplete: bool = True) -> pd.DataFrame:
        """
        从 1m K线构造指定周期K线。

        interval 示例：
        - "5min"
        - "15min"
        - "1h"
        - "4h"
        - "1d"

        注意：
        pandas 推荐用 "5min"，不要用 "5m"。
        """

        # resample 不改写源帧，无需拷贝
        df = self.df_1m

        agg_dict = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }

        kline = df.resample(
            interval,
            label="left",
            closed="left"
        ).agg(agg_dict)

        kline = kline.dropna()

        # 添加 close_time
        offset = pd.tseries.frequencies.to_offset(interval)
        kline["close_time"] = kline.index + offset

        # 删除最后一根未完成K线
        if drop_incomplete:
            # 最后一根 1m K线 open_time 为 t，覆盖区间 [t, t+1min)，
            # 数据实际覆盖到 t + 1min。
            # 直接用 close_time <= t 会把一根实际已完成的高周期K线误删。
            data_end_time = df.index.max() + pd.Timedelta(minutes=1)
            kline = kline[kline["close_time"] <= data_end_time]

        return kline

    def build_many(self, intervals: list[str], drop_incomplete: bool = True) -> dict:
        """
        一次性构造多个周期K线。

        返回：
        {
            "5min": df_5m,
            "1h": df_1h,
            "4h": df_4h
        }
        """

        result = {}

        for interval in intervals:
            result[interval] = self.build(
                interval=interval,
                drop_incomplete=drop_incomplete
            )

        return result

    @staticmethod
    def add_close_time(df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """
        给已有K线补充 close_time。
        """

        result = df.copy()

        if not isinstance(result.index, pd.DatetimeIndex):
            raise ValueError("K线 DataFrame 的 index 必须是 DatetimeIndex")

        offset = pd.tseries.frequencies.to_offset(interval)
        result["close_time"] = result.index + offset

        return result

    @staticmethod
    def shift_features(
        df: pd.DataFrame,
        feature_cols: list[str],
        shift_periods: int = 1
    ) -> pd.DataFrame:
        """
        将指定特征列整体后移，防止未来函数。

        举例：
        4h K线在 08:00-12:00 期间形成，
        它的信号不能在 08:00 使用，
        只能在 12:00 后使用。

        所以高周期特征一般要 shift(1)。
        """

        result = df.copy()

        for col in feature_cols:
            if col not in result.columns:
                raise ValueError(f"缺少特征列: {col}")

        result[feature_cols] = result[feature_cols].shift(shift_periods)

        return result

    @staticmethod
    def map_higher_to_lower(
        lower_df: pd.DataFrame,
        higher_df: pd.DataFrame,
        feature_cols: list[str],
        keep_higher_close_time: bool = False
    ) -> pd.DataFrame:
        """
        将高周期特征安全映射到低周期K线。

        关键点：
        使用 higher_df["close_time"] 作为高周期信号的可用时间。

        这可以防止：
        4h K线刚开盘，就提前使用4h收盘结果的问题。
        """

        lower = lower_df.copy()
        higher = higher_df.copy()

        if not isinstance(lower.index, pd.DatetimeIndex):
            raise ValueError("lower_df 的 index 必须是 DatetimeIndex")

        if not isinstance(higher.index, pd.DatetimeIndex):
            raise ValueError("higher_df 的 index 必须是 DatetimeIndex")

        if "close_time" not in higher.columns:
            raise ValueError("higher_df 必须包含 close_time 列")

        for col in feature_cols:
            if col not in higher.columns:
                raise ValueError(f"higher_df 缺少特征列: {col}")

        # 高周期特征只在 close_time 后才可用
        higher_features = higher[["close_time"] + feature_cols].dropna()
        higher_features = higher_features.sort_values("close_time")

        # 低周期时间
        lower_reset = lower.reset_index()

        # 兼容 index 名字
        time_col = lower_reset.columns[0]
        lower_reset = lower_reset.rename(columns={time_col: "time"})

        mapped = pd.merge_asof(
            lower_reset.sort_values("time"),
            higher_features,
            left_on="time",
            right_on="close_time",
            direction="backward"
        )

        mapped = mapped.set_index("time")

        if not keep_higher_close_time:
            mapped = mapped.drop(columns=["close_time"], errors="ignore")

        return mapped

    @staticmethod
    def validate_no_future_mapping(
        mapped_df: pd.DataFrame,
        higher_close_col: str = "close_time"
    ) -> bool:
        """
        简单检查映射是否存在未来函数。

        如果保留了 higher close_time，
        可以检查每一行低周期时间是否 >= 高周期 close_time。

        注意：
        使用前 map_higher_to_lower 需要设置：
        keep_higher_close_time=True
        """

        if higher_close_col not in mapped_df.columns:
            raise ValueError(f"mapped_df 缺少 {higher_close_col}，无法检查")

        check_df = mapped_df.dropna(subset=[higher_close_col])

        invalid = check_df.index < check_df[higher_close_col]

        if invalid.any():
            return False

        return True