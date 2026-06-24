"""
无 Gradio 的回测编排内核（webUI 与稳健性分析层共用，杜绝两条执行路径分叉）。

把「解析路由 → 加载策略 → 按版本加载数据 → 资金费率 → 选引擎 run → 取 result」
从 webUI.run_backtest_from_ui 抽出为纯库函数：
- webUI.run_backtest_from_ui 调 run_once 拿 result + 元信息，再自行出图；
- 稳健性分析（module/analysis/robustness.py）用 load_for_backtest 加载【一次】，
  run_prepared 切窗/换参跑【多次】，数据只打数据层一次（N 次回测 I/O ≈ 1 次）。

纯库、不依赖 Gradio；返回结构里只放引擎原始 result + 元信息，出图/UI 留给调用方。
"""

from module.modules.Load_real_kline import (
    DEFAULT_FUNDING_DIR,
    normalize_symbol,
)
from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.data_panel import (
    DEFAULT_DATA_DIR,
    build_funding_rates,
    filter_df_by_date,
    load_aligned_panel,
    load_symbol_kline,
)
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from module.Strategy.strategy_loader import (
    load_strategy_func_from_code,
    parse_strategy_metadata,
    validate_strategy_metadata,
)


class InsufficientKlinesError(ValueError):
    """回测（子）窗口 K 线根数不足（< min_klines）。调用方据此给友好提示
    或在稳健性扫描中把该窗口标注为「样本不足」。"""

    def __init__(self, kline_count, min_klines):
        self.kline_count = kline_count
        self.min_klines = min_klines
        super().__init__(f"K 线根数不足：{kline_count} < {min_klines}")


def resolve_strategy_route(strategy_code: str, ui_symbol: str):
    """根据策略代码元数据决定回测路由，返回 (contract_version, symbols)：
    - v2：使用代码中声明的 SYMBOLS（共享校验已保证非空、格式规范、无重复）
    - v1：代码点名了标的就用代码的（优先于 UI 选择），否则用 UI 选择的标的
    版本与 SYMBOLS 组合规则单源在 strategy_loader.validate_strategy_metadata。
    """
    metadata = parse_strategy_metadata(strategy_code)
    validate_strategy_metadata(metadata)

    if metadata["contract_version"] == 2:
        return 2, list(metadata["symbols"])
    if metadata["symbols"]:
        return 1, [normalize_symbol(metadata["symbols"][0])]
    return 1, [normalize_symbol(ui_symbol)]


def load_for_backtest(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    data_dir=DEFAULT_DATA_DIR,
):
    """加载一次：路由 + 按版本加载（并日期过滤到请求窗口）数据。返回 prepared
    dict 供 run_prepared 复用（数据只打数据层一次，切窗/换参不重复加载）。"""
    route_version, route_symbols = resolve_strategy_route(strategy_code, ui_symbol)

    if route_version == 2:
        data = load_aligned_panel(
            route_symbols, timeframe,
            start_date=start_str, end_date=end_str, data_dir=data_dir,
        )
        return {
            "route_version": 2, "route_symbols": route_symbols,
            "strategy_code": strategy_code, "timeframe": timeframe,
            "data": data, "df": None,
            "display_symbol": " + ".join(route_symbols),
        }

    symbol = route_symbols[0]
    df = load_symbol_kline(symbol, timeframe, data_dir=data_dir, required_end=end_str)
    df = filter_df_by_date(df, start_str, end_str)
    return {
        "route_version": 1, "route_symbols": route_symbols,
        "strategy_code": strategy_code, "timeframe": timeframe,
        "data": None, "df": df, "display_symbol": symbol,
    }


def run_prepared(
    prepared, engine_params=None, *,
    sub_start=None, sub_end=None, min_klines=100,
    funding_dir=DEFAULT_FUNDING_DIR,
):
    """在已加载数据上跑一次回测。可选 sub_start/sub_end 把窗口切到子区间
    （样本内外 / walk-forward）。engine_params 是引擎构造参数 dict（不含
    strategy_func）。

    每次重新 load_strategy_func_from_code 得到【全新模块】——避免 AI 策略的
    模块级可变状态在多次 run 之间漂移（稳健性扫描会对同一策略反复调用）。
    funding 按【子窗索引】重建（天然防未来函数）。
    """
    engine_params = dict(engine_params or {})
    route_version = prepared["route_version"]
    route_symbols = prepared["route_symbols"]
    sliced = sub_start is not None or sub_end is not None

    if route_version == 2:
        data = prepared["data"]
        if sliced:
            data = {s: filter_df_by_date(df, sub_start, sub_end) for s, df in data.items()}
        data_index = next(iter(data.values())).index
    else:
        df = prepared["df"]
        if sliced:
            df = filter_df_by_date(df, sub_start, sub_end)
        data_index = df.index

    kline_count = len(data_index)
    if kline_count < min_klines:
        raise InsufficientKlinesError(kline_count, min_klines)

    strategy_func = load_strategy_func_from_code(prepared["strategy_code"])
    funding_map = build_funding_rates(route_symbols, data_index, funding_dir=funding_dir)
    engine_kwargs = dict(engine_params, strategy_func=strategy_func)

    if route_version == 2:
        result = PortfolioBacktestCore(**engine_kwargs).run(data, funding_rates=funding_map)
    else:
        symbol = route_symbols[0]
        v1_funding = funding_map.get(symbol) if funding_map else None
        result = CodeBacktestCore(**engine_kwargs).run(df, funding_rates=v1_funding)

    return {
        "result": result,
        "route_version": route_version,
        "route_symbols": route_symbols,
        "display_symbol": prepared["display_symbol"],
        "data_index": data_index,
        "kline_count": kline_count,
        "actual_start": str(data_index[0]),
        "actual_end": str(data_index[-1]),
    }


def run_once(
    strategy_code, ui_symbol, timeframe, start_str, end_str, engine_params=None, *,
    min_klines=100, data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """单次回测便捷入口 = load_for_backtest + run_prepared（全窗）。webUI 用它替代
    内联编排，保证「单次回测」与「稳健性多次回测」走同一内核、永不分叉。"""
    prepared = load_for_backtest(
        strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir,
    )
    return run_prepared(prepared, engine_params, min_klines=min_klines, funding_dir=funding_dir)
