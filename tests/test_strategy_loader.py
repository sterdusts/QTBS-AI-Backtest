"""
strategy_loader 安全校验与内存加载测试。
对应 STRATEGY_CONTRACT.md 第 6 节安全边界。
"""

import pandas as pd
import pytest

from module.Strategy.strategy_loader import (
    load_strategy_func_from_code,
    parse_strategy_metadata,
    save_strategy_code_audit,
    validate_strategy_code,
    validate_strategy_metadata,
    validate_symbols_format,
)


VALID_CODE = """
import pandas as pd
import numpy as np

def generate_signals(df):
    df = df.copy()
    df["target_position"] = 0
    return df
"""


def make_df():
    idx = pd.date_range("2024-01-01", periods=3, freq="4h")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1.0},
        index=idx,
    )


# =========================================================
# 内存加载
# =========================================================

def test_load_valid_code():
    func = load_strategy_func_from_code(VALID_CODE)
    result = func(make_df())
    assert "target_position" in result.columns
    assert (result["target_position"] == 0).all()


def test_loaded_modules_are_isolated():
    code_a = VALID_CODE.replace('df["target_position"] = 0', 'MY_CONST = 1\n    df["target_position"] = MY_CONST')
    code_b = VALID_CODE.replace('df["target_position"] = 0', 'MY_CONST = -1\n    df["target_position"] = MY_CONST')

    func_a = load_strategy_func_from_code(code_a)
    func_b = load_strategy_func_from_code(code_b)

    # 两次加载互不污染
    assert (func_a(make_df())["target_position"] == 1).all()
    assert (func_b(make_df())["target_position"] == -1).all()


# =========================================================
# 安全校验
# =========================================================

def test_hidden_import_in_function_rejected():
    code = """
import pandas as pd
import numpy as np

def generate_signals(df):
    import urllib.request
    return df
"""
    with pytest.raises(ValueError, match="禁止导入"):
        validate_strategy_code(code)


def test_import_from_rejected():
    code = """
import pandas as pd
from urllib import request

def generate_signals(df):
    return df
"""
    with pytest.raises(ValueError, match="禁止 from"):
        validate_strategy_code(code)


def test_pandas_numpy_submodules_allowed():
    code = """
import pandas as pd
import numpy as np
import numpy.linalg
from pandas.api import types

def generate_signals(df):
    df = df.copy()
    df["target_position"] = 0
    return df
"""
    validate_strategy_code(code)


@pytest.mark.parametrize("snippet", [
    "import os",
    "import subprocess",
    "eval('1+1')",
    "open('x.txt')",
    "__import__('os')",
])
def test_forbidden_keywords_rejected(snippet):
    code = f"""
import pandas as pd

def generate_signals(df):
    {snippet}
    return df
"""
    with pytest.raises(ValueError, match="禁止"):
        validate_strategy_code(code)


def test_missing_generate_signals_rejected():
    code = """
import pandas as pd

def other_func(df):
    return df
"""
    with pytest.raises(ValueError, match="generate_signals"):
        validate_strategy_code(code)


def test_syntax_error_rejected():
    with pytest.raises(ValueError, match="语法错误"):
        validate_strategy_code("def generate_signals(df:\n    return df")


# =========================================================
# 契约元数据解析
# =========================================================

def test_metadata_defaults_to_v1():
    meta = parse_strategy_metadata(VALID_CODE)
    assert meta["contract_version"] == 1
    assert meta["symbols"] is None


def test_metadata_v2_with_symbols():
    code = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

def generate_signals(data):
    return data
"""
    meta = parse_strategy_metadata(code)
    assert meta["contract_version"] == 2
    assert meta["symbols"] == ["BTCUSDT", "ETHUSDT"]


def test_metadata_symbols_tuple():
    code = """
import pandas as pd

CONTRACT_VERSION = 1
SYMBOLS = ("ETHUSDT",)

def generate_signals(df):
    df["target_position"] = 0
    return df
"""
    meta = parse_strategy_metadata(code)
    assert meta["contract_version"] == 1
    assert meta["symbols"] == ["ETHUSDT"]


def test_metadata_invalid_version_raises():
    # 写错形式必须报错，不能静默回退 v1（否则 v2 代码被路由进 v1 引擎）
    code = """
CONTRACT_VERSION = "2"

def generate_signals(df):
    return df
"""
    with pytest.raises(ValueError, match="整数常量"):
        parse_strategy_metadata(code)


def test_metadata_invalid_symbols_raises():
    code = """
CONTRACT_VERSION = 2
SYMBOLS = [1, 2]

def generate_signals(data):
    return data
"""
    with pytest.raises(ValueError, match="字符串列表"):
        parse_strategy_metadata(code)


def test_metadata_annotated_assignment_supported():
    # LLM 常输出带类型注解的常量声明，必须与普通赋值同样解析，
    # 不能静默跳过回退 v1（那会把 v2 策略错误路由进 v1 引擎）
    code = """
