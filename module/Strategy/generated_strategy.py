import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 计算EMA12和EMA26
    df["ema12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema26"] = df["close"].ewm(span=26, adjust=False).mean()

    # 判断金叉和死叉
    # 金叉：当前ema12 > ema26 且 上一根K线ema12 <= ema26
    df["golden_cross"] = (df["ema12"] > df["ema26"]) & (df["ema12"].shift(1) <= df["ema26"].shift(1))
    # 死叉：当前ema12 < ema26 且 上一根K线ema12 >= ema26
    df["death_cross"] = (df["ema12"] < df["ema26"]) & (df["ema12"].shift(1) >= df["ema26"].shift(1))

    # 初始化target_position
    df["target_position"] = np.nan

    # 金叉时做多
    df.loc[df["golden_cross"], "target_position"] = 1
    # 死叉时平多至空仓
    df.loc[df["death_cross"], "target_position"] = 0

    # 信号延续：未产生新信号时保持上一根K线的持仓状态
    df["target_position"] = df["target_position"].ffill().fillna(0)
    df["target_position"] = df["target_position"].astype(int)

    return df