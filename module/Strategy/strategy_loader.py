"""
策略代码安全校验与加载。

设计原则（契约见项目根目录 STRATEGY_CONTRACT.md 第 6 节）：
1. 策略代码从内存编译加载，不经过任何共享文件路径。
   并发回测互不干扰，为未来 Web 多用户场景做准备。
2. 实际参与回测的代码以时间戳文件留档（仅审计追溯，永不加载执行）。
3. 加载前强制安全校验：白名单 import + 黑名单关键字 + ast.walk 全树遍历。
"""

import ast
import builtins
import os
import sys
import threading
import types
import uuid

from module.modules.file_naming import build_timestamped_filename


# =========================================================
# 运行期沙箱守卫（sys.addaudithook）
# =========================================================
# AST 黑名单是【加载期、按 API 名】的防御，被绕过四轮（open/import → dunder 链 →
# pandas/numpy I/O 方法 → 模块属性回取 → DataSource/ExcelFile…），属务实但漏。
# 这里加一层【执行期、按 OS 操作】的根因补强：在策略 exec/调用期间，于真正触发
# 文件打开 / socket / 子进程 / 原生代码时拦截——无论 Python 层用何种花招到达
# （DataSource、read_csv、裸 open、dunder 链回取 os.system 都殊途同归到这些审计事件）。
# 审计事件集小而稳定（不随 pandas 版本增长），纯函数策略零触发 ⇒ 近零误伤。
# 钩子全局且不可卸载，故用线程局部深度计数，仅在策略加载/执行窗口内生效。
_sbx_local = threading.local()

# 懒加载允许：import 子模块会读 .py/.pyc/.so/.pyd 及包内数据文件，均在这些目录内。
# 用户数据/密钥文件（如 C:\Users\...\secret.txt）不在其中 ⇒ 读取被拒。
_PYLIB_DIRS = tuple(sorted({
    os.path.normcase(os.path.abspath(p))
    for p in (sys.prefix, sys.base_prefix, *sys.path) if p
}, key=len, reverse=True))

_BLOCKED_AUDIT_PREFIXES = (
    "socket.", "subprocess.", "ctypes.", "winreg.", "urllib.", "ftplib.",
    "smtplib.", "http.client", "os.exec", "os.spawn", "os.fork",
)
_BLOCKED_AUDIT_EVENTS = frozenset({
    "os.system", "os.startfile", "os.posix_spawn", "os.kill", "os.putenv",
    "os.unsetenv", "os.removexattr", "os.setxattr", "shutil.copyfile",
})


def _sbx_active():
    return getattr(_sbx_local, "depth", 0) > 0


def _sandbox_audit_hook(event, args):
    if not _sbx_active():
        return
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else None
        m = "" if mode is None else str(mode)
        if any(c in m for c in ("w", "a", "x", "+")):
            raise PermissionError(f"策略沙箱：执行期禁止写文件 open({path!r}, {mode!r})")
        # 读：仅放行 Python/库目录内文件（懒加载 import）；其余（用户数据/密钥）一律拒。
        # 用 normcase（纯字符串、不触发审计事件）避免钩子内 abspath 再入。
        if isinstance(path, str):
            low = os.path.normcase(path)
            if any(low.startswith(d) for d in _PYLIB_DIRS):
                return
        raise PermissionError(
            f"策略沙箱：执行期禁止读取文件 open({path!r})（纯函数不得读数据/密钥文件）")
    if event in _BLOCKED_AUDIT_EVENTS or event.startswith(_BLOCKED_AUDIT_PREFIXES):
        raise PermissionError(f"策略沙箱：执行期禁止操作 {event}")


sys.addaudithook(_sandbox_audit_hook)


class sandbox_guard:
    """上下文管理器：在策略 exec/调用窗口内开启运行期审计守卫（线程局部、可重入）。"""

    def __enter__(self):
        _sbx_local.depth = getattr(_sbx_local, "depth", 0) + 1
        return self

    def __exit__(self, *exc):
        _sbx_local.depth = max(0, getattr(_sbx_local, "depth", 1) - 1)
        return False


# 策略代码审计留档目录（仅留档，不参与执行）
STRATEGY_AUDIT_DIR = os.path.join("Past_data", "strategy_code")


