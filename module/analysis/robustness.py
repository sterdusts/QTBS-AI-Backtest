"""
稳健性分析（Phase 3）：架在 v1/v2 引擎之上的纯库分析/编排层，不改引擎执行数学。
复用 backtest_runner（加载一次 → 切窗/换参跑多次）。全部返回 JSON-able dict。

三个对外函数：
- split_in_out_sample：样本内/样本外切分（按 K 线根数切，比退化判过拟合）
- scan_engine_params：引擎参数（fee/slippage/leverage/position_size/止损pct）网格扫描
- walk_forward：滚动窗前推（本期【无 IS 寻优的分段 OOS 稳定性扫描】，因策略内部参数
  尚不可调；契约 v3 让策略可参数化后升级为传统 WFO）

注：策略内部参数（均线周期等）无法在已加载的 func 上扫——运行期注入被沙箱封死
（__globals__/__code__/eval/exec 全禁），只能走契约 v3 的 generate_signals(df, params)。
本模块只做引擎参数 + 时间切分。

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

# 输出 schema 版本（供前端稳定消费，类比 CONTRACT_VERSION）
ROBUSTNESS_CONTRACT = "robustness_v1"

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


def _run_window(prepared, engine_params, start_bar, end_bar, min_klines, funding_dir):
    """按根数位置 [start_bar, end_bar) 切子窗并跑一次，返回 {metrics(sanitized),
    start, end, kline_count, empty} 或 {insufficient: True, kline_count}（样本不足）。"""
    sub = _slice_prepared(prepared, start_bar, end_bar)
    try:
        run = run_prepared(sub, engine_params, min_klines=min_klines, funding_dir=funding_dir)
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


def scan_engine_params(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    base_engine_params, param_grid, *, metric="sharpe_ratio",
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """引擎参数网格扫描（1~2 维）。param_grid: {参数名: [取值列表]}（离散列表，避免浮点
    步长踩坑）。每点覆盖 base_engine_params 的被扫键 run 一次，matrix 填 metric 标量、
    cells 保全套 metrics 供下钻。数据/策略循环外只加载一次。"""
    keys = list(param_grid.keys())
    if not 1 <= len(keys) <= 2:
        raise ValueError("param_grid 仅支持 1~2 个参数（2 维=热力图）")
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)

    n = len(_prepared_index(prepared))   # 全窗根数（参数扫描不切窗）
    x_param = keys[0]
    x_values = list(param_grid[x_param])
    y_param = keys[1] if len(keys) == 2 else None
    y_values = list(param_grid[y_param]) if y_param else [None]

    matrix = []   # matrix[yi][xi] = metric 标量（或 None）
    cells = []
    for yv in y_values:
        row = []
        for xv in x_values:
            point = dict(base_engine_params or {})
            point[x_param] = xv
            if y_param is not None:
                point[y_param] = yv
            window = _run_window(prepared, point, 0, n, min_klines, funding_dir)
            if window.get("insufficient"):
                row.append(None)
                cells.append({"x": xv, "y": yv, "params": _scanned(point, x_param, y_param), "insufficient": True})
            else:
                m = window["metrics"]
                row.append(m.get(metric))
                cells.append({"x": xv, "y": yv, "params": _scanned(point, x_param, y_param),
                              "metrics": m, "empty": window["empty"]})
        matrix.append(row)

    return {
        "type": "param_scan",
        "contract": ROBUSTNESS_CONTRACT,
        "metric": metric,
        "x_param": x_param, "x_values": x_values,
        "y_param": y_param, "y_values": y_values if y_param else None,
        "matrix": matrix,
        "cells": cells,
    }


def _scanned(point, x_param, y_param):
    out = {x_param: point[x_param]}
    if y_param is not None:
        out[y_param] = point[y_param]
    return out


def walk_forward(
    strategy_code, ui_symbol, timeframe, start_str, end_str,
    engine_params=None, *, train_bars, test_bars, step_bars=None, anchored=False,
    min_klines=30, prepared=None,
    data_dir=DEFAULT_DATA_DIR, funding_dir=DEFAULT_FUNDING_DIR,
):
    """walk-forward 前推。【本期无 IS 寻优】（策略内部参数不可调）：退化为「固定策略在
    多个滚动 OOS 段的分段稳定性扫描」——契约 v3 让策略可参数化后升级为传统 WFO。
    step_bars 默认=test_bars（不重叠 OOS）；anchored=True 时 IS 起点固定、窗口增长。"""
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars/test_bars 必须为正")
    step = step_bars if step_bars else test_bars
    if step <= 0:
        raise ValueError("step_bars 必须为正")
    if prepared is None:
        prepared = load_for_backtest(strategy_code, ui_symbol, timeframe, start_str, end_str, data_dir=data_dir)

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

        oos_window = _run_window(prepared, engine_params, oos_start, oos_end, min_klines, funding_dir)
        windows.append({
            "index": k,
            "train": {"start": str(index[is_start]), "end": str(index[is_end - 1]), "bars": is_end - is_start},
            "test": oos_window,
        })
        k += 1

    return {
        "type": "walk_forward",
        "contract": ROBUSTNESS_CONTRACT,
        "wfo_note": "本期无 IS 寻优（策略内部参数尚不可调）：为固定策略的分段 OOS 稳定性扫描，"
                    "非传统 WFO；契约 v3 策略可参数化后升级",
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
