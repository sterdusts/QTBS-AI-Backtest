"""
行为审查：生成的策略代码在交给审查 AI / 用户回测之前，
先在确定性的合成数据上把对应引擎真实跑一遍。

目的：
1. 运行时错误在生成阶段就拦截（KeyError、形状错误、契约违例），
   不必等用户点「运行回测」才在引擎深处炸出来
2. 产出行为事实（交易笔数、是否开过仓、是否做空、最大总敞口），
   喂给审查 AI——匹配度审查从「纯文本比对」升级为「看过实际行为」

合成数据完全确定（固定种子、固定形状）：同一份代码的检查结果可复现。
本模块不调用任何外部 API。
"""

import numpy as np
import pandas as pd

from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from module.Strategy.strategy_loader import (
    load_strategy_func_from_code,
    parse_strategy_metadata,
    validate_strategy_metadata,
)


# 三段行情（上涨/下跌/震荡）各约 240 根：MA200 金叉之类的慢策略
# 预热完后仍能看到完整的趋势段；更短则慢指标全程空窗、被误判为零交易
SYNTHETIC_BARS = 720

# 引擎参数全部钉死：行为检查必须确定性可复现，不能隐式继承引擎构造
# 函数默认值（默认值一改，行为事实就漂移、审查 AI 拿到错误结论）。
# rebalance_threshold 仅 v2 引擎接受，在 _build_engine_kwargs 里按版本附加。
_ENGINE_KWARGS = dict(
    initial_cash=1000.0,
    fee_rate=0.0005,
    slippage=0.0,
    leverage=1,
    position_size=1.0,
    enable_liquidation=True,
    maintenance_margin_rate=0.0,
    stop_on_liquidation=True,
    liquidation_fee_rate=0.0,
)


