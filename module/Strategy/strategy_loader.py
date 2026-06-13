"""
策略代码安全校验与加载。

设计原则（契约见项目根目录 STRATEGY_CONTRACT.md 第 6 节）：
1. 策略代码从内存编译加载，不经过任何共享文件路径。
   并发回测互不干扰，为未来 Web 多用户场景做准备。
2. 实际参与回测的代码以时间戳文件留档（仅审计追溯，永不加载执行）。
3. 加载前强制安全校验：白名单 import + 黑名单关键字 + ast.walk 全树遍历。
"""

import ast
import os
import types
import uuid

from module.modules.file_naming import build_timestamped_filename


# 策略代码审计留档目录（仅留档，不参与执行）
STRATEGY_AUDIT_DIR = os.path.join("Past_data", "strategy_code")


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
                if alias.name.split(".")[0] not in ("pandas", "numpy"):
                    raise ValueError(f"禁止导入模块: {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            root_module = (node.module or "").split(".")[0]
            if root_module not in ("pandas", "numpy"):
                raise ValueError(f"禁止 from {node.module} import ...")

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
    meta_names = {"CONTRACT_VERSION", "SYMBOLS"}
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

    return {
        "contract_version": contract_version,
        "symbols": symbols,
    }


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

    compiled = compile(code, filename=f"<{module_name}>", mode="exec")
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
