import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    策略名称：双均线趋势跟踪策略（做多版）
    
    策略原理：
    - 使用两条指数移动平均线（EMA）：快线EMA12和慢线EMA26
    - 当快线上穿慢线时，产生买入信号，开多仓
    - 当快线下穿慢线时，产生卖出信号，平多仓
    - 仅做多，不做空
    - 信号在当前K线收盘后确认，下一根K线开盘执行
    
    适用周期：任意K线周期（如4h、1h、日线等）
    """
    df = df.copy()
    
    # 计算EMA指标
    df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
    
    # 生成信号条件
    # 上穿：当前快线>慢线 且 上一根K线快线<=慢线
    df['cross_above'] = (df['ema12'] > df['ema26']) & (df['ema12'].shift(1) <= df['ema26'].shift(1))
    # 下穿：当前快线<慢线 且 上一根K线快线>=慢线
    df['cross_below'] = (df['ema12'] < df['ema26']) & (df['ema12'].shift(1) >= df['ema26'].shift(1))
    
    # 初始化target_position
    df['target_position'] = np.nan
    
    # 开多条件：快线上穿慢线
    df.loc[df['cross_above'], 'target_position'] = 1
    # 平多条件：快线下穿慢线
    df.loc[df['cross_below'], 'target_position'] = 0
    
    # 处理NaN：向前填充保持持仓状态，初始为0（空仓）
    df['target_position'] = df['target_position'].ffill().fillna(0)
    df['target_position'] = df['target_position'].astype(int)
    
    # 删除辅助列（可选，保留便于调试）
    # df.drop(columns=['ema12', 'ema26', 'cross_above', 'cross_below'], inplace=True)
    
    return df