# AST import 白名单的唯一出处：validate_strategy_code 的静态检查与
# 运行期受限 __import__（_make_restricted_import）共用同一集合，
# 两侧不一致会出现「静态放行、运行期拒绝」之类的不对称行为。
ALLOWED_IMPORT_ROOTS = ("pandas", "numpy")


# exec 沙箱：策略以最小纯计算内置命名空间执行。
#
# 背景（修复高危 #4）：types.ModuleType.__dict__ 没有 '__builtins__' 键时，
# CPython 在 exec 期会自动注入【完整】内置命名空间（open/getattr/__import__
# 全部可用）。仅靠 FORBIDDEN_KEYWORDS 子串匹配（可被 'o'+'pen' 拼接绕过）
# 与 ast.Import/ImportFrom 白名单（不审属性链与 __import__() 调用）拦不住，
# 通过校验的策略可经 generate_signals.__globals__['__builtins__']['open']
# 或 (1).__class__...__subclasses__() 回取 os 在 Past_data 写文件，击穿契约
# §1/§6「纯函数不读写文件」承诺。
#
# 对策：exec 前显式把 module.__dict__['__builtins__'] 设为下面这份白名单，
# 阻断 CPython 的完整注入。只放行无副作用、不能回取宿主对象的纯计算内置；
# open/__import__/getattr/eval/exec/compile/vars/dir/globals/locals/input/
# __build_class__/memoryview/breakpoint 等一律不放行。策略靠自身
# `import pandas as pd` 取得依赖，因此唯一放行的「带副作用」内置是一个
# 受限 __import__（见 _make_restricted_import），仅允许 ALLOWED_IMPORT_ROOTS。
_SAFE_BUILTIN_NAMES = (
    # 数值/聚合
    "abs", "round", "min", "max", "sum", "pow", "divmod",
    # 序列/迭代
    "len", "range", "enumerate", "zip", "sorted", "reversed",
    "map", "filter", "any", "all", "iter", "next", "slice",
    # 类型构造与判定
    "bool", "int", "float", "complex", "str", "bytes", "bytearray",
    "list", "tuple", "dict", "set", "frozenset",
    "isinstance", "issubclass", "callable", "hasattr",
    # 表示/格式化/进制
    "repr", "format", "hex", "oct", "bin", "ord", "chr", "ascii",
    # 调试输出（写 stdout，不触及文件系统）
    "print",
    # 常量
    "True", "False", "None", "NotImplemented", "Ellipsis",
    # 异常体系（策略与引擎需要 raise / 捕获）
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "RuntimeError", "ArithmeticError", "ZeroDivisionError",
    "OverflowError", "FloatingPointError", "AttributeError", "NameError",
    "StopIteration", "AssertionError", "NotImplementedError",
    "Warning", "UserWarning", "DeprecationWarning",
)


def _make_restricted_import():
    """
    构造受限 __import__：仅允许 ALLOWED_IMPORT_ROOTS 内的模块及其子模块
    （pandas/numpy，与 validate_strategy_code 的 AST 白名单同源），其余一律
    ImportError。策略靠自身 `import pandas as pd` 取依赖，这是白名单里唯一
    带副作用的内置；放任原生 __import__ 会让 `__import__('os')` 直接成立。
    """

    real_import = builtins.__import__

    def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0:
            # 相对导入在受限沙箱里无意义（策略是无包顶层模块），直接拒绝
            raise ImportError("策略代码不允许相对导入")
        root = (name or "").split(".")[0]
        if root not in ALLOWED_IMPORT_ROOTS:
            raise ImportError(
                f"策略代码不允许导入模块: {name}"
                f"（仅允许 {' / '.join(ALLOWED_IMPORT_ROOTS)}）"
            )
        return real_import(name, globals, locals, fromlist, level)

    return restricted_import


def _build_sandbox_builtins() -> dict:
    """
    组装注入 exec 命名空间的最小 __builtins__ 字典（纯计算 + 受限 __import__）。
    取自当前解释器的真实内置对象，避免硬编码引用失效。
    """

    safe = {}
    for bname in _SAFE_BUILTIN_NAMES:
        if hasattr(builtins, bname):
            safe[bname] = getattr(builtins, bname)
    safe["__import__"] = _make_restricted_import()
    return safe


