"""Backtest market-data validation shared by the single and portfolio engines."""

import numpy as np
import pandas as pd


OHLC_COLUMNS = ("open", "high", "low", "close")


def validate_backtest_frame(
    df: pd.DataFrame,
    *,
    label: str = "K 线",
    allow_all_nan_rows: bool = False,
) -> None:
    """Reject inputs that can make fills, stops, liquidation, or MTM meaningless."""

    if not isinstance(df, pd.DataFrame):
        raise ValueError(f"{label} 必须是 pandas DataFrame")
    if df.empty:
        raise ValueError(f"{label}不能为空")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{label} index 必须是 DatetimeIndex")
    if not df.index.is_unique:
        raise ValueError(f"{label}时间索引存在重复")
    if not df.index.is_monotonic_increasing:
        raise ValueError(f"{label}时间索引必须单调递增")

    missing = [c for c in OHLC_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{label}缺少必要字段: {missing}")

    try:
        values = df[list(OHLC_COLUMNS)].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} OHLC 必须是数值") from exc

    finite = np.isfinite(values)
    row_all_finite = finite.all(axis=1)
    row_all_nan = np.isnan(values).all(axis=1)

    if allow_all_nan_rows:
        invalid_missing = ~(row_all_finite | row_all_nan)
        valid_rows = row_all_finite
        if invalid_missing.any():
            raise ValueError(
                f"{label}存在 OHLC 部分缺失或非有限值的行；"
                "同一行必须全部有效，或全部为 NaN"
            )
    else:
        if not row_all_finite.all():
            raise ValueError(f"{label} OHLC 存在 NaN 或无穷值")
        valid_rows = row_all_finite

    if not valid_rows.any():
        return

    o, h, low, c = values[valid_rows].T
    if ((o <= 0) | (h <= 0) | (low <= 0) | (c <= 0)).any():
        raise ValueError(f"{label} OHLC 价格必须大于 0")

    impossible = (
        (h < np.maximum(o, c))
        | (low > np.minimum(o, c))
        | (h < low)
    )
    if impossible.any():
        raise ValueError(
            f"{label}存在不可能的 OHLC 几何关系："
            "high 必须不低于 open/close，low 必须不高于 open/close"
        )
