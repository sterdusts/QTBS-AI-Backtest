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
# exec 沙箱：最小化 __builtins__ 白名单（修复高危 #4）
# =========================================================
# 背景：module.__dict__ 缺 '__builtins__' 时 CPython 在 exec 期自动注入完整
# 内置命名空间（open/getattr/__import__ 全可用），一行属性链即任意文件写。
# 修复后 exec 前注入最小纯计算白名单 + 受限 __import__（仅 pandas/numpy），
# 并在 AST 拒绝危险 dunder 属性链。下列测试钉死「正常策略仍能跑、三类逃逸
# 全被拦」。注意：净化作用域是策略源码本身——pandas/numpy 内部 import os
# 走的是各自模块的真实 builtins，不受影响，所以下面正例必须真能加载执行。


# 典型策略：自身 import pandas/numpy，并用到一批纯计算内置（len/range/
# min/max/abs/round/enumerate/zip/sum/sorted/float/int/list/dict/isinstance/
# print/ValueError）。修复后这些必须仍可用，否则会误杀合规历史策略。
TYPICAL_STRATEGY_CODE = """
import pandas as pd
import numpy as np

CONTRACT_VERSION = 1

PERIODS = [int(p) for p in range(5, 21, 5)]


def generate_signals(df):
    df = df.copy()

    windows = sorted(set(PERIODS))
    longest = max(windows)
    shortest = min(windows)

    ma_fast = df["close"].rolling(window=shortest).mean()
    ma_slow = df["close"].rolling(window=longest).mean()

    rounded = [round(float(x), 2) for x in df["close"].tolist()]
    total = sum(rounded)
    assert isinstance(total, float)

    for i, w in enumerate(windows):
        if not isinstance(w, int):
            raise ValueError("window 必须是整数")

    signal = np.where(ma_fast > ma_slow, 1, 0)
    df["target_position"] = pd.Series(signal, index=df.index).fillna(0).astype(int)

    info = dict(zip(["fast", "slow"], [shortest, longest]))
    print("windows", info, abs(-1), len(df))
    return df
"""


def test_typical_strategy_with_imports_and_builtins_still_runs():
    # 正例：自身 import + 一批纯计算内置的真实风格策略仍能加载并调用成功。
    func = load_strategy_func_from_code(TYPICAL_STRATEGY_CODE)
    result = func(make_df())
    assert "target_position" in result.columns
    assert result["target_position"].notna().all()


def test_sandbox_has_no_open_or_dangerous_builtins():
    # 注入的 __builtins__ 必须不含 open/__import__-放行 os 之外的危险内置；
    # 直接检查策略函数闭包看到的 __builtins__ 是被最小化过的字典。
    func = load_strategy_func_from_code(VALID_CODE)
    sandbox_builtins = func.__globals__["__builtins__"]

    assert isinstance(sandbox_builtins, dict)  # 不是完整内置模块
    for forbidden in ("open", "eval", "exec", "compile", "getattr", "setattr",
                      "globals", "locals", "vars", "dir", "input",
                      "__build_class__", "breakpoint", "memoryview"):
        assert forbidden not in sandbox_builtins, f"{forbidden} 不应出现在沙箱内置中"

    # 受限 __import__ 在场（策略靠它 import pandas），但不是原生 __import__
    import builtins as _b
    assert "__import__" in sandbox_builtins
    assert sandbox_builtins["__import__"] is not _b.__import__


def test_escape_via_builtins_open_blocked():
    # 反例 1：generate_signals.__globals__['__builtins__']['open'] 取回。
    # __globals__ / __builtins__ 是被 AST 拒绝的危险 dunder，加载即被拦；
    # 即便绕过，沙箱 __builtins__ 里也没有 open。
    code = """
import pandas as pd

def generate_signals(df):
    f = generate_signals.__globals__['__builtins__']['open']
    f('escape_proof.txt', 'w').write('pwned')
    return df
"""
    with pytest.raises(ValueError, match="危险属性|危险名称"):
        load_strategy_func_from_code(code)


def test_escape_via_subclasses_blocked():
    # 反例 2：(1).__class__...__subclasses__() 回取 os。
    # 不依赖 import，靠属性链回取宿主对象——AST 危险 dunder 检查直接拦下。
    code = """
import pandas as pd

def generate_signals(df):
    obj = (1).__class__.__base__.__subclasses__()
    return df
"""
    with pytest.raises(ValueError, match="危险属性|危险名称"):
        load_strategy_func_from_code(code)


def test_escape_via_import_os_blocked():
    # 反例 3：__import__('os')。FORBIDDEN_KEYWORDS 已拦字符串形态；
    # 即便构造出调用，受限 __import__ 也会对 os 抛 ImportError（仅放行
    # pandas/numpy）。这里验证静态校验先一步拦下。
    code = """
import pandas as pd

def generate_signals(df):
    __import__('os').system('echo pwned')
    return df
"""
    with pytest.raises(ValueError, match="禁止|危险"):
        load_strategy_func_from_code(code)