# 即便 __builtins__ 已最小化，属性链仍可绕路回取宿主对象：
# (1).__class__.__base__.__subclasses__() 能在不 import 的情况下枚举到
# os 模块对象。这些 dunder 属性对正常量化策略毫无用处，AST 阶段直接拒绝，
# 把基于属性链的逃逸在静态层就斩断（与受限 __import__ 形成两道独立防线）。
FORBIDDEN_DUNDER_ATTRS = frozenset({
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__import__", "__dict__", "__getattribute__",
    "__code__", "__closure__", "__func__", "__self__", "__module__",
    "__loader__", "__spec__", "__init_subclass__", "__subclasshook__",
    # 序列化/反射类 dunder：可作属性链回取宿主对象的新起点（纵深防御）
    "__reduce__", "__reduce_ex__", "__setstate__", "__getstate__", "__class_getitem__",
})


# pandas/numpy 自带的文件/网络 I/O 方法是绕过整套沙箱的真实越权通道：
# 策略被允许 import pandas/numpy（ALLOWED_IMPORT_ROOTS），而这些 I/O 方法
# 既不是 dunder（FORBIDDEN_DUNDER_ATTRS 不命中）、名字里也不含 open(/eval(
# 之类子串（FORBIDDEN_KEYWORDS 不命中），于是 df.to_csv / pd.read_pickle /
# np.savetxt / pd.read_csv(url) 可任意读写文件、read_pickle 反序列化 RCE、
# read_csv(url) SSRF——直接击穿契约 §1/§6「纯函数、不读写文件、不联网」。
# 正常量化策略的数据由引擎注入、结果通过返回值传出，绝不需要这些 I/O 方法，
# 故在 AST 层按方法名静态拒绝（与 FORBIDDEN_DUNDER_ATTRS 同一道防线）。
# 注：这是单用户本地驾驶舱的务实加固；生产级隔离（独立进程 + 无网络 + 只读 FS）
# 是平台化阶段的目标（getattr/__getattribute__/eval/exec 已不可用，无法绕过属性名静态检查）。
FORBIDDEN_IO_ATTRS = frozenset({
    # pandas 读取（文件/网络）
    "read_csv", "read_table", "read_fwf", "read_pickle", "read_parquet",
    "read_feather", "read_orc", "read_hdf", "read_excel", "read_json",
    "read_html", "read_xml", "read_sql", "read_sql_query", "read_sql_table",
    "read_stata", "read_sas", "read_spss", "read_gbq", "read_clipboard",
    # pandas 写出（文件/网络）
    "to_csv", "to_pickle", "to_parquet", "to_feather", "to_orc", "to_hdf",
    "to_excel", "to_json", "to_html", "to_xml", "to_sql", "to_stata",
    "to_gbq", "to_clipboard",
    "ExcelWriter", "ExcelFile", "HDFStore", "get_handle",
    # numpy 文件 I/O（DataSource 是文件/URL 读取器，np.DataSource 与
    # np.lib.npyio.DataSource 两条路径都拦——审查发现的越权向量）
    "save", "savez", "savez_compressed", "savetxt", "load", "loadtxt",
    "fromfile", "tofile", "genfromtxt", "fromregex", "memmap", "DataSource",
})


# pandas/numpy import 时把自身依赖的标准库模块挂为子模块属性：pd.compat.os /
# pd.io.common.os / numpy.f2py.subprocess 等【就是真实的标准库模块】。属性名
# os/sys/subprocess 既非 dunder、不在 I/O 名单、也不含黑名单子串，可经
# pd.compat.os.system(...) 完整 RCE（round-10 端到端实测写文件）。这些模块/包名
# 作为属性对纯量化策略毫无用途，AST 静态拒绝其属性访问，斩断子模块回取标准库。
FORBIDDEN_MODULE_ATTRS = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "pickle", "marshal", "builtins", "compat", "io", "f2py",
    "npyio",   # np.lib.npyio 子模块（含 DataSource 等 I/O），斩断该路径中段
})


# df.query / df.eval 接收【字符串表达式】，pandas 的 python 表达式引擎会执行其中
# 的属性链调用（df.query("a.__class__...__subclasses__()[i].__init__.__globals__"
# "['os'].system(...)")）实现 RCE（round-10 端到端实测写文件，默认 engine 亦成立）；
# 表达式是 ast.Constant 字符串，AST dunder 防线对其内部失明。纯量化策略用向量化
# 布尔索引即可，绝不需 query/eval，故静态拒绝。
FORBIDDEN_EXPR_ATTRS = frozenset({"query", "eval"})