CONTRACT_VERSION: int = 2
SYMBOLS: list = ["BTCUSDT", "ETHUSDT"]

def generate_signals(data):
    return data
"""
    meta = parse_strategy_metadata(code)
    assert meta["contract_version"] == 2
    assert meta["symbols"] == ["BTCUSDT", "ETHUSDT"]


def test_metadata_bare_annotation_raises():
    # 只有注解没有值：声明了名字却没赋值，必须报错而不是静默忽略
    code = """
CONTRACT_VERSION: int

def generate_signals(df):
    return df
"""
    with pytest.raises(ValueError, match="整数常量"):
        parse_strategy_metadata(code)


def test_metadata_augassign_raises():
    code = """
CONTRACT_VERSION = 1
CONTRACT_VERSION += 1

def generate_signals(df):
    return df
"""
    with pytest.raises(ValueError, match="整数常量"):
        parse_strategy_metadata(code)


def test_metadata_unpack_assignment_raises():
    code = """
CONTRACT_VERSION, SYMBOLS = 2, ["BTCUSDT"]

def generate_signals(data):
    return data
"""
    with pytest.raises(ValueError):
        parse_strategy_metadata(code)


def test_metadata_nested_binding_raises():
    # 常量包在 try/if 等语句块内静态解析不到：必须报错而不是静默回退 v1
    code = """
try:
    CONTRACT_VERSION = 2
    SYMBOLS = ["BTCUSDT"]
except NameError:
    pass

def generate_signals(data):
    return data
"""
    with pytest.raises(ValueError, match="顶层"):
        parse_strategy_metadata(code)


def test_metadata_local_variable_not_flagged():
    # generate_signals 函数体内部的同名局部变量不受顶层声明规则限制
    code = """
CONTRACT_VERSION = 1

def generate_signals(df):
    SYMBOLS = ["BTCUSDT"]  # 局部变量，合法
    df["target_position"] = 0
    return df
"""
    meta = parse_strategy_metadata(code)
    assert meta["contract_version"] == 1


def test_metadata_starred_unpack_raises():
    code = """
first, *SYMBOLS = 1, "BTCUSDT", "ETHUSDT"

def generate_signals(data):
    return data
"""
    with pytest.raises(ValueError):
        parse_strategy_metadata(code)


def test_validate_symbols_format():
    validate_symbols_format(["BTCUSDT", "ETHUSDT"])

    with pytest.raises(ValueError, match="USDT"):
        validate_symbols_format(["btc"])

    with pytest.raises(ValueError, match="USDT"):
        validate_symbols_format(["BTC"])

    with pytest.raises(ValueError, match="USDT"):
        validate_symbols_format(["USDT"])


# =========================================================
# 版本与 SYMBOLS 组合规则（生成校验与回测路由共用的单源）
# =========================================================

def test_validate_metadata_accepts_valid_combinations():
    validate_strategy_metadata({"contract_version": 1, "symbols": None})
    validate_strategy_metadata({"contract_version": 1, "symbols": ["ETHUSDT"]})
    validate_strategy_metadata({"contract_version": 2, "symbols": ["BTCUSDT", "ETHUSDT"]})


def test_validate_metadata_unknown_version_raises():
    with pytest.raises(ValueError, match="未知契约版本"):
        validate_strategy_metadata({"contract_version": 3, "symbols": None})


def test_validate_metadata_v2_requires_symbols():
    with pytest.raises(ValueError, match="SYMBOLS"):
        validate_strategy_metadata({"contract_version": 2, "symbols": None})


def test_validate_metadata_v1_multi_symbol_rejected():
    # v1 多标的静默截断会无声丢掉对冲腿，必须拒绝并指引升级 v2
    with pytest.raises(ValueError, match="CONTRACT_VERSION = 2"):
        validate_strategy_metadata(
            {"contract_version": 1, "symbols": ["BTCUSDT", "ETHUSDT"]}
        )


# =========================================================
# 审计留档
# =========================================================

def test_audit_save_writes_file(tmp_path):
    path = save_strategy_code_audit(VALID_CODE, output_dir=str(tmp_path))

    saved = tmp_path / path.split("\\")[-1].split("/")[-1]
    assert saved.exists()
    assert saved.read_text(encoding="utf-8") == VALID_CODE
    assert saved.name.startswith("strategy_")


def test_audit_save_never_overwrites(tmp_path):
    path_a = save_strategy_code_audit(VALID_CODE, output_dir=str(tmp_path))
    path_b = save_strategy_code_audit(VALID_CODE, output_dir=str(tmp_path))
    assert path_a != path_b
    assert len(list(tmp_path.glob("strategy_*.py"))) == 2