def build_synthetic_kline(seed: int = 7, periods: int = SYNTHETIC_BARS) -> pd.DataFrame:
    """
    确定性合成 K 线：上涨段 → 下跌段 → 震荡段，叠加噪声。

    open 取上一根 close（连续报价），high/low 在 open/close 两侧
    各加随机影线，保证 high ≥ max(open, close) ≥ min(open, close) ≥ low。
    """

    rng = np.random.default_rng(seed)

    seg = periods // 3
    drift = np.concatenate([
        np.full(seg, 0.004),
        np.full(seg, -0.004),
        np.full(periods - 2 * seg, 0.0),
    ])
    noise = rng.normal(0.0, 0.01, periods)
    close = 100.0 * np.cumprod(1.0 + drift + noise)

    open_ = np.empty(periods)
    open_[0] = 100.0
    open_[1:] = close[:-1]

    wick = np.abs(rng.normal(0.0, 0.004, periods)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick

    # 成交量与波动幅度正相关且确定：恒定成交量会让一切量价条件
    # 策略（如「放量突破」）永假，被误判为零信号
    volume = 1000.0 * (1.0 + 10.0 * np.abs(drift + noise))

    idx = pd.date_range("2024-01-01", periods=periods, freq="4h")

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def _shorten(message: str, limit: int = 300) -> str:
    message = str(message).strip()
    return message if len(message) <= limit else message[: limit - 1] + "…"


def run_behavior_check(code: str) -> dict:
    """
    在合成数据上真实运行策略代码，返回 JSON-able 的行为事实。

    任何异常都被捕获并归入 ok=False——行为检查本身永远不抛异常，
    生成流程不应因为检查失败而中断（失败本身就是要上报的事实）。
    """

    result = {
        "ok": False,
        "error": None,
        "contract_version": None,
        "symbols": None,
        "synthetic_bars": SYNTHETIC_BARS,
        "trade_count": 0,
        "fill_count": None,
        "opened_position": False,
        "has_nonzero_signal": False,
        "used_short": False,
        "max_gross_exposure": None,
    }

    try:
        metadata = parse_strategy_metadata(code)
        validate_strategy_metadata(metadata)

        version = metadata["contract_version"]
        result["contract_version"] = version

        strategy_func = load_strategy_func_from_code(code)

        if version == 2:
            symbols = list(metadata["symbols"])
            result["symbols"] = symbols

            # 每个标的不同种子：价格路径分化，对冲/轮动策略才有信号可用
            data = {
                s: build_synthetic_kline(seed=7 + i)
                for i, s in enumerate(symbols)
            }

            # rebalance_threshold 仅 v2 接受，与 _ENGINE_KWARGS 一起钉死
            run = PortfolioBacktestCore(
                strategy_func, rebalance_threshold=0.01, **_ENGINE_KWARGS
            ).run(data)

            # 敞口/做空/信号事实必须基于策略的【原始】返回值（引擎的
            # _prepare_weights 会 clip + 缩放 + 乘 position_size，缩放后
            # 永远 ≤1，超敞口违例不可见）。直接复用引擎执行时记下的同一份
            # raw_weights，不再二次调用策略——含模块级状态的策略二次调用
            # 会返回与引擎实际执行不同的权重
            raw_w = run["raw_weights"].apply(pd.to_numeric, errors="coerce").fillna(0.0)

            result["trade_count"] = int(run["metrics"]["trade_count"])
            result["fill_count"] = int(run["metrics"]["fill_count"])
            result["opened_position"] = result["fill_count"] > 0
            result["has_nonzero_signal"] = bool((raw_w != 0).any().any())
            result["used_short"] = bool((raw_w < 0).any().any())
            result["max_gross_exposure"] = float(raw_w.abs().sum(axis=1).max())

        else:
            df = build_synthetic_kline()
            run = CodeBacktestCore(strategy_func, **_ENGINE_KWARGS).run(df)

            # 开仓/做空按【实际成交】视角（与 v2 的 fill 视角一致）：
            # 仅末根出现的信号永远不会成交，按意图报告会产出
            # 「0 笔交易；曾开仓」的自相矛盾事实
            targets = run["df"]["target_position"]
            trades = run["trades"]

            result["trade_count"] = int(run["metrics"]["trade_count"])
            result["opened_position"] = result["trade_count"] > 0
            result["has_nonzero_signal"] = bool((targets != 0).any())
            result["used_short"] = any(t.get("side") == "short" for t in trades)

        result["ok"] = True

    except Exception as e:
        result["error"] = _shorten(f"{type(e).__name__}: {e}")

    return result


def format_behavior_summary(behavior: dict) -> str:
    """
    把行为事实格式化成给审查 AI 的事实段落。

    固定用中文陈述（审查模型按目标语言输出 summary，事实段落语言无关），
    只陈述确定性事实、不做评价——评价是审查 AI 的工作。
    """

    if not behavior:
        return ""

    if not behavior["ok"]:
        return (
            f"代码在 {behavior['synthetic_bars']} 根合成 K 线上实际运行【失败】，"
            f"错误：{behavior['error']}"
        )

    parts = [
        f"代码在 {behavior['synthetic_bars']} 根合成 K 线"
        "（上涨/下跌/震荡三段行情）上实际运行通过",
        f"契约 v{behavior['contract_version']}",
    ]

    if behavior["symbols"]:
        parts.append(f"标的 {'+'.join(behavior['symbols'])}")

    parts.append(f"完成 {behavior['trade_count']} 笔交易")

    if behavior["fill_count"] is not None:
        parts.append(f"{behavior['fill_count']} 笔成交")

    if behavior["opened_position"]:
        parts.append("曾开仓")
    elif behavior.get("has_nonzero_signal"):
        parts.append("出现过非零信号但未发生成交（信号可能集中在数据末段）")
    else:
        parts.append("全程零信号、未开仓")

    parts.append("使用过做空" if behavior["used_short"] else "从未做空")

    if behavior["max_gross_exposure"] is not None:
        parts.append(f"最大总敞口 {behavior['max_gross_exposure']:.2f}（策略原始输出，缩放前）")

    return (
        "；".join(parts)
        + "。（合成数据局限：量价为合成值、长度有限；零交易不必然代表"
        "代码错误，可能是回看期过长或依赖的市场形态未在合成数据中出现。）"
    )
