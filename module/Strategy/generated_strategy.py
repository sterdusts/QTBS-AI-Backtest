import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    
    # 计算MA250
    df['MA250'] = df['close'].rolling(window=250).mean()
    
    # 计算上下轨
    df['upper_band'] = df['MA250'] * 1.008
    df['lower_band'] = df['MA250'] * 0.992
    
    # 初始化position状态
    df['target_position'] = 0
    
    # 使用状态机逐根K线生成信号
    # 当前K线收盘确认信号，下一根K线开盘执行
    position = 0  # 初始空仓
    
    for i in range(len(df)):
        if i < 250:  # 前250根K线没有MA250，保持空仓
            position = 0
        else:
            close = df['close'].iloc[i]
            upper = df['upper_band'].iloc[i]
            lower = df['lower_band'].iloc[i]
            
            # 收盘价突破上轨 -> 做多
            if close > upper:
                position = 1
            # 收盘价跌破下轨 -> 做空
            elif close < lower:
                position = -1
            # 价格在上下轨之间，保持原有仓位不变
            # 不执行任何操作，position保持上一根K线的值
        
        df.loc[df.index[i], 'target_position'] = position
    
    # 确保target_position只包含-1, 0, 1
    df['target_position'] = df['target_position'].astype(int)
    
    return df