# 字符串黑名单只保留 AST import 白名单覆盖不到的危险调用。
# "import os" 这类字符串项不要再加回来：所有 import 形式都由
# validate_strategy_code 里的 ast.walk 白名单拦截（且无法用换行/别名绕过），
# 字符串匹配反而会误杀注释（如注释里写 "不 import os"）。
FORBIDDEN_KEYWORDS = [
    "open(",
    "eval(",
    "exec(",
    "__import__",
    "compile(",
    "globals(",
    "locals(",
]


def validate_strategy_code(code: str) -> None:
    """
    对 AI 生成的策略代码做最基础安全检查。
    不是绝对安全沙箱，但足够作为第一层防线。
    """

    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in code:
            raise ValueError(f"策略代码包含禁止内容: {keyword}")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"策略代码语法错误: {e}")

    has_generate_signals = False

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "generate_signals":
                has_generate_signals = True

    # 用 ast.walk 遍历所有节点（包括函数体内部），
    # 防止把 import 藏在函数里绕过顶层检查。
    # 按根模块名判断，允许 pandas/numpy 的子模块。
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"禁止导入模块: {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            root_module = (node.module or "").split(".")[0]
            if root_module not in ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"禁止 from {node.module} import ...")

        # 危险 dunder 属性链（如 (1).__class__.__subclasses__()）能在不 import
        # 的情况下回取 os 等宿主对象，绕开最小化 __builtins__。这些 dunder 对
        # 正常量化策略无用，静态层直接拒绝——属性访问与显式名字两种形态都查。
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_DUNDER_ATTRS:
                raise ValueError(f"策略代码不允许访问危险属性: {node.attr}")
            # pandas/numpy 原生文件/网络 I/O 方法越权（见 FORBIDDEN_IO_ATTRS 说明）
            if node.attr in FORBIDDEN_IO_ATTRS:
                raise ValueError(
                    f"策略代码不允许调用文件/网络 I/O 方法: {node.attr}（策略是纯函数，"
                    "数据由引擎注入、结果经返回值传出，不得读写文件或联网，见契约 §1/§6）"
                )
            # pandas/numpy 子模块属性回取标准库模块（pd.compat.os 等）→ 完整越权 RCE
            if node.attr in FORBIDDEN_MODULE_ATTRS:
                raise ValueError(
                    f"策略代码不允许访问模块属性: {node.attr}（pandas/numpy 子模块会挂载 "
                    "os/sys/subprocess 等标准库模块，可经此完整越权执行命令，见契约 §1/§6）"
                )
            # df.query/df.eval 字符串表达式引擎可在字符串内执行任意属性链调用 → RCE
            if node.attr in FORBIDDEN_EXPR_ATTRS:
                raise ValueError(
                    f"策略代码不允许调用 {node.attr}()（pandas 表达式引擎会执行字符串内的"
                    "任意属性链；纯量化策略请用向量化布尔索引代替 query/eval）"
                )
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_DUNDER_ATTRS:
                raise ValueError(f"策略代码不允许引用危险名称: {node.id}")

    if not has_generate_signals:
        raise ValueError("策略代码必须包含 generate_signals(df) 函数")


