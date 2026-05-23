import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 计算超短周期均线 MA5 和 MA10
    df["ma5"] = df["close"].rolling(window=5).mean()
    df["ma10"] = df["close"].rolling(window=10).mean()

    # 初始化 target_position 为 NaN
    df["target_position"] = np.nan

    # 逐根 K 线处理 position 状态机
    # 策略逻辑：MA5 上穿 MA10 做多，MA5 下穿 MA10 做空
    # 多空反手直接切换，不等待确认，始终保持持仓状态
    position = 0  # 初始状态：空仓
    for i in range(len(df)):
        if i == 0:
            # 第一根 K 线无法判断交叉，保持空仓
            df.loc[df.index[i], "target_position"] = 0
            position = 0
            continue

        # 获取当前 K 线的 ma5 和 ma10
        ma5_prev = df["ma5"].iloc[i-1]
        ma5_curr = df["ma5"].iloc[i]
        ma10_prev = df["ma10"].iloc[i-1]
        ma10_curr = df["ma10"].iloc[i]

        # 判断上穿：前一根 ma5 <= ma10 且 当前 ma5 > ma10
        if ma5_prev <= ma10_prev and ma5_curr > ma10_curr:
            position = 1  # 做多
        # 判断下穿：前一根 ma5 >= ma10 且 当前 ma5 < ma10
        elif ma5_prev >= ma10_prev and ma5_curr < ma10_curr:
            position = -1  # 做空
        # 否则保持当前持仓状态不变

        df.loc[df.index[i], "target_position"] = position

    # 处理 NaN（理论上不会出现，但保留安全处理）
    df["target_position"] = df["target_position"].ffill().fillna(0)
    df["target_position"] = df["target_position"].astype(int)

    return df