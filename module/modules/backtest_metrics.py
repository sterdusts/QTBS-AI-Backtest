"""
回测绩效指标计算（单资产引擎与组合引擎共用）。

从 CodeBacktestCore 中抽出，输入输出保持不变：
- equity_curve: [{"time", "equity", "equity_close", "equity_worst", ...}, ...]
- trades: [{"gross_pnl", "pnl", "holding_hours", ...}, ...]

trades 对组合引擎而言是「持仓片段」（episode：从建仓到完全平仓），
对单资产引擎而言就是逐笔交易，两者结构兼容。
"""

import numpy as np
import pandas as pd


def calculate_holding_hours(entry_time, exit_time) -> float:
    """持仓时长口径的唯一定义（两引擎共用，接受 Timestamp 或字符串）。"""

    if entry_time is None or exit_time is None:
        return 0.0

    return (pd.to_datetime(exit_time) - pd.to_datetime(entry_time)).total_seconds() / 3600


def attach_engine_metrics(metrics, core, liquidation_count, liquidated, **extra) -> None:
    """
    把引擎配置回填进 metrics 报告（两引擎共用同一键集，
    新增回显字段只改这里，不会出现 v1/v2 报告字段静默分叉）。
    """

    metrics["leverage"] = core.leverage
    metrics["position_size"] = core.position_size
    metrics["enable_liquidation"] = core.enable_liquidation
    metrics["maintenance_margin_rate"] = core.maintenance_margin_rate
    metrics["liquidation_count"] = int(liquidation_count)
    metrics["liquidated"] = bool(liquidated)
    metrics.update(extra)


def max_consecutive_count(values, condition_func) -> int:
    max_count = 0
    current_count = 0

    for value in values:
        if condition_func(value):
            current_count += 1
            max_count = max(max_count, current_count)
        else:
            current_count = 0

    return max_count


def calculate_metrics(
    equity_curve,
    trades,
    initial_cash,
    final_equity,
) -> dict:
    if initial_cash <= 0:
        total_return_pct = 0.0
    else:
        total_return_pct = (final_equity / initial_cash - 1) * 100

    worst_equity_values = np.array(
        [x.get("equity_worst", x["equity"]) for x in equity_curve],
        dtype=float,
    )

    if len(worst_equity_values) > 0:
        peak = np.maximum.accumulate(worst_equity_values)
        peak = np.where(peak == 0, np.nan, peak)
        drawdown = (worst_equity_values - peak) / peak
        drawdown = drawdown[~np.isnan(drawdown)]
        max_drawdown_pct = float(drawdown.min() * 100) if len(drawdown) > 0 else 0.0
    else:
        max_drawdown_pct = 0.0

    annual_return_pct = 0.0
    sharpe_ratio = 0.0

    if len(equity_curve) >= 2:
        equity_df = pd.DataFrame(equity_curve)
        equity_df["time"] = pd.to_datetime(equity_df["time"])
        equity_df = equity_df.sort_values("time")

        if "equity_close" in equity_df.columns:
            equity_series = equity_df["equity_close"].astype(float)
        else:
            equity_series = equity_df["equity"].astype(float)

        start_time = equity_df["time"].iloc[0]
        end_time = equity_df["time"].iloc[-1]
        duration_days = (end_time - start_time).total_seconds() / 86400

        if duration_days > 0 and initial_cash > 0 and final_equity > 0:
            annual_return_pct = ((final_equity / initial_cash) ** (365.25 / duration_days) - 1) * 100

        returns = equity_series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()

        time_diffs = equity_df["time"].diff().dropna()

        if len(time_diffs) > 0:
            median_period_days = time_diffs.median().total_seconds() / 86400

            if median_period_days > 0 and len(returns) > 1:
                periods_per_year = 365.25 / median_period_days
                return_std = returns.std()

                if return_std and return_std > 0:
                    sharpe_ratio = returns.mean() / return_std * np.sqrt(periods_per_year)

    trade_count = len(trades)

    if trade_count == 0:
        return {
            "initial_cash": initial_cash,
            "final_equity": final_equity,
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "annual_return_pct": annual_return_pct,
            "sharpe_ratio": sharpe_ratio,

            "trade_count": 0,
            "gross_win_rate": 0.0,
            "net_win_rate": 0.0,
            "win_rate": 0.0,

            "avg_profit": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,

            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "avg_holding_hours": 0.0,
        }

    gross_pnls = np.array([t.get("gross_pnl", 0.0) for t in trades], dtype=float)
    net_pnls = np.array([t.get("pnl", 0.0) for t in trades], dtype=float)
    holding_hours = np.array([t.get("holding_hours", 0.0) for t in trades], dtype=float)

    gross_win_rate = np.sum(gross_pnls > 0) / trade_count * 100
    net_win_rate = np.sum(net_pnls > 0) / trade_count * 100

    profit_trades = net_pnls[net_pnls > 0]
    loss_trades = net_pnls[net_pnls < 0]

    avg_profit = float(profit_trades.mean()) if len(profit_trades) > 0 else 0.0
    avg_loss = float(loss_trades.mean()) if len(loss_trades) > 0 else 0.0

    if avg_loss < 0:
        payoff_ratio = avg_profit / abs(avg_loss)
    else:
        payoff_ratio = float("inf") if avg_profit > 0 else 0.0

    gross_profit_sum = float(profit_trades.sum()) if len(profit_trades) > 0 else 0.0
    gross_loss_sum = float(loss_trades.sum()) if len(loss_trades) > 0 else 0.0

    if gross_loss_sum < 0:
        profit_factor = gross_profit_sum / abs(gross_loss_sum)
    else:
        profit_factor = float("inf") if gross_profit_sum > 0 else 0.0

    max_consecutive_wins = max_consecutive_count(net_pnls, lambda x: x > 0)
    max_consecutive_losses = max_consecutive_count(net_pnls, lambda x: x < 0)

    avg_holding_hours = float(holding_hours.mean()) if len(holding_hours) > 0 else 0.0

    return {
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "annual_return_pct": annual_return_pct,
        "sharpe_ratio": sharpe_ratio,

        "trade_count": trade_count,
        "gross_win_rate": gross_win_rate,
        "net_win_rate": net_win_rate,
        "win_rate": net_win_rate,

        "avg_profit": avg_profit,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,

        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
        "avg_holding_hours": avg_holding_hours,
    }