def parse_strategy_metadata(code: str) -> dict:
    """
    从策略代码中提取契约元数据（模块级常量，AST 解析，不执行代码）：

    - CONTRACT_VERSION: int，缺省为 1（兼容历史 v1 代码）
    - SYMBOLS: list[str] | None，策略点名的交易标的

    契约 v2 代码必须声明 CONTRACT_VERSION = 2 和 SYMBOLS = [...]，
    见 STRATEGY_CONTRACT.md 第 10 节。
    """

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"策略代码语法错误: {e}")

    contract_version = 1
    symbols = None
    param_space = None  # 契约 v3：策略可选声明的可调参数空间（供稳健性参数扫描）

    # 收集模块顶层对这两个名字的「任何」绑定形式。写错形式必须报错而不是
    # 静默回退 v1（否则 v2 代码会被路由进 v1 引擎，在深处炸出误导性错误），
    # 所以严格性做在「检测到该名字的绑定就必须解析成功」上，而不是只枚举
    # ast.Assign 一种节点（LLM 常输出 CONTRACT_VERSION: int = 2 这类注解赋值）。
    bindings = []  # (name, value_node | None)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.append((target.id, node.value))
                elif isinstance(target, (ast.Tuple, ast.List)):
                    # 解包赋值无法静态对应到单个值 → 不支持的形式
                    for elt in target.elts:
                        # a, *SYMBOLS = ... 的星号元素同样要被识破
                        if isinstance(elt, ast.Starred):
                            elt = elt.value
                        if isinstance(elt, ast.Name):
                            bindings.append((elt.id, None))
        elif isinstance(node, ast.AnnAssign):
            # 带类型注解的赋值等同普通赋值；只有注解没有值视为未赋值
            if isinstance(node.target, ast.Name):
                bindings.append((node.target.id, node.value))
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                bindings.append((node.target.id, None))

    # 顶层之外的绑定（if/try/with 等语句块内）静态解析不到：与其静默
    # 回退 v1（LLM 偶尔会把常量包进防御性 try 块），不如直接指出声明
    # 必须放在模块顶层。函数/类体内部的同名局部变量不在此列。
    meta_names = {"CONTRACT_VERSION", "SYMBOLS", "PARAM_SPACE"}
    bound_top = {name for name, _ in bindings if name in meta_names}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue  # 顶层绑定已收集

        for sub in ast.walk(node):
            if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for target in targets:
                    for elt in ast.walk(target):
                        if (
                            isinstance(elt, ast.Name)
                            and elt.id in meta_names
                            and elt.id not in bound_top
                        ):
                            raise ValueError(
                                f"{elt.id} 必须在模块顶层声明"
                                "（不要包在 if/try 等语句块内），"
                                "否则无法静态解析契约元数据"
                            )

    for name, value in bindings:
        if name == "CONTRACT_VERSION":
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)
            ):
                contract_version = value.value
            else:
                raise ValueError(
                    "CONTRACT_VERSION 必须是整数常量（1 或 2），例如 CONTRACT_VERSION = 2"
                )

        elif name == "SYMBOLS":
            if (
                isinstance(value, (ast.List, ast.Tuple))
                and value.elts
                and all(
                    isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    for elt in value.elts
                )
            ):
                symbols = [elt.value for elt in value.elts]
            else:
                raise ValueError(
                    'SYMBOLS 必须是非空字符串列表，例如 SYMBOLS = ["BTCUSDT", "ETHUSDT"]'
                )

        elif name == "PARAM_SPACE":
            param_space = _parse_param_space(value)

    return {
        "contract_version": contract_version,
        "symbols": symbols,
        "param_space": param_space,
    }