def test_escape_via_pandas_numpy_io_blocked():
    # 反例 4（round-9）：pandas/numpy 自带 I/O 方法绕过沙箱——它们既非 dunder、
    # 也不含 open(/eval( 子串。逐个验证 AST 按方法名静态拒绝（任意文件读写 /
    # read_pickle 反序列化 RCE / read_csv(url) SSRF）。
    io_snippets = [
        "df.to_csv('/tmp/pwned.csv')",
        "pd.read_csv('/etc/passwd')",
        "pd.read_pickle('payload.pkl')",
        "df.to_pickle('x.pkl')",
        "pd.read_parquet('x.parquet')",
        "pd.read_csv('http://evil.example/x')",
        "np.savetxt('x.txt', df.values)",
        "np.load('x.npy')",
        "df.values.tofile('x.bin')",
    ]
    for snippet in io_snippets:
        code = (
            "import pandas as pd\n"
            "import numpy as np\n\n"
            "def generate_signals(df):\n"
            f"    {snippet}\n"
            "    return df\n"
        )
        with pytest.raises(ValueError, match="文件/网络 I/O"):
            load_strategy_func_from_code(code)


def test_legit_pandas_numpy_compute_not_blocked_by_io_guard():
    # 正例：常用纯计算方法（to_numpy/to_dict/to_frame/rolling/ewm/shift 等）
    # 不被 I/O 守卫误杀，典型策略仍可加载执行。
    code = """
import pandas as pd
import numpy as np

def generate_signals(df):
    arr = df['close'].to_numpy()
    ma = df['close'].rolling(3).mean().shift(1).fillna(0)
    d = df.to_dict()
    df['target_position'] = (df['close'] > ma).astype(int)
    return df
"""
    func = load_strategy_func_from_code(code)
    assert callable(func)


def test_escape_via_pandas_submodule_module_attr_blocked():
    # round-10 反例：pandas/numpy import 时把 stdlib 模块挂为子模块属性，
    # pd.compat.os / pd.io.common.os / np.f2py.subprocess 是【真实 stdlib 模块】，
    # 可经 pd.compat.os.system(...) 完整 RCE（实测）。模块属性名静态拒绝。
    for snippet in ["pd.compat.os.getcwd()", "pd.io.common", "pd.compat.subprocess", "np.f2py"]:
        code = (
            "import pandas as pd\nimport numpy as np\n\n"
            "def generate_signals(df):\n"
            f"    y = {snippet}\n    return df\n"
        )
        with pytest.raises(ValueError, match="模块属性"):
            load_strategy_func_from_code(code)


def test_escape_via_df_query_blocked():
    # round-10 反例：df.query 字符串表达式引擎可在字符串内执行属性链至 os.system
    # （实测 RCE）；df.eval 已被 FORBIDDEN_KEYWORDS 的 'eval(' 子串拦。query 静态拒绝。
    for snippet in ['df.query("close > open")', 'df.query("a.__class__.__mro__")']:
        code = (
            "import pandas as pd\n\n"
            "def generate_signals(df):\n"
            f"    {snippet}\n    return df\n"
        )
        with pytest.raises(ValueError, match="query|表达式"):
            load_strategy_func_from_code(code)


def test_import_via_builtins_dict_rejected():
    # 经 __builtins__['__import__'] 取回导入器是又一条逃逸向量。
    # 这里 __builtins__ 与 __import__ 都命中 FORBIDDEN_KEYWORDS 子串黑名单，
    # 在静态校验最先一层即被拒（早于 AST 危险 dunder 检查），加载阶段就拦下。
    # 运行期受限 __import__ 本身由 test_restricted_import_rejects_os_allows_pandas 直接验证。
    code = """
import pandas as pd

def generate_signals(df):
    name = 'o' + 's'
    mod = __builtins__['__import__'](name)
    return df
"""
    with pytest.raises(ValueError, match="禁止|危险"):
        load_strategy_func_from_code(code)


def test_restricted_import_rejects_os_allows_pandas():
    # 直接对受限 __import__ 做单元断言：放行 pandas/numpy，拒绝 os。
    from module.Strategy.strategy_loader import _make_restricted_import

    restricted = _make_restricted_import()

    pandas_mod = restricted("pandas")
    assert pandas_mod is not None

    # pandas/numpy 子模块（与 AST 白名单一致）放行
    restricted("numpy.linalg", fromlist=["norm"])

    with pytest.raises(ImportError):
        restricted("os")
    with pytest.raises(ImportError):
        restricted("subprocess")
    with pytest.raises(ImportError):
        restricted("importlib")


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
