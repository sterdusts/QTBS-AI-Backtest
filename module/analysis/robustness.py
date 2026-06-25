"""
稳健性分析（Phase 3）：架在 v1/v2 引擎之上的纯库分析/编排层，不改引擎执行数学。
复用 backtest_runner（加载一次 → 切窗/换参跑多次）。全部返回 JSON-able dict。

对外函数：
- split_in_out_sample：样本内/样本外切分（按 K 线根数切，比退化判过拟合）
- scan_engine_params：引擎参数（fee/slippage/leverage/position_size/止损pct）网格扫描
- scan_strategy_params：策略内部参数（PARAM_SPACE，均线周期等）网格扫描——契约 v3 解锁
- walk_forward：滚动窗前推。给定 param_grid（策略参数）时做【传统 WFO】：每窗先在样本
  内（IS）按目标指标寻优、再用最优参数评样本外（OOS），IS 严格早于 OOS（无未来函数）。
  不给 param_grid 时退化为「固定策略的分段 OOS 稳定性扫描」（向后兼容）。

注：策略内部参数（均线周期等）的扫描经契约 v3 的 generate_signals(df, params) 形参
显式传入（沙箱封死 __globals__/__code__/eval/exec，运行期注入不可行）；本模块据策略
模块级 PARAM_SPACE 取扫描空间，逐点 run_prepared(strategy_params=...)。

输出卫生（_sanitize_metrics）：metrics 里 payoff_ratio/profit_factor/annual_return_pct
可为 inf、trade_count==0 走全 0 精简分支——聚合（均值/方差/退化比）前必须把 inf/NaN
归一为 None、把空窗口（trade_count==0）单独标注，否则统计量被污染。
"""

import math

from module.modules.Load_real_kline import DEFAULT_FUNDING_DIR
from module.modules.data_panel import DEFAULT_DATA_DIR
from module.Strategy.backtest_runner import (
    InsufficientKlinesError,
    load_for_backtest,
    run_prepared,
)
from module.Strategy.strategy_loader import parse_strategy_metadata

# 输出 schema 版本（供前端稳定消费，类比 CONTRACT_VERSION）。
# v2：新增 scan_strategy_params（策略参数扫描）+ walk_forward 真 WFO（IS 寻优→OOS 评估）
ROBUSTNESS_CONTRACT = "robustness_v2"

# 退化对比关注的核心指标
_DEGRADATION_METRICS = (
    "total_return_pct", "annual_return_pct", "max_drawdown_pct",
    "sharpe_ratio", "profit_factor", "net_win_rate",
)


def _prepared_index(prepared):
    if prepared["route_version"] == 2:
        return next(iter(prepared["data"].values())).index
    return prepared["df"].index


def _slice_prepared(prepared, start_bar, end_bar):
    """按 K 线根数位置 [start_bar, end_bar)（end 独占）精确切 prepared（iloc）。
    用 iloc 而非 filter_df_by_date：后者对午夜时间戳按「整天」扩展，会让按根数
    切分错位（如切到 index[6]=次日 00:00 时整天 bar 被带进来）。"""
    if prepared["route_version"] == 2:
        data = {s: df.iloc[start_bar:end_bar] for s, df in prepared["data"].items()}
        return {**prepared, "data": data}
    return {**prepared, "df": prepared["df"].iloc[start_bar:end_bar]}


