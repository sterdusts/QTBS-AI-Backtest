"""
webUI 辅助函数测试（日期解析、参数校验、过滤、格式化）。

注意：import webUI 会构建 Gradio Blocks（不启动服务），首次较慢属正常。
"""

import pandas as pd
import pytest

from webUI import (
    clamp_score,
    filter_df_by_date,
    format_number,
    normalize_date,
    resolve_strategy_route,
    validate_backtest_params,
    validate_date_range,
)


# =========================================================
# 日期解析
# =========================================================

@pytest.mark.parametrize("raw,expected", [
    ("2017-01-13", "2017-01-13"),
    ("2017/1/13", "2017-01-13"),
    ("2017.1.13", "2017-01-13"),
    ("2017年1月13日", "2017-01-13"),
    ("13/1/2017", "2017-01-13"),          # 日/月/年
    ("1/13/2017", "2017-01-13"),          # 月/日/年（第二位 > 12）
    ("2017-01-13 00:00:00", "2017-01-13"),
    ("2017-01-13T00:00:00", "2017-01-13"),
])
def test_normalize_date_formats(raw, expected):
    assert normalize_date(raw, "2017-01-01") == expected


def test_normalize_date_empty_returns_default():
    assert normalize_date(None, "2020-05-05") == "2020-05-05"
    assert normalize_date("   ", "2020-05-05") == "2020-05-05"


@pytest.mark.parametrize("raw", ["abc", "2017-01", "13-01", "2017-13-45"])
def test_normalize_date_invalid_raises(raw):
    with pytest.raises(ValueError):
        normalize_date(raw, "2017-01-01")


def test_validate_date_range():
    validate_date_range("2017-01-01", "2020-01-01", "zh")

    with pytest.raises(ValueError):
        validate_date_range("2016-12-31", "2020-01-01", "zh")

    with pytest.raises(ValueError):
        validate_date_range("2020-01-02", "2020-01-01", "zh")


# =========================================================
# K线时间过滤
# =========================================================

def test_filter_includes_full_end_day():
    idx = pd.date_range("2024-01-01", "2024-01-15 23:00", freq="1h")
    df = pd.DataFrame({"close": 1.0}, index=idx)

    filtered = filter_df_by_date(df, "2024-01-10", "2024-01-15")

    assert filtered.index.min() == pd.Timestamp("2024-01-10 00:00")
    # 结束日当天的 K 线全部包含
    assert filtered.index.max() == pd.Timestamp("2024-01-15 23:00")


# =========================================================
# 回测参数校验
# =========================================================

def test_params_defaults():
    cash, lev, eff_lev, pos, fee, slip = validate_backtest_params(
        None, None, None, None, None, "zh",
    )
    assert cash == pytest.approx(1000.0)
    assert lev == 1
    assert eff_lev == 1
    assert pos == pytest.approx(100.0)
    assert fee == pytest.approx(0.05)
    assert slip == pytest.approx(0.0)


def test_params_leverage_zero_means_1x():
    _, lev, eff_lev, *_ = validate_backtest_params(1000, 0, 100, 0.05, 0, "zh")
    assert lev == 0
    assert eff_lev == 1


@pytest.mark.parametrize("kwargs", [
    dict(initial_cash=0),
    dict(initial_cash=-1),
    dict(leverage=201),
    dict(leverage=-1),
    dict(leverage=1.5),
    dict(position_size=101),
    dict(position_size=-5),
    dict(fee=-0.01),
    dict(slip=-0.01),
])
def test_params_invalid_raises(kwargs):
    params = dict(initial_cash=1000, leverage=1, position_size=100, fee=0.05, slip=0)
    params.update(kwargs)
    with pytest.raises(ValueError):
        validate_backtest_params(
            params["initial_cash"], params["leverage"], params["position_size"],
            params["fee"], params["slip"], "zh",
        )


# =========================================================
# 契约路由
# =========================================================

V1_PLAIN = """
import pandas as pd

def generate_signals(df):
    df["target_position"] = 0
    return df
"""

V1_WITH_SYMBOL = """
import pandas as pd

CONTRACT_VERSION = 1
SYMBOLS = ["ETHUSDT"]

def generate_signals(df):
    df["target_position"] = 0
    return df
"""

V2_HEDGE = """
import pandas as pd

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

def generate_signals(data):
    return data
"""

V2_NO_SYMBOLS = """
import pandas as pd

CONTRACT_VERSION = 2

def generate_signals(data):
    return data
"""


def test_route_v1_uses_ui_symbol():
    assert resolve_strategy_route(V1_PLAIN, "BTC") == (1, ["BTCUSDT"])
    assert resolve_strategy_route(V1_PLAIN, "sol") == (1, ["SOLUSDT"])


def test_route_v1_code_symbol_overrides_ui():
    # 策略文本点名了 ETH → 代码声明 SYMBOLS，优先于 UI 选择
    assert resolve_strategy_route(V1_WITH_SYMBOL, "BTC") == (1, ["ETHUSDT"])


def test_route_v2():
    assert resolve_strategy_route(V2_HEDGE, "BTC") == (2, ["BTCUSDT", "ETHUSDT"])


def test_route_v2_dedups_symbols():
    code = V2_HEDGE.replace(
        'SYMBOLS = ["BTCUSDT", "ETHUSDT"]',
        'SYMBOLS = ["BTCUSDT", "BTCUSDT", "ETHUSDT"]',
    )
    assert resolve_strategy_route(code, "BTC") == (2, ["BTCUSDT", "ETHUSDT"])


def test_route_v2_bad_symbol_format_raises():
    # 策略代码内部按 SYMBOLS 原样引用面板键，非规范格式必须明确报错
    code = V2_HEDGE.replace('"BTCUSDT"', '"btc"')
    with pytest.raises(ValueError, match="USDT"):
        resolve_strategy_route(code, "BTC")


def test_route_v2_without_symbols_raises():
    with pytest.raises(ValueError, match="SYMBOLS"):
        resolve_strategy_route(V2_NO_SYMBOLS, "BTC")


def test_route_unknown_version_raises():
    code = V1_PLAIN.replace(
        "import pandas as pd",
        "import pandas as pd\n\nCONTRACT_VERSION = 3",
    )
    with pytest.raises(ValueError, match="未知契约版本"):
        resolve_strategy_route(code, "BTC")


def test_route_v1_multi_symbol_raises():
    # v1 多标的不允许静默截断成第一个标的（会无声丢掉对冲腿）
    code = V1_WITH_SYMBOL.replace(
        'SYMBOLS = ["ETHUSDT"]',
        'SYMBOLS = ["ETHUSDT", "BTCUSDT"]',
    )
    with pytest.raises(ValueError, match="CONTRACT_VERSION = 2"):
        resolve_strategy_route(code, "BTC")


# =========================================================
# 格式化辅助
# =========================================================

def test_clamp_score():
    assert clamp_score(150) == pytest.approx(99.99)
    assert clamp_score(-5) == pytest.approx(0.0)
    assert clamp_score("abc") == pytest.approx(0.0)
    assert clamp_score(88.5) == pytest.approx(88.5)


def test_format_number():
    assert format_number(None, na_text="-") == "-"
    assert format_number(float("nan"), na_text="-") == "-"
    assert format_number(float("inf")) == "∞"
    assert format_number(1.23456, digits=2) == "1.23"
    assert format_number("not a number", na_text="N/A") == "N/A"