def _ast_number(node):
    """从 AST 节点提取数值常量（int/float，含一元负号），否则返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _parse_param_space(value):
    """解析模块级 PARAM_SPACE = {参数名: [候选值列表]}（契约 v3，AST 静态解析）。
    键必须是合法标识符字符串、值必须是非空数值列表。形式错误必须报错（与
    CONTRACT_VERSION/SYMBOLS 同样严格，避免「声明了但静态解析不到」的静默漂移）。"""
    err = (
        'PARAM_SPACE 必须是 {参数名(字符串): [候选值数值列表]} 的非空字典，'
        '例如 PARAM_SPACE = {"ma_fast": [5, 10, 20], "ma_slow": [30, 60]}'
    )
    if not isinstance(value, ast.Dict) or not value.keys:
        raise ValueError(err)

    out = {}
    for k_node, v_node in zip(value.keys, value.values):
        if not (isinstance(k_node, ast.Constant) and isinstance(k_node.value, str) and k_node.value.isidentifier()):
            raise ValueError(err + "（键必须是合法标识符字符串）")
        if not isinstance(v_node, (ast.List, ast.Tuple)) or not v_node.elts:
            raise ValueError(err + f"（参数 {k_node.value} 的候选值必须是非空列表）")
        vals = [_ast_number(e) for e in v_node.elts]
        if any(v is None for v in vals):
            raise ValueError(err + f"（参数 {k_node.value} 的候选值必须全为数值）")
        out[k_node.value] = vals
    return out


def call_strategy(strategy_func, data, params=None):
    """调用策略函数（契约 v3 兼容分支）：函数签名接收第二个位置参数时传入 params
    （参数化策略 generate_signals(df, params=None)），否则只传 data（历史无参策略
    generate_signals(df)）。params=None ⇒ 参数化策略走自身默认 ⇒ bit-level 退化。"""
    import inspect

    try:
        positional = [
            p for p in inspect.signature(strategy_func).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        accepts_params = len(positional) >= 2
    except (ValueError, TypeError):
        accepts_params = False

    # 运行期沙箱守卫：策略函数体执行期间于 OS 操作层拦文件/网络/子进程（见模块顶部）
    with sandbox_guard():
        if accepts_params:
            return strategy_func(data, params)
        return strategy_func(data)


SUPPORTED_CONTRACT_VERSIONS = (1, 2)


def validate_strategy_metadata(metadata: dict) -> None:
    """
    契约版本与 SYMBOLS 组合规则的唯一出处（生成校验与回测路由共用，
    两侧规则不一致会出现「生成放行、回测拒绝」之类的不对称行为）：

    - 版本必须在 SUPPORTED_CONTRACT_VERSIONS 内
    - v2 必须声明非空 SYMBOLS，且为规范化大写 USDT 交易对
    - v1 至多声明一个标的：多标的静默截断会无声丢掉对冲腿，必须拒绝
    """

    version = metadata["contract_version"]
    symbols = metadata["symbols"]

    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise ValueError(
            f"未知契约版本: CONTRACT_VERSION = {version}，"
            f"只支持 {' 或 '.join(str(v) for v in SUPPORTED_CONTRACT_VERSIONS)}。"
        )

    if version == 2:
        if not symbols:
            raise ValueError(
                '契约 v2 策略代码必须声明非空 SYMBOLS 常量，'
                '例如 SYMBOLS = ["BTCUSDT", "ETHUSDT"]。'
            )
        validate_symbols_format(symbols)

        # 重复标的必须拒绝而不是静默去重：策略代码会按原始 SYMBOLS
        # 建权重列，重复列在引擎 reindex 时炸出无法自查的 pandas 报错
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        if duplicates:
            raise ValueError(
                f"SYMBOLS 不能包含重复标的: {duplicates}，请去重后重新声明。"
            )
    elif symbols and len(symbols) > 1:
        raise ValueError(
            "契约 v1 只支持单标的，但 SYMBOLS 声明了多个标的。"
            "多标的策略请声明 CONTRACT_VERSION = 2。"
        )


def validate_symbols_format(symbols: list) -> None:
    """
    SYMBOLS 必须是规范化的大写 USDT 交易对（如 BTCUSDT）。

    策略代码内部会按 SYMBOLS 原样引用数据面板的键和权重列，
    所以这里强制规范格式，而不是在路由层静默改写。
    """

    invalid = [
        s for s in symbols
        if not (
            isinstance(s, str)
            and s == s.strip().upper()
            and s.endswith("USDT")
            and len(s) > len("USDT")
        )
    ]

    if invalid:
        raise ValueError(
            f"SYMBOLS 必须是大写 USDT 交易对格式（如 BTCUSDT），不符合的项: {invalid}"
        )


def load_strategy_func_from_code(code: str):
    """
    校验并从内存加载策略代码，返回 generate_signals 函数。

    每次加载使用独立的临时模块对象，互不污染，
    不读写任何共享文件路径。
    """

    validate_strategy_code(code)

    module_name = f"generated_strategy_{uuid.uuid4().hex}"
    module = types.ModuleType(module_name)

    # 显式注入最小化 __builtins__，阻断 CPython 在 exec 期自动注入完整内置
    # 命名空间（否则 open/getattr/__import__ 全部可用，详见模块顶部说明）。
    # 必须在 exec 之前设置；每个模块独立一份，并发互不污染。
    module.__dict__["__builtins__"] = _build_sandbox_builtins()

    compiled = compile(code, filename=f"<{module_name}>", mode="exec")
    # 模块级代码也可能内含越权（如顶层 np.DataSource().open(...)）：exec 期也开守卫
    with sandbox_guard():
        exec(compiled, module.__dict__)

    if not hasattr(module, "generate_signals"):
        raise ValueError("策略代码中没有 generate_signals 函数")

    return module.generate_signals


def save_strategy_code_audit(code: str, output_dir: str = STRATEGY_AUDIT_DIR) -> str:
    """
    留档实际参与回测的策略代码，便于追溯与复现。

    文件名带 UTC 时间戳 + 随机后缀，不覆盖历史，并发安全。
    留档文件永远不会被加载执行。
    """

    os.makedirs(output_dir, exist_ok=True)

    file_name = build_timestamped_filename("strategy", ".py")
    file_path = os.path.join(output_dir, file_name)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    return file_path