def _sanitize_value(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def _sanitize_metrics(metrics):
    """把 metrics 里的 inf/NaN 归一为 None，保证整体严格 JSON-able
    （json.dumps(allow_nan=False) 不抛）。其余字段原样。"""
    return {k: _sanitize_value(v) for k, v in metrics.items()}


def _run_window(prepared, engine_params, start_bar, end_bar, min_klines, funding_dir,
                strategy_params=None):
    """按根数位置 [start_bar, end_bar) 切子窗并跑一次，返回 {metrics(sanitized),
    start, end, kline_count, empty} 或 {insufficient: True, kline_count}（样本不足）。
    strategy_params 透传给策略（契约 v3）；None ⇒ 走策略默认（bit-level 退化）。"""
    sub = _slice_prepared(prepared, start_bar, end_bar)
    try:
        run = run_prepared(sub, engine_params, min_klines=min_klines,
                           funding_dir=funding_dir, strategy_params=strategy_params)
    except InsufficientKlinesError as e:
        return {"insufficient": True, "kline_count": e.kline_count}

    metrics = _sanitize_metrics(run["result"]["metrics"])
    return {
        "metrics": metrics,
        "start": run["actual_start"],
        "end": run["actual_end"],
        "kline_count": run["kline_count"],
        # trade_count==0 ⇒ 空窗口：指标全 0 无意义，聚合时须剔除
        "empty": int(metrics.get("trade_count", 0) or 0) == 0,
    }


def _cartesian(param_grid):
    """{名:[值,...]} → [{名:值,...}, ...] 全笛卡尔积，键序/值序确定（无 Date/random，可复现）。"""
    combos = [{}]
    for k, values in param_grid.items():
        combos = [dict(c, **{k: v}) for c in combos for v in values]
    return combos


def _param_space_of(strategy_code):
    """取策略模块级 PARAM_SPACE（契约 v3）；无声明则 None。解析不执行策略。"""
    return parse_strategy_metadata(strategy_code).get("param_space")


def _ratio(out_v, in_v):
    if out_v is None or in_v is None or in_v == 0:
        return None
    return out_v / in_v


def _degradation(is_window, oos_window):
    """IS vs OOS 原始对比 + 退化比 + 红线判据（用户拍板：原始对比+红线，不合成单一评分）。"""
    if is_window.get("insufficient") or oos_window.get("insufficient"):
        return {"available": False, "reason": "样本不足，无法对比"}

    im, om = is_window["metrics"], oos_window["metrics"]
    per_metric = {}
    for k in _DEGRADATION_METRICS:
        per_metric[k] = {"in": im.get(k), "out": om.get(k), "out_over_in": _ratio(om.get(k), im.get(k))}

    flags = []
    is_sharpe, oos_sharpe = im.get("sharpe_ratio"), om.get("sharpe_ratio")
    if is_sharpe is not None and oos_sharpe is not None and is_sharpe > 0 and oos_sharpe < 0.5 * is_sharpe:
        flags.append("OOS 夏普不足 IS 的 50%（过拟合嫌疑）")
    is_ret, oos_ret = im.get("total_return_pct"), om.get("total_return_pct")
    if is_ret is not None and oos_ret is not None and is_ret > 1.0 and oos_ret <= 0:
        flags.append("IS 显著正收益但 OOS 非正（过拟合嫌疑）")
    if is_window.get("empty") or oos_window.get("empty"):
        flags.append("存在空窗口（某段无交易），对比仅供参考")

    return {"available": True, "metrics": per_metric, "overfit_flags": flags}


def split_in_out_sample(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    engine_params=None, split_ratio=0.7, *,
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """样本内/样本外切分：按 K 线根数在全窗 t*=int(n*split_ratio) 处切两段，同一策略
    各跑一次，比退化。split_ratio 为 IS 占比。prepared 可注入（测试/复用已加载数据）。"""
    if not 0.0 < split_ratio < 1.0:
        raise ValueError("split_ratio 必须在 (0, 1) 之间")
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)

    index = _prepared_index(prepared)
    n = len(index)
    t = int(n * split_ratio)
    if t <= 0 or t >= n:
        raise ValueError(f"切分点无效：n={n}, t={t}（窗口太短或 split_ratio 过极端）")

    is_window = _run_window(prepared, engine_params, 0, t, min_klines, funding_dir)
    oos_window = _run_window(prepared, engine_params, t, n, min_klines, funding_dir)

    return {
        "type": "in_out",
        "contract": ROBUSTNESS_CONTRACT,
        "split_ratio": split_ratio,
        "boundary_time": str(index[t]),
        "warmup_note": "OOS 从切分点起，承认 rolling/ewm/lookback 头部暖机损耗（简单硬切）",
        "in_sample": is_window,
        "out_sample": oos_window,
        "degradation": _degradation(is_window, oos_window),
    }


def _scan_2d(param_grid, metric, run_point):
    """通用 1~2 维网格扫描骨架。run_point(point:dict)->window（_run_window 的返回）。
    返回 {x_param,x_values,y_param,y_values,matrix,cells}。matrix[yi][xi]=metric 标量。"""
    keys = list(param_grid.keys())
    if not 1 <= len(keys) <= 2:
        raise ValueError("param_grid 仅支持 1~2 个参数（2 维=热力图）")
    x_param = keys[0]
    x_values = list(param_grid[x_param])
    y_param = keys[1] if len(keys) == 2 else None
    y_values = list(param_grid[y_param]) if y_param else [None]

    matrix = []   # matrix[yi][xi] = metric 标量（或 None）
    cells = []
    for yv in y_values:
        row = []
        for xv in x_values:
            point = {x_param: xv}
            if y_param is not None:
                point[y_param] = yv
            window = run_point(point)
            if window.get("insufficient"):
                row.append(None)
                cells.append({"x": xv, "y": yv, "params": dict(point), "insufficient": True})
            else:
                m = window["metrics"]
                row.append(m.get(metric))
                cells.append({"x": xv, "y": yv, "params": dict(point),
                              "metrics": m, "empty": window["empty"]})
        matrix.append(row)
    return {"x_param": x_param, "x_values": x_values,
            "y_param": y_param, "y_values": y_values if y_param else None,
            "matrix": matrix, "cells": cells}


def scan_engine_params(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    base_engine_params, param_grid, *, metric="sharpe_ratio",
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """引擎参数网格扫描（1~2 维）。param_grid: {参数名: [取值列表]}（离散列表，避免浮点
    步长踩坑）。每点覆盖 base_engine_params 的被扫键 run 一次，matrix 填 metric 标量、
    cells 保全套 metrics 供下钻。数据/策略循环外只加载一次。"""
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)
    n = len(_prepared_index(prepared))   # 全窗根数（参数扫描不切窗）

    def run_point(point):
        engine = dict(base_engine_params or {}, **point)
        return _run_window(prepared, engine, 0, n, min_klines, funding_dir)

    grid = _scan_2d(param_grid, metric, run_point)
    return {"type": "param_scan", "scan_target": "engine",
            "contract": ROBUSTNESS_CONTRACT, "metric": metric, **grid}


def scan_strategy_params(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    engine_params=None, param_grid=None, *, metric="sharpe_ratio",
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """策略内部参数网格扫描（1~2 维，契约 v3）。param_grid 缺省时取策略模块级
    PARAM_SPACE；策略未声明 PARAM_SPACE 且未显式给 grid ⇒ ValueError。engine_params
    固定，每点以 strategy_params=该点 run 一次。"""
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)
    if param_grid is None:
        param_grid = _param_space_of(strategy_code)
    if not param_grid:
        raise ValueError("策略未声明 PARAM_SPACE 且未显式提供 param_grid，无可扫描的策略参数")
    n = len(_prepared_index(prepared))

    def run_point(point):
        return _run_window(prepared, engine_params, 0, n, min_klines, funding_dir,
                           strategy_params=dict(point))

    grid = _scan_2d(param_grid, metric, run_point)
    return {"type": "param_scan", "scan_target": "strategy",
            "contract": ROBUSTNESS_CONTRACT, "metric": metric, **grid}


def _optimize_is(prepared, engine_params, is_start, is_end, min_klines, funding_dir, combos, metric):
    """在 IS 段对每个候选策略参数各跑一次，按 metric【越大越好】取最优（夏普/收益/盈亏比/
    胜率都是越大越好；max_drawdown_pct 为负，max=最小回撤，亦自洽）。None/空窗/样本不足
    不参选；全不可选时退化为首个候选并标 valid=False。返回 (best_combo, best_value, valid, evaluated)。"""
    best = None  # (metric_value, combo)
    evaluated = 0
    for combo in combos:
        w = _run_window(prepared, engine_params, is_start, is_end, min_klines, funding_dir,
                        strategy_params=combo)
        if w.get("insufficient") or w.get("empty"):
            continue
        mv = w["metrics"].get(metric)
        if mv is None:
            continue
        evaluated += 1
        if best is None or mv > best[0]:
            best = (mv, combo)
    if best is None:
        return combos[0], None, False, evaluated
    return best[1], best[0], True, evaluated


def walk_forward(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    engine_params=None, *, train_bars, test_bars, step_bars=None, anchored=False,
    param_grid=None, optimize_metric="sharpe_ratio",
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """walk-forward 前推。
    - 给定 param_grid（或策略声明了 PARAM_SPACE 自动取用）⇒【传统 WFO】：每窗先在 IS 段按
      optimize_metric 寻优策略参数，再以最优参数评 OOS 段。IS 严格早于 OOS ⇒ 参数选择不含
      未来函数（OOS 是真正的样本外）。
    - 无可扫描参数 ⇒ 退化为「固定策略的分段 OOS 稳定性扫描」（向后兼容旧行为）。
    step_bars 默认=test_bars（不重叠 OOS）；anchored=True 时 IS 起点固定、窗口增长。"""
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars/test_bars 必须为正")
    step = step_bars if step_bars else test_bars
    if step <= 0:
        raise ValueError("step_bars 必须为正")
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)

    if param_grid is None:
        param_grid = _param_space_of(strategy_code)
    combos = _cartesian(param_grid) if param_grid else None
    optimized = bool(combos)

    index = _prepared_index(prepared)
    n = len(index)

    windows = []
    k = 0
    while True:
        is_start = 0 if anchored else k * step
        is_end = train_bars + k * step  # anchored: IS 增长；rolling: IS 滑动
        oos_start = is_end
        oos_end = oos_start + test_bars
        if oos_end > n:
            break

        train = {"start": str(index[is_start]), "end": str(index[is_end - 1]), "bars": is_end - is_start}
        if optimized:
            best_combo, best_val, valid, evaluated = _optimize_is(
                prepared, engine_params, is_start, is_end, min_klines, funding_dir, combos, optimize_metric)
            oos_window = _run_window(prepared, engine_params, oos_start, oos_end, min_klines, funding_dir,
                                     strategy_params=best_combo)
            train.update({"chosen_params": best_combo, "is_metric": best_val,
                          "is_metric_name": optimize_metric, "is_optimized": valid,
                          "candidates_evaluated": evaluated})
        else:
            oos_window = _run_window(prepared, engine_params, oos_start, oos_end, min_klines, funding_dir)

        windows.append({"index": k, "train": train, "test": oos_window})
        k += 1

    note = ("传统 WFO：每窗 IS 段寻优策略参数、最优参数评 OOS（IS 早于 OOS，无未来函数）"
            if optimized else
            "无可扫描策略参数（未声明 PARAM_SPACE / 未给 param_grid）：固定策略的分段 OOS 稳定性扫描")
    return {
        "type": "walk_forward",
        "contract": ROBUSTNESS_CONTRACT,
        "optimized": optimized,
        "optimize_metric": optimize_metric if optimized else None,
        "param_grid": param_grid if optimized else None,
        "wfo_note": note,
        "window_def": {"train_bars": train_bars, "test_bars": test_bars, "step_bars": step, "anchored": anchored},
        "windows": windows,
        "aggregate": _aggregate_walk_forward(windows),
    }


def _aggregate_walk_forward(windows):
    """聚合各段 OOS 表现（剔除空窗口/样本不足窗口防污染）。"""
    rets, sharpes, dds = [], [], []
    valid = 0
    for w in windows:
        t = w["test"]
        if t.get("insufficient") or t.get("empty"):
            continue
        # WFO 模式下若该窗 IS 段无有效信号（is_optimized=False，参数退化为首候选而非
        # 真正寻优），其 OOS 不代表"按最优参数前推"的结果，不计入聚合统计防污染。
        # 稳定性扫描模式（无 param_grid）的窗口没有 is_optimized 键 ⇒ 不受影响。
        if w["train"].get("is_optimized") is False:
            continue
        m = t["metrics"]
        valid += 1
        if m.get("total_return_pct") is not None:
            rets.append(m["total_return_pct"])
        if m.get("sharpe_ratio") is not None:
            sharpes.append(m["sharpe_ratio"])
        if m.get("max_drawdown_pct") is not None:
            dds.append(m["max_drawdown_pct"])

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    def _std(xs):
        if len(xs) < 2:
            return 0.0 if xs else None
        mu = sum(xs) / len(xs)
        return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5

    return {
        "total_windows": len(windows),
        "valid_windows": valid,
        "positive_window_ratio": (sum(1 for r in rets if r > 0) / len(rets)) if rets else None,
        "mean_out_return": _mean(rets),
        "std_out_return": _std(rets),
        "mean_out_sharpe": _mean(sharpes),
        "worst_window_drawdown": min(dds) if dds else None,  # 最负=最差
    }


def _scan_grid_from_space(param_space):
    """从策略 PARAM_SPACE 取【前 ≤2 维】构成热力图扫描网格（>2 维只取前两维，
    其余维不进扫描——webUI 热力图最多二维）。无声明 ⇒ None。"""
    if not param_space:
        return None
    keys = list(param_space.keys())[:2]
    return {k: param_space[k] for k in keys}


def run_full_analysis(
    strategy_code, ui_symbol, timeframe, start_str, end_str, engine_params=None, *,
    split_ratio=0.7, train_frac=0.3, test_frac=0.1, anchored=False,
    scan_metric="sharpe_ratio", optimize_metric="sharpe_ratio",
    min_window_bars=20, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """一站式稳健性分析编排（webUI 面板用）：加载【一次】数据，复跑样本内外切分 +
    walk-forward（策略声明 PARAM_SPACE 则自动真 WFO）+ 策略参数热力图扫描（若有
    PARAM_SPACE）。WFO 训练/测试窗按全窗根数比例派生（train_frac/test_frac）。
    返回单一 JSON-able 报告 dict（各子分析仍各自 JSON-able）。数据不足以切窗时
    available=False + reason。"""
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)

    index = _prepared_index(prepared)
    n = len(index)
    # 切两段 IS/OOS 至少各 min_window_bars，WFO 至少容下一段 train+test
    if n < max(2 * min_window_bars, 4):
        return {
            "type": "robustness_report", "contract": ROBUSTNESS_CONTRACT,
            "available": False,
            "reason": f"数据根数不足（{n} 根），无法做稳健性切窗分析（至少需 {max(2 * min_window_bars, 4)} 根）",
            "meta": {"display_symbol": prepared["display_symbol"], "timeframe": timeframe, "kline_count": n},
        }

    in_out = split_in_out_sample(
        strategy_code, ui_symbol, timeframe, start_str, end_str,
        engine_params=engine_params, split_ratio=split_ratio,
        min_klines=min_window_bars, prepared=prepared, funding_dir=funding_dir)

    train_bars = max(min_window_bars, int(n * train_frac))
    test_bars = max(min_window_bars, int(n * test_frac))
    wfo = walk_forward(
        strategy_code, ui_symbol, timeframe, start_str, end_str,
        engine_params=engine_params, train_bars=train_bars, test_bars=test_bars,
        step_bars=test_bars, anchored=anchored, optimize_metric=optimize_metric,
        min_klines=min_window_bars, prepared=prepared, funding_dir=funding_dir)

    param_space = _param_space_of(strategy_code)
    scan = None
    if param_space:
        scan = scan_strategy_params(
            strategy_code, ui_symbol, timeframe, start_str, end_str,
            engine_params=engine_params, param_grid=_scan_grid_from_space(param_space),
            metric=scan_metric, min_klines=min_window_bars, prepared=prepared, funding_dir=funding_dir)

    return {
        "type": "robustness_report",
        "contract": ROBUSTNESS_CONTRACT,
        "available": True,
        "in_out": in_out,
        "walk_forward": wfo,
        "param_scan": scan,         # None ⇒ 策略未声明 PARAM_SPACE，无热力图
        "meta": {
            "display_symbol": prepared["display_symbol"],
            "timeframe": timeframe,
            "kline_count": n,
            "actual_start": str(index[0]),
            "actual_end": str(index[-1]),
            "has_param_space": bool(param_space),
        },
    }
