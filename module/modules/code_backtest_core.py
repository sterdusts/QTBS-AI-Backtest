import pandas as pd
import numpy as np

from module.modules.backtest_metrics import (
    attach_engine_metrics,
    calculate_holding_hours,
    calculate_metrics,
    normalize_engine_params,
)


class CodeBacktestCore:
    """
    代码策略回测核心。

    执行语义与 AI↔引擎契约见项目根目录 STRATEGY_CONTRACT.md。
    修改本引擎的执行语义，必须同步契约文档与 tests/ 金样例测试。

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

        # 参数截断规则单源在 backtest_metrics，与组合引擎完全一致
        params = normalize_engine_params(
            initial_cash, fee_rate, slippage, leverage,
            position_size, maintenance_margin_rate, liquidation_fee_rate,
        )
        self.initial_cash = params["initial_cash"]
        self.fee_rate = params["fee_rate"]
        self.slippage = params["slippage"]
        self.leverage = params["leverage"]
        self.position_size = params["position_size"]
        self.maintenance_margin_rate = params["maintenance_margin_rate"]
        self.liquidation_fee_rate = params["liquidation_fee_rate"]

        self.enable_liquidation = bool(enable_liquidation)
        self.stop_on_liquidation = bool(stop_on_liquidation)

    def run(self, df: pd.DataFrame) -> dict:
        # 浅拷贝即可防策略篡改：pandas 写时复制下任何写入只复制被改的块
        df = df.copy(deep=False)

        try:
            df = self.strategy_func(df)
        except KeyError as e:
            # v2 策略（按标的名访问数据面板）被错误路由进 v1 时最典型的炸点。
            # 只有缺的键长得像交易对时才提示版本声明，避免把策略自身的
            # 普通 KeyError（dict 键拼错等）错误归因到契约路由上
            key = e.args[0] if e.args else None
            if isinstance(key, str) and key.upper().endswith("USDT"):
                raise ValueError(
                    f"策略函数按标的名取数据失败（KeyError: {e}）。"
                    "多标的策略（契约 v2）必须声明 CONTRACT_VERSION = 2，"
                    "否则会被按单标的 v1 路由。"
                ) from e
            raise ValueError(
                f"策略代码访问了不存在的键/列（KeyError: {e}），"
                "请检查策略引用的列名或字典键是否存在。"
            ) from e

        if "target_position" not in df.columns:
            raise ValueError("策略函数必须生成 target_position 字段")

        required_columns = ["open", "high", "low", "close"]
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"回测数据缺少必要字段: {missing_columns}")

        # 合法值校验必须在整数转换之前：astype(int) 会把 0.5 向零截断成
        # 合法的 0、把 inf 炸成晦涩的 pandas 转换错误，校验就形同虚设
        raw_target = df["target_position"].fillna(0)
        invalid_mask = ~raw_target.isin([-1, 0, 1])
        if invalid_mask.any():
            # key=repr：非法值可能混合类型（如 '1' 与 0.5），裸 sorted 会
            # 抛 TypeError，把契约报错变成 Python 内部错误
            invalid_values = sorted(set(raw_target[invalid_mask].tolist()), key=repr)
            raise ValueError(
                f"target_position 只能是 -1, 0, 1，发现非法值: {invalid_values}"
            )

        df["target_position"] = raw_target.astype(int)

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

        for i in range(len(df)):
            current_time = df.index[i]
            current_target = int(df["target_position"].iloc[i])

            high_price = float(df["high"].iloc[i])
            low_price = float(df["low"].iloc[i])
            close_price = float(df["close"].iloc[i])

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
                # open_price 只有强平分支用到：空仓/关闭强平时不必每根取值
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
                    open_price=float(df["open"].iloc[i]),
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
                    position=position,
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
                    # 仓位在强平价已被关闭：账户盘中真实极值就是结算后
                    # 权益。worst 用虚拟极值会报 < -100% 的不可能回撤；
                    # best 用虚拟有利极值会造一个仓位从未实现的幽灵峰，
                    # 抬高后续回撤分母——两者都必须落到结算后 cash
                    "equity_worst": float(cash),
                    "equity_best": float(cash),
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

            # 最后一根 K 线没有下一根开盘价，只做权益结算，不再执行交易
            if i + 1 >= len(df):
                break

            # 目标仓位与实际持仓一致时无需交易。
            # 与实际持仓比较（而不是与上一根信号比较），可以正确处理：
            # 1. 首根 K 线即有信号的情况（旧逻辑永远不会开仓）
            # 2. 开仓失败（资金不足等）后在后续 K 线重试
            if current_target == position_side:
                continue

            next_open = float(df["open"].iloc[i + 1])
            execute_time = df.index[i + 1]
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

            # target 合法性已在 run() 入口统一校验，此处只需区分开仓与否；
            # 多空开仓除 side 外完全一致，合并为单一路径避免两份记账漂移
            if current_target != 0:
                open_result = self._open_position(
                    cash=cash,
                    side=current_target,
                    raw_price=next_open,
                    execute_time=execute_time,
                )

                if open_result is not None:
                    cash = open_result["cash_after"]
                    position = open_result["position"]
                    position_side = current_target
                    entry_price = open_result["entry_price"]
                    entry_raw_price = open_result["entry_raw_price"]
                    entry_time = open_result["entry_time"]
                    entry_equity = open_result["entry_equity"]
                    entry_margin = open_result["entry_margin"]
                    entry_notional = open_result["entry_notional"]
                    entry_open_fee = open_result["entry_open_fee"]

        # 回测结束时仍持仓：以最后收盘价虚拟结算，计入交易统计。
        # 不产生真实成交、不改变现金与权益曲线，只是让 trade_count / 胜率
        # 不再漏掉这笔仓位（否则会出现「0 笔交易却有收益」的自相矛盾报告）。
        if position_side != 0 and len(df) > 0:
            last_time = df.index[-1]
            last_close = float(df["close"].iloc[-1])

            equity_now = self._equity_at_price(
                cash=cash,
                position=position,
                position_side=position_side,
                entry_price=entry_price,
                mark_price=last_close,
            )

            trades.append(self._build_trade(
                exit_reason="end_of_data",
                position_side=position_side,
                position=position,
                entry_time=entry_time,
                exit_time=last_time,
                entry_price=entry_price,
                exit_price=last_close,
                entry_raw_price=entry_raw_price,
                exit_raw_price=last_close,
                entry_equity=entry_equity,
                entry_margin=entry_margin,
                entry_notional=entry_notional,
                entry_open_fee=entry_open_fee,
                close_fee=0.0,
                equity_after=equity_now,
            ))

        final_equity = equity_curve[-1]["equity_close"] if equity_curve else self.initial_cash

        metrics = self._calculate_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_cash=self.initial_cash,
            final_equity=final_equity,
        )

        attach_engine_metrics(metrics, self, len(liquidation_events), liquidated)

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
            net_pnl_before_close_fee = position * (exit_price - entry_price)
        elif position_side == -1:
            exit_price = exit_raw_price * (1 + self.slippage)
            net_pnl_before_close_fee = position * (entry_price - exit_price)
        else:
            raise ValueError("未知仓位方向")

        close_fee = abs(position * exit_price) * self.fee_rate
        cash_after = cash + net_pnl_before_close_fee - close_fee

        trade = self._build_trade(
            exit_reason="signal",
            position_side=position_side,
            position=position,
            entry_time=entry_time,
            exit_time=execute_time,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_raw_price=entry_raw_price,
            exit_raw_price=exit_raw_price,
            entry_equity=entry_equity,
            entry_margin=entry_margin,
            entry_notional=entry_notional,
            entry_open_fee=entry_open_fee,
            close_fee=close_fee,
            equity_after=cash_after,
        )

        return {
            "cash_after": float(cash_after),
            "trade": trade,
        }

    def _build_trade(
        self,
        exit_reason,
        position_side,
        position,
        entry_time,
        exit_time,
        entry_price,
        exit_price,
        entry_raw_price,
        exit_raw_price,
        entry_equity,
        entry_margin,
        entry_notional,
        entry_open_fee,
        close_fee,
        equity_after,
        extra=None,
    ):
        """
        统一的成交记录构造器：signal / liquidation / end_of_data 三种收尾
        共用同一字段集与同一 gross/net 口径，杜绝三处手写副本各自漂移。

        gross = raw 价格口径的纯价格盈亏（不含滑点与手续费），与组合引擎同口径。
        """

        if position_side == 1:
            gross_pnl = position * (exit_raw_price - entry_raw_price)
        else:
            gross_pnl = position * (entry_raw_price - exit_raw_price)

        gross_pnl_pct = gross_pnl / entry_equity * 100 if entry_equity else 0.0

        net_pnl = equity_after - entry_equity
        net_pnl_pct = net_pnl / entry_equity * 100 if entry_equity else 0.0

        trade = {
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "side": "long" if position_side == 1 else "short",
            "exit_reason": exit_reason,

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

            "holding_hours": float(self._calculate_holding_hours(entry_time, exit_time)),
            "equity_after": float(equity_after),
            "liquidated": exit_reason == "liquidation",
        }

        if extra:
            trade.update(extra)

        return trade

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
        open_price,
        high_price,
        low_price,
    ):
        if position_side == 0 or position <= 0:
            return None

        # 维持保证金 = rate × 当前名义价值（交易所惯例口径，与组合引擎一致）。
        # 强平价是 equity(p) = rate × position × p 的解；
        # rate = 0 时退化为旧公式 entry ∓ cash / position
        rate = self.maintenance_margin_rate

        if position_side == 1:
            liquidation_price = (position * entry_price - cash) / (position * (1.0 - rate))
            triggered = low_price <= liquidation_price
            trigger_price, trigger_field = low_price, "low"
            # 跳空越过强平价时按本根开盘价结算：理论强平价在该根
            # 从未成交过，按它结算会凭空回收市场没给过的权益
            fill_price = min(liquidation_price, open_price)
        else:
            liquidation_price = (position * entry_price + cash) / (position * (1.0 + rate))
            triggered = high_price >= liquidation_price
            trigger_price, trigger_field = high_price, "high"
            fill_price = max(liquidation_price, open_price)

        if not triggered:
            return None

        # 结算后权益 = 按实际成交价的盯市权益（非跳空时恰好等于维持线）
        liquidation_equity = self._equity_at_price(
            cash=cash,
            position=position,
            position_side=position_side,
            entry_price=entry_price,
            mark_price=fill_price,
        )

        return {
            "time": str(current_time),
            "side": "long" if position_side == 1 else "short",
            "entry_time": str(entry_time),
            "entry_price": float(entry_price),
            "entry_raw_price": float(entry_raw_price),
            "liquidation_price": float(fill_price),
            "trigger_price": float(trigger_price),
            "trigger_field": trigger_field,
            "equity_before": float(entry_equity),
            "equity_after": float(max(liquidation_equity, 0.0)),
            "entry_margin": float(entry_margin),
            "entry_notional": float(entry_notional),
            "open_fee": float(entry_open_fee),
            "liquidation_fee": float(abs(position * fill_price) * self.liquidation_fee_rate),
            "leverage": int(self.leverage),
            "position_size": float(self.position_size),
            "maintenance_margin_rate": float(self.maintenance_margin_rate),
        }

    def _build_liquidation_trade(
        self,
        event,
        entry_time,
        position_side,
        position,
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
        return self._build_trade(
            exit_reason="liquidation",
            position_side=position_side,
            position=position,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_raw_price=entry_raw_price,
            exit_raw_price=exit_raw_price,
            entry_equity=entry_equity,
            entry_margin=entry_margin,
            entry_notional=entry_notional,
            entry_open_fee=entry_open_fee,
            close_fee=float(event.get("liquidation_fee", 0.0)),
            equity_after=cash_after,
            extra={
                "liquidation_fee": float(event.get("liquidation_fee", 0.0)),
                "liquidation_price": float(event["liquidation_price"]),
                "trigger_price": float(event["trigger_price"]),
                "trigger_field": event["trigger_field"],
            },
        )

    def _calculate_holding_hours(self, entry_time, exit_time) -> float:
        # 口径单源在 backtest_metrics，与组合引擎共用
        return calculate_holding_hours(entry_time, exit_time)

    def _calculate_metrics(
        self,
        equity_curve,
        trades,
        initial_cash,
        final_equity,
    ):
        # 指标计算已抽取到 backtest_metrics.py，与组合引擎共用
        return calculate_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_cash=initial_cash,
            final_equity=final_equity,
        )