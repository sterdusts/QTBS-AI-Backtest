import pandas as pd
import numpy as np

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    전략 설명:
    이 전략은 WMA(가중이동평균), DDI(방향성 이동지수), HMA(고가 이동평균) 세 가지 지표를 사용하여
    추세 방향 → 에너지 확인 → 돌파 확인의 3단계 필터링 시스템을 구축합니다.

    진입 조건 (롱):
    1. 종가가 WMA(20)를 상향 돌파 (추세 방향 상승)
    2. DDI가 0선을 상향 돌파 (상승 에너지 확인)
    3. 고가가 HMA(20)를 상향 돌파 (돌파 강도 확인)
    위 세 조건이 모두 만족되면 롱 포지션 진입 (target_position = 1)

    청산 조건 (롱):
    - 종가가 WMA(20)를 하향 돌파하면 청산 (target_position = 0)

    참고: 사용자가 명시적으로 공매도를 언급하지 않았으므로, 공매도는 사용하지 않습니다.
    """
    df = df.copy()

    # --- 지표 계산 ---

    # 1. WMA (가중이동평균, N=20)
    # WMA = (N*CLOSE + (N-1)*REF(CLOSE,1) + ... + 1*REF(CLOSE,N-1)) / (1+2+...+N)
    n_wma = 20
    weights = np.arange(1, n_wma + 1)
    def calc_wma(series):
        return series.rolling(window=n_wma).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    df['wma20'] = calc_wma(df['close'])

    # 2. DDI (방향성 이동지수, N=40)
    n_ddi = 40
    df['hl'] = df['high'] + df['low']
    df['hl_prev'] = df['hl'].shift(1)
    df['high_diff'] = (df['high'] - df['high'].shift(1)).abs()
    df['low_diff'] = (df['low'] - df['low'].shift(1)).abs()
    df['max_diff'] = df[['high_diff', 'low_diff']].max(axis=1)

    df['dmz'] = np.where(df['hl'] > df['hl_prev'], df['max_diff'], 0)
    df['dmf'] = np.where(df['hl'] < df['hl_prev'], df['max_diff'], 0)

    df['sum_dmz'] = df['dmz'].rolling(window=n_ddi).sum()
    df['sum_dmf'] = df['dmf'].rolling(window=n_ddi).sum()
    df['diz'] = df['sum_dmz'] / (df['sum_dmz'] + df['sum_dmf'] + 1e-10)
    df['dif'] = df['sum_dmf'] / (df['sum_dmz'] + df['sum_dmf'] + 1e-10)
    df['ddi'] = df['diz'] - df['dif']

    # 3. HMA (고가 이동평균, N=20)
    n_hma = 20
    df['hma20'] = df['high'].rolling(window=n_hma).mean()

    # --- 신호 조건 ---

    # 진입 조건: 종가가 WMA 상향 돌파 AND DDI가 0 상향 돌파 AND 고가가 HMA 상향 돌파
    df['close_above_wma'] = df['close'] > df['wma20']
    df['close_cross_wma_up'] = (df['close'] > df['wma20']) & (df['close'].shift(1) <= df['wma20'].shift(1))

    df['ddi_above_zero'] = df['ddi'] > 0
    df['ddi_cross_zero_up'] = (df['ddi'] > 0) & (df['ddi'].shift(1) <= 0)

    df['high_above_hma'] = df['high'] > df['hma20']
    df['high_cross_hma_up'] = (df['high'] > df['hma20']) & (df['high'].shift(1) <= df['hma20'].shift(1))

    # 청산 조건: 종가가 WMA 하향 돌파
    df['close_cross_wma_down'] = (df['close'] < df['wma20']) & (df['close'].shift(1) >= df['wma20'].shift(1))

    # --- 포지션 설정 ---
    df['target_position'] = np.nan

    # 진입: 세 조건이 동시에 만족될 때 롱 진입
    long_entry = df['close_cross_wma_up'] & df['ddi_cross_zero_up'] & df['high_cross_hma_up']
    df.loc[long_entry, 'target_position'] = 1

    # 청산: 종가가 WMA 하향 돌파 시 청산
    df.loc[df['close_cross_wma_down'], 'target_position'] = 0

    # 신호가 없는 경우 이전 포지션 유지
    df['target_position'] = df['target_position'].ffill().fillna(0)
    df['target_position'] = df['target_position'].astype(int)

    # 불필요한 중간 컬럼 제거 (선택사항)
    cols_to_drop = ['hl', 'hl_prev', 'high_diff', 'low_diff', 'max_diff', 'dmz', 'dmf',
                    'sum_dmz', 'sum_dmf', 'diz', 'dif',
                    'close_above_wma', 'close_cross_wma_up',
                    'ddi_above_zero', 'ddi_cross_zero_up',
                    'high_above_hma', 'high_cross_hma_up', 'close_cross_wma_down']
    df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])

    return df