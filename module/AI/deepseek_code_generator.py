# module/AI/deepseek_code_generator.py
#
# 本文件 prompt 中的策略规则源自项目根目录 STRATEGY_CONTRACT.md（契约 v1）。
# 修改任何规则必须与契约文档、tests/ 金样例测试保持同步。

import os
import re
from openai import OpenAI

from module.Strategy.strategy_loader import (
    parse_strategy_metadata,
    validate_strategy_code,
    validate_strategy_metadata,
)


# 只拦截真正可执行的违规模式（带引号的周期字面量、resample 调用）。
# 不要加裸词（如 "timeframe"）：会对注释做子串匹配，
# 英文注释写 "works on any timeframe" 就会误杀合规代码且报错原因误导。
FORBIDDEN_TIMEFRAME_TOKENS = [
    '"1m"', "'1m'",
    '"5m"', "'5m'",
    '"15m"', "'15m'",
    '"1h"', "'1h'",
    '"4h"', "'4h'",
    '"1d"', "'1d'",
    '"1T"', "'1T'",
    '"5T"', "'5T'",
    '"15T"', "'15T'",
    '"1H"', "'1H'",
    '"4H"', "'4H'",
    '"1D"', "'1D'",
    "resample(",
    ".resample(",
]


def clean_python_code(content: str) -> str:
    """
    清理 DeepSeek 返回内容：
    - 去掉 ```python
    - 去掉 ```
    - 去掉前后空白
    """

    if not content:
        raise ValueError("DeepSeek 返回空内容，请重试。")

    content = content.strip()

    # 去掉 markdown 代码块（兼容 ```python / ```py / ```Python / ``` 等写法）
    content = re.sub(r"^```[a-zA-Z0-9]*\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    return content.strip()


def validate_generated_code(code: str) -> None:
    """
    对 DeepSeek 生成的策略代码做基础结构检查（按契约版本分流）。

    这里不是完整安全沙箱，真正执行前由 strategy_loader.py 做安全检查。
    这个函数主要拦截：
    1. 没有 generate_signals
    2. v1 没有 target_position / v2 没有 SYMBOLS
    3. 在策略代码里写死周期或重采样 K 线
    """

    # generate_signals 存在性 / import 白名单 / 危险调用与回测加载
    # 共用同一实现（AST 级，而非脆弱的子串匹配），
    # 保证「生成放行 ⇔ 回测接受」严格一致
    validate_strategy_code(code)

    metadata = parse_strategy_metadata(code)

    # 版本与 SYMBOLS 组合规则单源在 strategy_loader.validate_strategy_metadata，
    # 与回测路由共用，保证「生成放行 ⇔ 回测接受」严格一致
    validate_strategy_metadata(metadata)

    if metadata["contract_version"] == 1 and "target_position" not in code:
        raise ValueError("DeepSeek 返回的契约 v1 代码中没有 target_position 字段。")

    lower_code = code.lower()

    for token in FORBIDDEN_TIMEFRAME_TOKENS:
        if token.lower() in lower_code:
            raise ValueError(
                f"DeepSeek 返回的代码中出现了不允许的周期写死或重采样内容：{token}。"
                "请重新生成策略代码。策略代码只能基于传入的数据计算，不能写死 1m/5m/15m/1h/4h/1d，也不能 resample。"
            )


def build_strategy_code_prompt(
    user_text: str,
    market: str = "加密货币",
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    language: str = "中文",
    allow_short: bool = False,
    initial_cash: float | None = None,
    fee_rate_percent: float | None = None,
    slippage_percent: float | None = None,
    available_symbols: list | None = None,
) -> str:
    """
    构造给 DeepSeek 的策略代码生成 Prompt（契约 v1 + v2，见 STRATEGY_CONTRACT.md）。

    注意：
    - market / symbol / timeframe 可以给 AI，作为策略上下文。
    - timeframe 只能作为上下文，不允许 AI 写进代码。
    - 手续费 / 滑点可以作为环境说明，但不允许 AI 自己写成交逻辑。
    - AI 只能生成 generate_signals，由代码中的 CONTRACT_VERSION 常量声明契约版本。
    """

    allow_short_text = (
        "允许做空。策略可以使用 target_position = -1。"
        if allow_short
        else "默认不允许做空。除非用户策略明确要求做空，否则只能使用 target_position = 1 或 0。"
    )

    env_text = f"""
当前回测环境：
- 市场类型：{market}
- 交易标的：{symbol}
- 当前 webUI 选择的 K线周期：{timeframe}
- 用户语言：{language}
- 初始资金：{initial_cash if initial_cash is not None else "由回测框架默认处理"}
- 手续费率：{fee_rate_percent if fee_rate_percent is not None else 0}%
- 滑点：{slippage_percent if slippage_percent is not None else 0}%

重要说明：
1. 手续费、滑点、初始资金由回测框架处理。
2. 你不要在策略代码中计算手续费、滑点、收益率、净值曲线或成交价格。
3. 当前 K线周期只用于理解用户策略背景，不允许写进代码。
4. 系统会根据 webUI 选择的周期，提前把对应周期的 K线 DataFrame 传入 generate_signals(df)。
"""

    timeframe_rule = """
时间周期规则，必须严格遵守：

1. 不要在代码中写死任何时间周期。
2. 不要在代码中出现 "1m"、"5m"、"15m"、"1h"、"4h"、"1d" 等字符串。
3. 不要在代码中定义 timeframe 变量。
4. 不要根据 timeframe 判断不同逻辑。
5. 不要使用 df.resample(...) 或任何重采样逻辑。
6. 不要读取其他周期数据。
7. 不要创建多周期 K线。
8. generate_signals(df) 必须只基于系统传入的 df 计算。
9. 如果用户策略描述里出现“使用 4小时K线 / 1小时K线 / 日线”等说法，只把它理解为策略背景，不要写入代码。
10. 策略代码应该对任意周期的 df 都能运行。
"""

    available_text = (
        "、".join(available_symbols)
        if available_symbols
        else "BTCUSDT、ETHUSDT、SOLUSDT 等"
    )

    prompt = f"""
你是一个专业量化策略代码生成器。

你的任务：
根据用户的自然语言策略，生成一个 Python 策略函数 generate_signals。

系统支持两种契约，你必须根据用户策略自动选择一种，并在代码开头用模块常量声明：

CONTRACT_VERSION = 1   （单标的离散信号）
CONTRACT_VERSION = 2   （多标的目标权重）

契约选择规则：

1. 默认使用契约 v1：用户策略只涉及一个交易标的、只有 满仓做多/空仓/满仓做空 三种状态。
2. 只有当用户策略明确涉及以下情形时才使用契约 v2：
   - 多个交易标的（例如 BTC/ETH 对冲、配对交易、轮动、组合、多因子选币）
   - 动态仓位 / 半仓 / 分批建仓 / 分批减仓（仓位需要按比例连续调整）
3. 不要因为"可以做得更复杂"而升级契约：用户没要求的能力不要使用。

通用规则（两种契约都必须遵守）：

1. 只能输出 Python 代码，不要 markdown、解释、JSON。
2. 不要生成完整回测系统、可视化、下单代码。
3. 不要读取本地文件、调用网络 API、调用交易所接口。
4. 不要计算最终收益、手续费、滑点（回测框架负责）。
5. 不要使用未来数据。
6. 只能使用 pandas 和 numpy。
7. 代码开头必须包含：
   import pandas as pd
   import numpy as np

{timeframe_rule}

重要时序规则（两种契约相同）：

1. 所有信号默认在当前 K 线 close 后确认。
2. 回测框架会在下一根 K 线 open 执行。
3. 判断上穿 / 下穿必须使用 shift(1)。
4. 禁止使用 shift(-1)。
5. rolling / ewm 指标只能基于当前和历史数据。

=========================================================
契约 v1：单标的离散信号
=========================================================

函数签名：

def generate_signals(df: pd.DataFrame) -> pd.DataFrame:

输入 df 至少包含字段：open、high、low、close、volume。
你可以新增指标列（ma20、ema12、rsi14、macd、boll_upper、atr14 等）。

你必须新增或覆盖字段 target_position：

1 = 应该持有多仓
0 = 应该空仓
-1 = 应该持有空仓

{allow_short_text}

策略输出规则：

1. 用户只描述做多策略：开多条件 target_position = 1，平仓条件 target_position = 0。
2. 用户明确描述做空或多空切换时才使用 -1。
3. 用户没说止损止盈，不要自己添加；没说做空，不要做空；没说加仓，不要加仓。
4. 信号没有变化时，target_position 延续上一根 K 线的状态。
5. 最后必须处理 NaN，并确保 target_position 中只包含 -1、0、1。
6. 如果用户明确点名了交易标的（例如"做多ETH"），额外声明 SYMBOLS = ["ETHUSDT"]；
   用户没点名标的就不要声明 SYMBOLS。

契约 v1 推荐结构：

import pandas as pd
import numpy as np

CONTRACT_VERSION = 1


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 计算指标

    # 生成条件

    df["target_position"] = np.nan

    # 根据条件设置 target_position

    df["target_position"] = df["target_position"].ffill().fillna(0)
    df["target_position"] = df["target_position"].astype(int)

    return df

=========================================================
契约 v2：多标的目标权重
=========================================================

函数签名：

def generate_signals(data: dict) -> pd.DataFrame:

输入 data 是 {{标的: K线DataFrame}}，所有 DataFrame 的索引完全相同（已对齐）。
例如 data["BTCUSDT"]["close"] 是 BTC 的收盘价序列。

返回目标权重 DataFrame：

- index = 与输入 K 线相同的时间索引
- columns = SYMBOLS 中的标的
- 值 = 目标权重：正数 = 做多，负数 = 做空，0 = 空仓
- 权重表示「该标的占总资金的目标比例」，总敞口（各标的 |权重| 之和）不要超过 1
- 负权重只有在用户明确要求做空 / 对冲时才能使用

必须声明两个模块常量：

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]   # 策略涉及的所有标的，USDT 交易对格式

标的规则：

1. 用户点名了标的就用用户点名的。
2. 用户要求"轮动 / 选币 / 多因子"但没点名标的时，从本地可用标的中选择：{available_text}
3. 用户点名了本地没有的 USDT 币种也可以使用（系统会自动拉取数据）。

数据规则：

1. 某些标的上市较晚，上市前的 K 线为 NaN。
2. 指标计算遇到 NaN 会自然产生 NaN，最后把 NaN 权重统一填成 0 即可。
3. 不要试图填充或伪造价格数据。

不需要你处理的事情（引擎负责）：

1. 再平衡执行、手续费、滑点、杠杆、强平。
2. 你只描述「每根 K 线收盘后各标的应该是多少权重」。

契约 v2 推荐结构：

import pandas as pd
import numpy as np

CONTRACT_VERSION = 2
SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def generate_signals(data: dict) -> pd.DataFrame:
    btc = data["BTCUSDT"]
    eth = data["ETHUSDT"]

    index = btc.index
    weights = pd.DataFrame(0.0, index=index, columns=SYMBOLS)

    # 计算指标、生成条件、设置各标的权重

    weights = weights.fillna(0.0)
    return weights

=========================================================

通用收尾要求：

1. 把策略原理注释在代码里，方便使用者核对是否符合其描述。
2. 注释语言跟随用户输入语言（用户用{language}就用{language}注释）。

{env_text}

用户策略描述如下：

{user_text}
"""

    return prompt.strip()


def generate_strategy_code_with_deepseek(
    user_text: str,
    market: str = "加密货币",
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    language: str = "中文",
    allow_short: bool = False,
    initial_cash: float | None = None,
    fee_rate_percent: float | None = None,
    slippage_percent: float | None = None,
    available_symbols: list | None = None,
) -> str:
    """
    调用 DeepSeek，把自然语言策略转换成 generate_signals 策略代码。
    AI 根据策略内容自动选择契约 v1（单标的）或 v2（多标的权重）。

    返回：
        Python 代码字符串
    """

    api_key = os.environ.get("DEEPSEEK_API_KEY")

    if not api_key:
        raise ValueError(
            "没有读取到 DEEPSEEK_API_KEY，请在项目根目录的 .env 文件中设置 DEEPSEEK_API_KEY。"
        )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )

    system_prompt = """
你是一个量化策略代码生成器。
你只负责生成策略信号函数 generate_signals。
你不能生成完整回测系统。
你不能生成 JSON。
你不能解释。
你只能输出 Python 代码。

硬性规则：
1. 代码开头必须声明模块常量 CONTRACT_VERSION（1 = 单标的离散信号，2 = 多标的目标权重）。
2. 契约 v2 必须同时声明 SYMBOLS = ["BTCUSDT", ...]。
3. 不能在策略代码中写死任何 K线周期。
4. 不能在策略代码中写 "1m"、"5m"、"15m"、"1h"、"4h"、"1d"。
5. 不能使用 resample。
6. 不能读取其他周期数据。
7. 只能基于传入的数据计算信号（v1 输出 target_position，v2 输出目标权重 DataFrame）。
8. 把策略详情注释在代码里面，要让使用者知道这个策略代码原理是什么，并方便让使用者比较与其描述是否相符。注释语言使用使用者输入语言注释，比如使用者使用中文输入，代码注释则使用中文，如果输入语言是英文则使用英文注释代码，其余语言同上。
"""

    user_prompt = build_strategy_code_prompt(
        user_text=user_text,
        market=market,
        symbol=symbol,
        timeframe=timeframe,
        language=language,
        allow_short=allow_short,
        initial_cash=initial_cash,
        fee_rate_percent=fee_rate_percent,
        slippage_percent=slippage_percent,
        available_symbols=available_symbols,
    )

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.1,
        max_tokens=4000,
        extra_body={
            "thinking": {"type": "disabled"}
        }
    )

    content = response.choices[0].message.content

    code = clean_python_code(content)

    validate_generated_code(code)

    return code