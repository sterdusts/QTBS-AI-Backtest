import pandas as pd
import numpy as np


class CodeBacktestCore:
    """
    代码策略回测核心。

    strategy_func(df) 只负责生成 target_position：
        1  = 做多
        0  = 空仓
        -1 = 做空

    本版本重点修复：
        1. 实时权益不再只看 close，而是逐 K 线计算盘中 high/low 浮动权益。
        2. 多仓用 low 检查盘中最坏风险。
        3. 空仓用 high 检查盘中最坏风险。
        4. 支持简化强平/爆仓检测。
        5. equity_curve 中保留 close/high/low/worst/best 多组权益数据。
        6. 为兼容旧图表，equity 字段使用“盘中最大偏离权益”，插针会被画出来。
    """

    def __init__(
        self,
        strategy_func,
        initial_cash: float = 1000,
        fee_rate: float = 0.0,
        slippage: float = 0.0,
        leverage: int = 1,
        position_size: float = 1.0,
        enable_liquidation: bool = True,
        maintenance_margin_rate: float = 0.0,
        stop_on_liquidation: bool = True,
        liquidation_fee_rate: float = 0.0,
    ):
        self.strategy_func = strategy_func
        self.initial_cash = float(initial_cash)
        self.fee_rate = float(fee_rate)
        self.slippage = float(slippage)

        self.leverage = int(leverage)
        if self.leverage <= 0:
            self.leverage = 1

        self.position_size = float(position_size)
        self.position_size = min(max(self.position_size, 0.0), 1.0)

        self.enable_liquidation = bool(enable_liquidation)
        self.maintenance_margin_rate = max(float(maintenance_margin_rate), 0.0)
        self.stop_on_liquidation = bool(stop_on_liquidation)
        self.liquidation_fee_rate = max(float(liquidation_fee_rate), 0.0)

    def run(self, df: pd.DataFrame) -> dict:
        df = df.copy()
        df = self.strategy_func(df)

        if "target_position" not in df.columns:
            raise ValueError("策略函数必须生成 target_position 字段")

        required_columns = ["open", "high", "low", "close"]
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"回测数据缺少必要字段: {missing_columns}")

        df["target_position"] = df["target_position"].fillna(0).astype(int)

        cash = self.initial_cash

        position = 0.0
        position_side = 0

        entry_price = None
        entry_raw_price = None
        entry_time = None
        entry_equity = None
        entry_margin = None
        entry_notional = None
        entry_open_fee = None

        trades = []
        equity_curve = []
        realized_equity_curve = []
        liquidation_events = []

        realized_equity = self.initial_cash
        liquidated = False

        for i in range(1, len(df) - 1):
            current_time = df.index[i]
            next_time = df.index[i + 1]

            current_target = int(df["target_position"].iloc[i])
            previous_target = int(df["target_position"].iloc[i - 1])

            high_price = float(df["high"].iloc[i])
            low_price = float(df["low"].iloc[i])
            close_price = float(df["close"].iloc[i])
            next_open = float(df["open"].iloc[i + 1])

            mtm = self._calculate_mtm_equity(
                cash=cash,
                position=position,
                position_side=position_side,
                entry_price=entry_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
            )

            liquidation_event = None

            if self.enable_liquidation and position_side != 0:
                liquidation_event = self._check_liquidation(
                    current_time=current_time,
                    cash=cash,
                    position=position,
                    position_side=position_side,
                    entry_price=entry_price,
                    entry_raw_price=entry_raw_price,
                    entry_time=entry_time,
                    entry_equity=entry_equity,
                    entry_margin=entry_margin,
                    entry_notional=entry_notional,
                    entry_open_fee=entry_open_fee,
                    high_price=high_price,
                    low_price=low_price,
                )

            if liquidation_event is not None:
                liquidation_price = liquidation_event["liquidation_price"]

                liquidation_equity = float(liquidation_event["equity_after"])
                liquidation_fee = float(liquidation_event["liquidation_fee"])
                cash = max(liquidation_equity - liquidation_fee, 0.0)

                trade = self._build_liquidation_trade(
                    event=liquidation_event,
                    entry_time=entry_time,
                    position_side=position_side,
                    entry_price=entry_price,
                    entry_raw_price=entry_raw_price,
                    entry_equity=entry_equity,
                    entry_margin=entry_margin,
                    entry_notional=entry_notional,
                    entry_open_fee=entry_open_fee,
                    exit_time=current_time,
                    exit_price=liquidation_price,
                    exit_raw_price=liquidation_price,
                    cash_after=cash,
                )

                trades.append(trade)

                realized_equity = cash
                liquidation_events.append(liquidation_event)

                equity_curve.append({
                    "time": str(current_time),
                    "equity": float(cash),
                    "equity_close": float(cash),
                    "equity_at_high": float(mtm["equity_at_high"]),
                    "equity_at_low": float(mtm["equity_at_low"]),
                    "equity_worst": float(mtm["equity_worst"]),
                    "equity_best": float(mtm["equity_best"]),
                    "price_high": float(high_price),
                    "price_low": float(low_price),
                    "price_close": float(close_price),
                    "position_side": int(position_side),
                    "liquidated": True,
                    "liquidation_price": float(liquidation_price),
                })

                realized_equity_curve.append({
                    "time": str(current_time),
                    "equity": float(realized_equity),
                })

                position = 0.0
                position_side = 0
                entry_price = None
                entry_raw_price = None
                entry_time = None
                entry_equity = None
                entry_margin = None
                entry_notional = None
                entry_open_fee = None

                liquidated = True

                if self.stop_on_liquidation:
                    break

                continue

            equity_for_chart = mtm["equity_intrabar_extreme"]

            equity_curve.append({
                "time": str(current_time),
                "equity": float(equity_for_chart),
                "equity_close": float(mtm["equity_close"]),
                "equity_at_high": float(mtm["equity_at_high"]),
                "equity_at_low": float(mtm["equity_at_low"]),
                "equity_worst": float(mtm["equity_worst"]),
                "equity_best": float(mtm["equity_best"]),
                "price_high": float(high_price),
                "price_low": float(low_price),
                "price_close": float(close_price),
                "position_side": int(position_side),
                "liquidated": False,
                "liquidation_price": None,
            })

            realized_equity_curve.append({
                "time": str(current_time),
                "equity": float(realized_equity),
            })

            if current_target == previous_target:
                continue

            execute_time = next_time
            exit_raw_price = next_open

            if position_side != 0:
                close_result = self._close_position(
                    cash=cash,
                    position=position,
                    position_side=position_side,
                    entry_price=entry_price,
                    entry_raw_price=entry_raw_price,
                    entry_time=entry_time,
                    entry_equity=entry_equity,
                    entry_margin=entry_margin,
                    entry_notional=entry_notional,
                    entry_open_fee=entry_open_fee,
                    exit_raw_price=exit_raw_price,
                    execute_time=execute_time,
                )

                cash = close_result["cash_after"]
                trades.append(close_result["trade"])
                realized_equity = cash

                position = 0.0
                position_side = 0
                entry_price = None
                entry_raw_price = None
                entry_time = None
                entry_equity = None
                entry_margin = None
                entry_notional = None
                entry_open_fee = None

            if current_target == 1:
                open_result = self._open_position(
                    cash=cash,
                    side=1,
                    raw_price=next_open,
                    execute_time=execute_time,
                )

                if open_result is not None:
                    cash = open_result["cash_after"]
                    position = open_result["position"]
                    position_side = 1
                    entry_price = open_result["entry_price"]
                    entry_raw_price = open_result["entry_raw_price"]
                    entry_time = open_result["entry_time"]
                    entry_equity = open_result["entry_equity"]
                    entry_margin = open_result["entry_margin"]
                    entry_notional = open_result["entry_notional"]
                    entry_open_fee = open_result["entry_open_fee"]

            elif current_target == -1:
                open_result = self._open_position(
                    cash=cash,
                    side=-1,
                    raw_price=next_open,
                    execute_time=execute_time,
                )

                if open_result is not None:
                    cash = open_result["cash_after"]
                    position = open_result["position"]
                    position_side = -1
                    entry_price = open_result["entry_price"]
                    entry_raw_price = open_result["entry_raw_price"]
                    entry_time = open_result["entry_time"]
                    entry_equity = open_result["entry_equity"]
                    entry_margin = open_result["entry_margin"]
                    entry_notional = open_result["entry_notional"]
                    entry_open_fee = open_result["entry_open_fee"]

            elif current_target == 0:
                pass

            else:
                raise ValueError("target_position 只能是 -1, 0, 1")

        final_equity = equity_curve[-1]["equity_close"] if equity_curve else self.initial_cash

        metrics = self._calculate_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_cash=self.initial_cash,
            final_equity=final_equity,
        )

        metrics["leverage"] = self.leverage
        metrics["position_size"] = self.position_size
        metrics["enable_liquidation"] = self.enable_liquidation
        metrics["maintenance_margin_rate"] = self.maintenance_margin_rate
        metrics["liquidation_count"] = len(liquidation_events)
        metrics["liquidated"] = bool(liquidated)

        return {
            "df": df,
            "trades": trades,
            "equity_curve": equity_curve,
            "realized_equity_curve": realized_equity_curve,
            "liquidation_events": liquidation_events,
            "metrics": metrics,
        }

    def _open_position(self, cash, side, raw_price, execute_time):
        if cash <= 0:
            return None

        if side == 1:
            entry_price = raw_price * (1 + self.slippage)
        elif side == -1:
            entry_price = raw_price * (1 - self.slippage)
        else:
            raise ValueError("side 只能是 1 或 -1")

        entry_equity = cash
        entry_margin = entry_equity * self.position_size
        entry_notional = entry_margin * self.leverage

        if entry_notional <= 0:
            return None

        entry_open_fee = entry_notional * self.fee_rate
        cash_after = entry_equity - entry_open_fee

        if cash_after <= 0:
            return None

        position = entry_notional / entry_price

        return {
            "cash_after": float(cash_after),
            "position": float(position),
            "entry_price": float(entry_price),
            "entry_raw_price": float(raw_price),
            "entry_time": execute_time,
            "entry_equity": float(entry_equity),
            "entry_margin": float(entry_margin),
            "entry_notional": float(entry_notional),
            "entry_open_fee": float(entry_open_fee),
        }

    def _close_position(
        self,
        cash,
        position,
        position_side,
        entry_price,
        entry_raw_price,
        entry_time,
        entry_equity,
        entry_margin,
        entry_notional,
        entry_open_fee,
        exit_raw_price,
        execute_time,
    ):
        if position_side == 1:
            exit_price = exit_raw_price * (1 - self.slippage)

            raw_return = exit_raw_price / entry_raw_price - 1
            gross_pnl = entry_notional * raw_return
            gross_pnl_pct = gross_pnl / entry_equity * 100 if entry_equity else 0.0

            net_pnl_before_close_fee = position * (exit_price - entry_price)
            close_notional = abs(position * exit_price)
            close_fee = close_notional * self.fee_rate

            cash_after = cash + net_pnl_before_close_fee - close_fee

        elif position_side == -1:
            exit_price = exit_raw_price * (1 + self.slippage)

            raw_return = (entry_raw_price - exit_raw_price) / entry_raw_price
            gross_pnl = entry_notional * raw_return
            gross_pnl_pct = gross_pnl / entry_equity * 100 if entry_equity else 0.0

            net_pnl_before_close_fee = position * (entry_price - exit_price)
            close_notional = abs(position * exit_price)
            close_fee = close_notional * self.fee_rate

            cash_after = cash + net_pnl_before_close_fee - close_fee

        else:
            raise ValueError("未知仓位方向")

        net_pnl = cash_after - entry_equity
        net_pnl_pct = net_pnl / entry_equity * 100 if entry_equity else 0.0
        holding_hours = self._calculate_holding_hours(entry_time, execute_time)

        trade = {
            "entry_time": str(entry_time),
            "exit_time": str(execute_time),
            "side": "long" if position_side == 1 else "short",
            "exit_reason": "signal",

            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "entry_raw_price": float(entry_raw_price),
            "exit_raw_price": float(exit_raw_price),

            "leverage": int(self.leverage),
            "position_size": float(self.position_size),
            "entry_margin": float(entry_margin),
            "entry_notional": float(entry_notional),
            "open_fee": float(entry_open_fee),
            "close_fee": float(close_fee),

            "gross_pnl": float(gross_pnl),
            "gross_pnl_pct": float(gross_pnl_pct),

            "pnl": float(net_pnl),
            "pnl_pct": float(net_pnl_pct),
            "net_pnl": float(net_pnl),
            "net_pnl_pct": float(net_pnl_pct),

            "holding_hours": float(holding_hours),
            "equity_after": float(cash_after),
            "liquidated": False,
        }

        return {
            "cash_after": float(cash_after),
            "trade": trade,
        }

    def _calculate_mtm_equity(
        self,
        cash,
        position,
        position_side,
        entry_price,
        high_price,
        low_price,
        close_price,
    ):
        if position_side == 0:
            return {
                "equity_close": float(cash),
                "equity_at_high": float(cash),
                "equity_at_low": float(cash),
                "equity_worst": float(cash),
                "equity_best": float(cash),
                "equity_intrabar_extreme": float(cash),
            }

        equity_close = self._equity_at_price(
            cash=cash,
            position=position,
            position_side=position_side,
            entry_price=entry_price,
            mark_price=close_price,
        )

        equity_at_high = self._equity_at_price(
            cash=cash,
            position=position,
            position_side=position_side,
            entry_price=entry_price,
            mark_price=high_price,
        )

        equity_at_low = self._equity_at_price(
            cash=cash,
            position=position,
            position_side=position_side,
            entry_price=entry_price,
            mark_price=low_price,
        )

        equity_worst = min(equity_at_high, equity_at_low, equity_close)
        equity_best = max(equity_at_high, equity_at_low, equity_close)

        if abs(equity_at_high - equity_close) >= abs(equity_at_low - equity_close):
            equity_intrabar_extreme = equity_at_high
        else:
            equity_intrabar_extreme = equity_at_low

        return {
            "equity_close": float(equity_close),
            "equity_at_high": float(equity_at_high),
            "equity_at_low": float(equity_at_low),
            "equity_worst": float(equity_worst),
            "equity_best": float(equity_best),
            "equity_intrabar_extreme": float(equity_intrabar_extreme),
        }

    def _equity_at_price(
        self,
        cash,
        position,
        position_side,
        entry_price,
        mark_price,
    ):
        if position_side == 1:
            unrealized_pnl = position * (mark_price - entry_price)
            return cash + unrealized_pnl

        if position_side == -1:
            unrealized_pnl = position * (entry_price - mark_price)
            return cash + unrealized_pnl

        return cash

    def _check_liquidation(
        self,
        current_time,
        cash,
        position,
        position_side,
        entry_price,
        entry_raw_price,
        entry_time,
        entry_equity,
        entry_margin,
        entry_notional,
        entry_open_fee,
        high_price,
        low_price,
    ):
        if position_side == 0 or position <= 0:
            return None

        liquidation_equity = entry_equity * self.maintenance_margin_rate

        if position_side == 1:
            liquidation_price = entry_price + (liquidation_equity - cash) / position

            if low_price <= liquidation_price:
                return {
                    "time": str(current_time),
                    "side": "long",
                    "entry_time": str(entry_time),
                    "entry_price": float(entry_price),
                    "entry_raw_price": float(entry_raw_price),
                    "liquidation_price": float(liquidation_price),
                    "trigger_price": float(low_price),
                    "trigger_field": "low",
                    "equity_before": float(entry_equity),
                    "equity_after": float(max(liquidation_equity, 0.0)),
                    "entry_margin": float(entry_margin),
                    "entry_notional": float(entry_notional),
                    "open_fee": float(entry_open_fee),
                    "liquidation_fee": float(abs(position * liquidation_price) * self.liquidation_fee_rate),
                    "leverage": int(self.leverage),
                    "position_size": float(self.position_size),
                    "maintenance_margin_rate": float(self.maintenance_margin_rate),
                }

        elif position_side == -1:
            liquidation_price = entry_price - (liquidation_equity - cash) / position

            if high_price >= liquidation_price:
                return {
                    "time": str(current_time),
                    "side": "short",
                    "entry_time": str(entry_time),
                    "entry_price": float(entry_price),
                    "entry_raw_price": float(entry_raw_price),
                    "liquidation_price": float(liquidation_price),
                    "trigger_price": float(high_price),
                    "trigger_field": "high",
                    "equity_before": float(entry_equity),
                    "equity_after": float(max(liquidation_equity, 0.0)),
                    "entry_margin": float(entry_margin),
                    "entry_notional": float(entry_notional),
                    "open_fee": float(entry_open_fee),
                    "liquidation_fee": float(abs(position * liquidation_price) * self.liquidation_fee_rate),
                    "leverage": int(self.leverage),
                    "position_size": float(self.position_size),
                    "maintenance_margin_rate": float(self.maintenance_margin_rate),
                }

        return None

    def _build_liquidation_trade(
        self,
        event,
        entry_time,
        position_side,
        entry_price,
        entry_raw_price,
        entry_equity,
        entry_margin,
        entry_notional,
        entry_open_fee,
        exit_time,
        exit_price,
        exit_raw_price,
        cash_after,
    ):
        if position_side == 1:
            raw_return = exit_raw_price / entry_raw_price - 1
        elif position_side == -1:
            raw_return = (entry_raw_price - exit_raw_price) / entry_raw_price
        else:
            raw_return = 0.0

        gross_pnl = entry_notional * raw_return
        gross_pnl_pct = gross_pnl / entry_equity * 100 if entry_equity else 0.0

        net_pnl = cash_after - entry_equity
        net_pnl_pct = net_pnl / entry_equity * 100 if entry_equity else 0.0

        holding_hours = self._calculate_holding_hours(entry_time, exit_time)

        return {
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "side": "long" if position_side == 1 else "short",
            "exit_reason": "liquidation",

            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "entry_raw_price": float(entry_raw_price),
            "exit_raw_price": float(exit_raw_price),

            "leverage": int(self.leverage),
            "position_size": float(self.position_size),
            "entry_margin": float(entry_margin),
            "entry_notional": float(entry_notional),
            "open_fee": float(entry_open_fee),
            "close_fee": float(event.get("liquidation_fee", 0.0)),
            "liquidation_fee": float(event.get("liquidation_fee", 0.0)),

            "gross_pnl": float(gross_pnl),
            "gross_pnl_pct": float(gross_pnl_pct),

            "pnl": float(net_pnl),
            "pnl_pct": float(net_pnl_pct),
            "net_pnl": float(net_pnl),
            "net_pnl_pct": float(net_pnl_pct),

            "holding_hours": float(holding_hours),
            "equity_after": float(cash_after),

            "liquidated": True,
            "liquidation_price": float(event["liquidation_price"]),
            "trigger_price": float(event["trigger_price"]),
            "trigger_field": event["trigger_field"],
        }

    def _calculate_holding_hours(self, entry_time, exit_time) -> float:
        if entry_time is None or exit_time is None:
            return 0.0

        entry_dt = pd.to_datetime(entry_time)
        exit_dt = pd.to_datetime(exit_time)

        return (exit_dt - entry_dt).total_seconds() / 3600

    def _max_consecutive_count(self, values, condition_func) -> int:
        max_count = 0
        current_count = 0

        for value in values:
            if condition_func(value):
                current_count += 1
                max_count = max(max_count, current_count)
            else:
                current_count = 0

        return max_count

    def _calculate_metrics(
        self,
        equity_curve,
        trades,
        initial_cash,
        final_equity,
    ):
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

        max_consecutive_wins = self._max_consecutive_count(
            net_pnls,
            lambda x: x > 0,
        )

        max_consecutive_losses = self._max_consecutive_count(
            net_pnls,
            lambda x: x < 0,
        )

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