import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    策略名称：ZLMACD + TMA + TYP 三线共振趋势跟踪策略（方案二简化版）
    
    策略原理：
    本策略基于三个互补指标构建趋势识别与入场确认的分层过滤系统：
    
    1. ZLMACD（零滞后MACD）：
       - 使用DEMA（双重指数移动平均）替代传统EMA计算MACD
       - DEMA = 2 * EMA(close, N) - EMA(EMA(close, N), N)
       - 相比传统MACD，对近期价格变化更敏感，滞后更小
       - ZLMACD > 0 表示短期动量向上，趋势偏多
       - ZLMACD < 0 表示短期动量向下，趋势偏空
    
    2. TMA（三角形移动平均线）：
       - 对价格进行双重平滑，中间时点权重最高，两端递减
       - 噪声过滤能力强，适合作为趋势确认工具
       - 收盘价 > TMA 表示价格处于均线上方，趋势偏多
       - 收盘价 < TMA 表示价格处于均线下方，趋势偏空
    
    3. TYP（典型价格）：
       - TYP = (high + low + close) / 3
       - 包含日内完整价格区间信息，比单一收盘价更全面
       - 计算TYP的快速均线(TYPMA1)和慢速均线(TYPMA2)
       - TYPMA1 > TYPMA2 表示典型价格均线多头排列，趋势偏多
       - TYPMA1 < TYPMA2 表示典型价格均线空头排列，趋势偏空
    
    入场条件（三线共振做多）：
       - ZLMACD > 0（动量向上）
       - TYPMA1 > TYPMA2（典型价格均线多头排列）
       - 收盘价 > TMA（价格位于双重平滑均线上方）
    
    出场条件（任一条件失效）：
       - ZLMACD < 0 或
       - TYPMA1 < TYPMA2 或
       - 收盘价 < TMA
    
    参数说明（可根据不同周期和品种优化）：
       - N1 = 20（ZLMACD快线周期）
       - N2 = 100（ZLMACD慢线周期）
       - N_TMA = 20（TMA周期）
       - N1_TYP = 20（TYP快线周期）
       - N2_TYP = 100（TYP慢线周期）
    
    注意：本策略仅做多，不做空。
    """
    df = df.copy()
    
    # ========== 参数定义 ==========
    N1 = 20      # ZLMACD快线周期
    N2 = 100     # ZLMACD慢线周期
    N_TMA = 20   # TMA周期
    N1_TYP = 20  # TYP快线周期
    N2_TYP = 100 # TYP慢线周期
    
    # ========== 1. 计算ZLMACD ==========
    # 计算DEMA（双重指数移动平均）
    ema1_n1 = df['close'].ewm(span=N1, adjust=False).mean()
    ema2_n1 = ema1_n1.ewm(span=N1, adjust=False).mean()
    dema_n1 = 2 * ema1_n1 - ema2_n1
    
    ema1_n2 = df['close'].ewm(span=N2, adjust=False).mean()
    ema2_n2 = ema1_n2.ewm(span=N2, adjust=False).mean()
    dema_n2 = 2 * ema1_n2 - ema2_n2
    
    # ZLMACD = DEMA(快线) - DEMA(慢线)
    df['zlm acd'] = dema_n1 - dema_n2
    
    # ========== 2. 计算TMA（三角形移动平均线） ==========
    # TMA = SMA(SMA(close, N), N)
    sma1 = df['close'].rolling(window=N_TMA, min_periods=N_TMA).mean()
    df['tma'] = sma1.rolling(window=N_TMA, min_periods=N_TMA).mean()
    
    # ========== 3. 计算TYP及TYPMA ==========
    # TYP = (high + low + close) / 3
    df['typ'] = (df['high'] + df['low'] + df['close']) / 3
    
    # TYPMA1 = EMA(TYP, N1_TYP)
    df['typma1'] = df['typ'].ewm(span=N1_TYP, adjust=False).mean()
    
    # TYPMA2 = EMA(TYP, N2_TYP)
    df['typma2'] = df['typ'].ewm(span=N2_TYP, adjust=False).mean()
    
    # ========== 4. 生成交易信号 ==========
    df['target_position'] = np.nan
    
    # 入场条件：三线共振做多
    long_entry = (
        (df['zlm acd'] > 0) &
        (df['typma1'] > df['typma2']) &
        (df['close'] > df['tma'])
    )
    
    # 出场条件：任一条件失效
    long_exit = (
        (df['zlm acd'] < 0) |
        (df['typma1'] < df['typma2']) |
        (df['close'] < df['tma'])
    )
    
    # 设置信号
    df.loc[long_entry, 'target_position'] = 1
    df.loc[long_exit, 'target_position'] = 0
    
    # 延续上一根K线的状态
    df['target_position'] = df['target_position'].ffill()
    
    # 处理NaN，默认空仓
    df['target_position'] = df['target_position'].fillna(0)
    df['target_position'] = df['target_position'].astype(int)
    
    return df