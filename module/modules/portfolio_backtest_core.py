"""
组合策略回测引擎（契约 v2，见 STRATEGY_CONTRACT.md）。

strategy_func(data) 接收对齐的多标的 K 线面板：
    data = {symbol: DataFrame}   # 索引完全相同（data_panel.load_aligned_panel）

返回目标权重 DataFrame：
    index   = 与 K 线相同的时间索引
    columns = 标的
    值      = 目标权重，正=做多，负=做空
              权重 × 杠杆 = 目标敞口相对当前权益的比例

核心语义（与 v1 一致的「目标状态对账」，推广到连续与多资产）：
1. 权重在当前 K 线收盘确认，下一根 K 线开盘执行
2. 空仓 → 非零权重：必定开仓
3. 目标权重为 0：必定全平（不受阈值限制）
4. 其余调整：|目标权重 - 当前权重| > rebalance_threshold 才交易（防手续费磨损）
5. 权重持续维持意味着恒定杠杆：杠杆 >1 时浮盈会加仓、浮亏会减仓，
   这是「目标权重」语义的自然推论，已写入契约文档

记账模型（与 v1 相同的期货式账本）：
- cash = 初始资金 + Σ已实现盈亏 - Σ手续费
- 权益 = cash + Σ未实现盈亏
- 持仓 = 带符号数量（正多负空）+ 移动平均成本价

强平模型（v1 的多资产推广）：
- 盘中最坏情况 = 所有腿同时处于各自最不利极值（保守假设）
- 维持保证金 = maintenance_margin_rate × 名义价值（与最坏权益同价格口径估值）
- 最坏权益 ≤ 维持保证金时触发全仓强平
- 强平价格按 α 插值求解「权益恰好打到维持线」的价格向量；
  单资产时严格退化为 v1 的强平价公式（任意 maintenance_margin_rate）
"""

import math

import numpy as np
import pandas as pd

from module.modules.backtest_metrics import (
    attach_engine_metrics,
    calculate_holding_hours,
    calculate_metrics,
    normalize_engine_params,
)


class PortfolioBacktestCore:

    def __init__(
        self,
        strategy_func,
        initial_cash: float = 1000.0,
        fee_rate: float = 0.0,
        slippage: float = 0.0,
        leverage: int = 1,
        position_size: float = 1.0,
        rebalance_threshold: float = 0.01,
        enable_liquidation: bool = True,
        maintenance_margin_rate: float = 0.0,
        stop_on_liquidation: bool = True,
        liquidation_fee_rate: float = 0.0,
    ):
        self.strategy_func = strategy_func

        # 参数截断规则单源在 backtest_metrics，与单标的引擎完全一致
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

        self.rebalance_threshold = max(float(rebalance_threshold), 0.0)
        self.enable_liquidation = bool(enable_liquidation)
        self.stop_on_liquidation = bool(stop_on_liquidation)

    # =========================================================
    # 主流程
    # =========================================================

    def run(self, data: dict, funding_rates=None) -> dict:
        symbols, index = self._validate_data(data)
        funding_arr = self._prepare_funding_rates(funding_rates, symbols, index)

        # 浅拷贝即可防策略篡改：pandas 写时复制（CoW）下策略的任何写入
        # 只触发被改块的复制，原面板不受影响，深拷贝是纯浪费
        strategy_input = {s: df.copy(deep=False) for s, df in data.items()}
        raw_weights = self.strategy_func(strategy_input)
        weights = self._prepare_weights(raw_weights, symbols, index)

        opens = {s: data[s]["open"].to_numpy(dtype=float) for s in symbols}
        highs = {s: data[s]["high"].to_numpy(dtype=float) for s in symbols}
        lows = {s: data[s]["low"].to_numpy(dtype=float) for s in symbols}
        closes = {s: data[s]["close"].to_numpy(dtype=float) for s in symbols}
        weights_arr = {s: weights[s].to_numpy(dtype=float) for s in symbols}

        cash = self.initial_cash
        total_funding_cost = 0.0  # 累计资金费率净支出（正=净付出），契约 §10.8
        qty = {s: 0.0 for s in symbols}
        avg_entry = {s: None for s in symbols}
        avg_entry_raw = {s: None for s in symbols}  # raw 价移动平均成本，供 gross 口径
        last_close = {s: None for s in symbols}

        # 主循环每根 K 线要写多条曲线，时间字符串一次性向量化生成
        time_strs = index.astype(str).tolist()

        open_episodes = {s: None for s in symbols}
        episodes = []
        fills = []
        equity_curve = []
        realized_equity_curve = []
        exposure_curve = []
        liquidation_events = []

        liquidated = False

        # ---------- 内部辅助 ----------

        def finish_episode(s, exit_time, exit_price, exit_reason):
            ep = open_episodes[s]
            ep["exit_time"] = str(exit_time)
            ep["exit_price"] = float(exit_price)
            ep["exit_reason"] = exit_reason

            # funding（契约 §10.8）作为持有期现金流计入单笔 net_pnl，与 v1 的
            # net_pnl = equity_after - entry_equity（cash 已被 funding 扣减）口径
            # 一致；gross_pnl 仍为 raw 价差、不含 funding。funding_cf 默认 0 ⇒
            # 无 funding 时与原公式逐字节一致。
            funding_cf = ep.get("funding_cf", 0.0)
            pnl = ep["realized_pnl"] - ep["fees"] + funding_cf
            entry_equity = ep["entry_equity"]

            ep["pnl"] = float(pnl)
            ep["net_pnl"] = float(pnl)
            ep["funding_pnl"] = float(funding_cf)
            ep["pnl_pct"] = float(pnl / entry_equity * 100) if entry_equity else 0.0
            ep["net_pnl_pct"] = ep["pnl_pct"]
            # gross = raw 价格口径的纯价格盈亏（不含滑点与手续费），与 v1 同口径
            ep["gross_pnl"] = float(ep["realized_pnl_raw"])
            ep["gross_pnl_pct"] = (
                float(ep["realized_pnl_raw"] / entry_equity * 100) if entry_equity else 0.0
            )

            # 口径单源在 backtest_metrics，与 v1 引擎共用
            ep["holding_hours"] = float(
                calculate_holding_hours(ep["entry_time"], ep["exit_time"])
            )

            ep["liquidated"] = exit_reason == "liquidation"

            episodes.append(ep)
            open_episodes[s] = None

        def record_fill(s, execute_time, side, fill_qty_abs, fill_price, raw_price,
                        fee, realized, action):
            fills.append({
                "time": str(execute_time),
                "symbol": s,
                "side": side,
                "action": action,
                "qty": float(fill_qty_abs),
                "price": float(fill_price),
                "raw_price": float(raw_price),
                "fee": float(fee),
                "realized_pnl": float(realized),
            })

        def execute_trade(s, target_qty, raw_price, execute_time, decision_equity):
            nonlocal cash

            delta = target_qty - qty[s]

            if abs(delta) * raw_price < 1e-12:
                return

            if delta > 0:
                fill_price = raw_price * (1 + self.slippage)
                fill_side = "buy"
            else:
                fill_price = raw_price * (1 - self.slippage)
                fill_side = "sell"

            remaining = delta

            # 1) 先平掉与交易方向相反的持仓部分（可能全平或反手）
            if qty[s] != 0.0 and (delta > 0) != (qty[s] > 0):
                full_close = abs(delta) >= abs(qty[s])
                # 全平时取 -qty 使新持仓精确为 0，不依赖浮点吸零兜底
                closing_signed = -qty[s] if full_close else delta
                closing_abs = abs(closing_signed)

                # 实现盈亏 = 平仓量 × (成交价 - 平均成本) × 持仓方向
                # 空头（qty<0）时价格下跌为正收益
                direction = 1.0 if qty[s] > 0 else -1.0
                realized = closing_abs * (fill_price - avg_entry[s]) * direction
                realized_raw = closing_abs * (raw_price - avg_entry_raw[s]) * direction

                fee = closing_abs * fill_price * self.fee_rate
                cash += realized - fee

                ep = open_episodes[s]
                ep["realized_pnl"] += realized
                ep["realized_pnl_raw"] += realized_raw
                ep["fees"] += fee

                record_fill(s, execute_time, fill_side, closing_abs, fill_price,
                            raw_price, fee, realized, "close")

                new_qty = qty[s] + closing_signed
                qty[s] = 0.0 if abs(new_qty) < 1e-12 else new_qty
                remaining = delta - closing_signed

                if abs(remaining) * raw_price < 1e-12:
                    remaining = 0.0

                if qty[s] == 0.0:
                    avg_entry[s] = None
                    avg_entry_raw[s] = None
                    finish_episode(s, execute_time, fill_price, "signal")

            # 2) 开仓 / 同方向加仓
            if abs(remaining) * raw_price >= 1e-12:
                fee = abs(remaining) * fill_price * self.fee_rate
                cash -= fee

                if qty[s] == 0.0:
                    open_episodes[s] = {
                        "symbol": s,
                        "side": "long" if remaining > 0 else "short",
                        "entry_time": str(execute_time),
                        "entry_price": float(fill_price),
                        "entry_raw_price": float(raw_price),
                        "entry_equity": float(decision_equity),
                        "leverage": int(self.leverage),
                        "position_size": float(self.position_size),
                        "realized_pnl": 0.0,
                        "realized_pnl_raw": 0.0,
                        "fees": fee,
                        "funding_cf": 0.0,
                        "max_abs_qty": abs(remaining),
                    }
                    avg_entry[s] = fill_price
                    avg_entry_raw[s] = raw_price
                    qty[s] = remaining
                else:
                    total_abs = abs(qty[s]) + abs(remaining)
                    avg_entry[s] = (
                        abs(qty[s]) * avg_entry[s] + abs(remaining) * fill_price
                    ) / total_abs
                    avg_entry_raw[s] = (
                        abs(qty[s]) * avg_entry_raw[s] + abs(remaining) * raw_price
                    ) / total_abs
                    qty[s] = qty[s] + remaining

                    ep = open_episodes[s]
                    ep["fees"] += fee
                    ep["max_abs_qty"] = max(ep["max_abs_qty"], abs(qty[s]))

                record_fill(s, execute_time, fill_side, abs(remaining), fill_price,
                            raw_price, fee, 0.0, "open")

        def leg_extremes(s, i):
            """单腿盘中极值：缺 high/low 时回退 close；按持仓方向给出最不利/最有利价。"""
            close_v = last_close[s]
            high_v = highs[s][i]
            low_v = lows[s][i]

            if math.isnan(high_v):
                high_v = close_v
            if math.isnan(low_v):
                low_v = close_v

            if qty[s] > 0:
                return low_v, high_v
            return high_v, low_v

        def compute_mtm(i):
            unreal_close = 0.0
            unreal_worst = 0.0
            unreal_best = 0.0
            notional_entry = 0.0
            notional_adverse = 0.0

            for s in symbols:
                q = qty[s]
                if q == 0.0:
                    continue

                # 持仓中的标的必然有过有效价格
                close_v = last_close[s]
                entry = avg_entry[s]
                adverse, favorable = leg_extremes(s, i)

                unreal_close += q * (close_v - entry)
                unreal_worst += q * (adverse - entry)
                unreal_best += q * (favorable - entry)

                notional_entry += abs(q) * entry
                notional_adverse += abs(q) * adverse

            return {
                "equity_close": cash + unreal_close,
                "equity_worst": cash + unreal_worst,
                "equity_best": cash + unreal_best,
                "unreal_worst": unreal_worst,
                "notional_entry": notional_entry,
                "notional_adverse": notional_adverse,
            }

        # ---------- 主循环 ----------

        for i in range(len(index)):
            ts = time_strs[i]

            for s in symbols:
                c = closes[s][i]
                if not math.isnan(c):
                    last_close[s] = float(c)

            # ---------- 资金费率结算（契约 §10.8）----------
            # 在 MTM 与强平检测之前按持仓名义价值扣/加 cash，使 funding 拖低
            # 权益后能自然参与本根强平判定。funding_arr 为 None 时整段跳过，
            # 逐根行为与无 funding 完全一致（金样例零影响的硬门槛）。
            if funding_arr is not None:
                for s in symbols:
                    q = qty[s]
                    if q == 0.0 or last_close[s] is None:
                        continue
                    rate_i = funding_arr[s][i]
                    if rate_i == 0.0:
                        continue
                    # 正费率：多头付（cash 减）、空头收（cash 增）
                    funding_cf = -q * last_close[s] * rate_i
                    cash += funding_cf
                    total_funding_cost -= funding_cf
                    ep = open_episodes[s]
                    if ep is not None:
                        ep["funding_cf"] += funding_cf

            mtm = compute_mtm(i)
            equity_close = mtm["equity_close"]
            equity_worst = mtm["equity_worst"]
            equity_best = mtm["equity_best"]

            has_position = any(qty[s] != 0.0 for s in symbols)

            # ---------- 强平检测（保守：所有腿同时处于最不利极值） ----------
            if self.enable_liquidation and has_position:
                # 维持保证金 = rate × 名义价值，与最坏权益同用最不利价格估值，
                # 单资产时与 v1 的强平价公式严格等价（任意 rate）
                maintenance = self.maintenance_margin_rate * mtm["notional_adverse"]

                if equity_worst <= maintenance:
                    # α 插值求「权益恰好打到维持线」的价格向量，两侧均沿
                    # p(α) = entry + α·(adverse - entry) 线性变化：
                    #   cash + α·unreal_worst
                    #     = rate·(notional_entry + α·(notional_adverse - notional_entry))
                    denom = mtm["unreal_worst"] - self.maintenance_margin_rate * (
                        mtm["notional_adverse"] - mtm["notional_entry"]
                    )
                    if denom < -1e-12:
                        alpha = (
                            self.maintenance_margin_rate * mtm["notional_entry"] - cash
                        ) / denom
                        alpha = min(max(alpha, 0.0), 1.0)
                    else:
                        # denom ≥ 0 时「权益 − 维持线」沿 α 不降，而触发条件
                        # 保证 α=1 处已 ≤ 0 ⇒ α=0 处同样 ≤ 0：首次触线在
                        # 入场价位，按成本价清算（取 1.0 会凭空多算一整根
                        # K 线的最不利亏损）
                        alpha = 0.0

                    event_legs = []
                    liq_fee_total = 0.0

                    for s in symbols:
                        q = qty[s]
                        if q == 0.0:
                            continue

                        adverse, _ = leg_extremes(s, i)
                        liq_price = avg_entry[s] + (adverse - avg_entry[s]) * alpha

                        # 跳空越过强平价时按本根开盘价结算（与 v1 同规则）：
                        # 插值价在该根从未成交过，按它结算会凭空回收权益
                        open_v = opens[s][i]
                        if not math.isnan(open_v):
                            if q > 0:
                                liq_price = min(liq_price, open_v)
                            else:
                                liq_price = max(liq_price, open_v)

                        realized = q * (liq_price - avg_entry[s])
                        liq_fee = abs(q) * liq_price * self.liquidation_fee_rate
                        liq_fee_total += liq_fee

                        cash += realized - liq_fee

                        ep = open_episodes[s]
                        ep["realized_pnl"] += realized
                        # 强平价没有滑点修饰，gross 口径用 raw 成本价
                        ep["realized_pnl_raw"] += q * (liq_price - avg_entry_raw[s])
                        ep["fees"] += liq_fee

                        record_fill(
                            s, ts, "sell" if q > 0 else "buy",
                            abs(q), liq_price, liq_price, liq_fee, realized,
                            "liquidation",
                        )

                        event_legs.append({
                            "symbol": s,
                            "side": "long" if q > 0 else "short",
                            "qty": float(abs(q)),
                            "entry_price": float(avg_entry[s]),
                            "liquidation_price": float(liq_price),
                            "trigger_price": float(adverse),
                        })

                        qty[s] = 0.0
                        avg_entry[s] = None
                        avg_entry_raw[s] = None
                        finish_episode(s, ts, liq_price, "liquidation")

                    cash = max(cash, 0.0)

                    liquidation_events.append({
                        "time": ts,
                        "equity_before": float(equity_close),
                        "equity_after": float(cash),
                        "maintenance": float(maintenance),
                        "liquidation_fee": float(liq_fee_total),
                        "legs": event_legs,
                        "leverage": int(self.leverage),
                        "maintenance_margin_rate": float(self.maintenance_margin_rate),
                    })

                    equity_curve.append({
                        "time": ts,
                        "equity": float(cash),
                        "equity_close": float(cash),
                        # 全部腿已在强平价结算：账户盘中真实极值就是结算后
                        # 权益。worst 用虚拟极值会报不可能回撤；best 用虚拟
                        # 有利极值会造从未实现的幽灵峰抬高后续回撤分母——
                        # 两者都落到结算后 cash
                        "equity_worst": float(cash),
                        "equity_best": float(cash),
                        "liquidated": True,
                    })

                    realized_equity_curve.append({
                        "time": ts,
                        "equity": float(cash),
                    })

                    exposure_curve.append({
                        "time": ts,
                        **{s: 0.0 for s in symbols},
                    })

                    liquidated = True

                    if self.stop_on_liquidation:
                        break

                    continue

            # ---------- 正常权益记录 ----------

            # 图表用 equity 取离 close 偏离最大的盘中极值（插针可见）。
            # 平局取 worst（保守）；v1 平局取 at_high，两者仅在上下影
            # 完全对称的 K 线上方向不同，只影响图表显示、不影响结算数字
            if abs(equity_worst - equity_close) >= abs(equity_best - equity_close):
                equity_extreme = equity_worst
            else:
                equity_extreme = equity_best

            equity_curve.append({
                "time": ts,
                "equity": float(equity_extreme),
                "equity_close": float(equity_close),
                "equity_worst": float(equity_worst),
                "equity_best": float(equity_best),
                "liquidated": False,
            })

            realized_equity_curve.append({
                "time": ts,
                "equity": float(cash),
            })

            exposure_curve.append({
                "time": ts,
                **{
                    s: (
                        float(qty[s] * last_close[s] / equity_close)
                        if qty[s] != 0.0 and equity_close > 0
                        else 0.0
                    )
                    for s in symbols
                },
            })

            # ---------- 最后一根 K 线只结算不交易 ----------
            if i + 1 >= len(index):
                break

            decision_equity = equity_close
            execute_time = time_strs[i + 1]

            # ---------- 按目标权重对账 ----------
            for s in symbols:
                target_w = weights_arr[s][i]
                raw_open = opens[s][i + 1]

                # 下一根开盘价缺失（未上市/缺数据）：本根无法交易，之后自动重试
                if math.isnan(raw_open):
                    continue

                if target_w == 0.0:
                    if qty[s] == 0.0:
                        continue
                    # 目标空仓：必定全平，不受阈值限制（权益 ≤ 0 时同样允许，
                    # 与 v1 一致——破产后只禁止开仓/调仓，不能禁止止损离场）
                    execute_trade(s, 0.0, raw_open, execute_time, decision_equity)
                    continue

                # 权益 ≤ 0：无法定义目标名义，跳过开仓与调仓（上面的全平不受影响）
                if decision_equity <= 0:
                    continue

                if qty[s] != 0.0:
                    # 持仓调整：当前权重按决策时点（收盘价）估值，与决策权益同口径；
                    # 空仓 → 非零目标则必定开仓，不受阈值限制
                    current_w = qty[s] * last_close[s] / (decision_equity * self.leverage)
                    if abs(target_w - current_w) <= self.rebalance_threshold:
                        continue

                # 目标数量按预期成交价（含滑点）换算，与 v1 的
                # position = notional / entry_price 同口径；若用 raw_open 换算，
                # 成交后的名义敞口会系统性超出目标 (1±slippage) 倍
                target_notional = target_w * decision_equity * self.leverage
                provisional_delta = target_notional / raw_open - qty[s]
                if provisional_delta > 0:
                    expected_fill = raw_open * (1 + self.slippage)
                else:
                    expected_fill = raw_open * (1 - self.slippage)
                target_qty = target_notional / expected_fill

                # 滑点修正后交易方向反转（乘积 < 0）或目标恰好等于当前持仓
                # （乘积 = 0，调整量纯属滑点伪影）：放弃本次调整
                if (target_qty - qty[s]) * provisional_delta <= 0:
                    continue

                execute_trade(s, target_qty, raw_open, execute_time, decision_equity)

        # ---------- 末尾持仓虚拟结算 ----------
        # 回测结束时仍持仓的标的：以最后有效收盘价虚拟结算，
        # 计入交易统计（exit_reason="end_of_data"）。
        # 不产生成交记录、不改变现金与权益曲线。

        if equity_curve:
            last_time = equity_curve[-1]["time"]

            for s in symbols:
                ep = open_episodes[s]
                if ep is None or qty[s] == 0.0:
                    continue

                close_v = last_close[s]
                ep["realized_pnl"] += qty[s] * (close_v - avg_entry[s])
                ep["realized_pnl_raw"] += qty[s] * (close_v - avg_entry_raw[s])
                finish_episode(s, last_time, close_v, "end_of_data")

        # ---------- 指标 ----------

        final_equity = (
            equity_curve[-1]["equity_close"] if equity_curve else self.initial_cash
        )

        metrics = calculate_metrics(
            equity_curve=equity_curve,
            trades=episodes,
            initial_cash=self.initial_cash,
            final_equity=final_equity,
        )

        attach_engine_metrics(
            metrics, self, len(liquidation_events), liquidated,
            rebalance_threshold=self.rebalance_threshold,
            fill_count=len(fills),
            symbols=list(symbols),
            total_funding_cost=float(total_funding_cost),
        )

        return {
            "data": data,
            "raw_weights": raw_weights,   # 策略原始返回值（未经 _prepare_weights）
            "weights": weights,
            "trades": episodes,
            "fills": fills,
            "equity_curve": equity_curve,
            "realized_equity_curve": realized_equity_curve,
            "exposure_curve": exposure_curve,
            "liquidation_events": liquidation_events,
            "metrics": metrics,
        }

    # =========================================================
    # 输入校验
    # =========================================================

    def _validate_data(self, data):
        if not isinstance(data, dict) or len(data) == 0:
            raise ValueError("data 必须是非空 dict: {symbol: DataFrame}")

        required_columns = ["open", "high", "low", "close"]
        index = None

        for symbol, df in data.items():
            missing = [c for c in required_columns if c not in df.columns]
            if missing:
                raise ValueError(f"{symbol} 的 K 线缺少必要字段: {missing}")

            # 引擎依赖「同一行 OHLC 要么全有效、要么全 NaN（未上市/缺数据）」
            # 的不变式：行内混合缺失会让持仓估值取到 None 而深处崩溃，
            # 必须在入口报清楚
            valid = df[required_columns].notna()
            mixed = valid.any(axis=1) & ~valid.all(axis=1)
            if bool(mixed.any()):
                raise ValueError(
                    f"{symbol} 的 K 线存在 OHLC 部分缺失的行（如 open 有值但 "
                    "close 为 NaN）：同一行要么全部有效、要么全部为 NaN，"
                    "请使用 data_panel.load_aligned_panel 加载数据"
                )

            if index is None:
                index = df.index
            elif not df.index.equals(index):
                raise ValueError(
                    "所有标的的索引必须完全对齐，"
                    "请使用 data_panel.load_aligned_panel 加载数据"
                )

        return list(data.keys()), index

    def _prepare_funding_rates(self, funding_rates, symbols, index):
        """把可选的 per-symbol 资金费率序列归一化为 {symbol: np.array(len(index))}。

        funding_rates[s] 是「每根 K 线的资金费率」（已由数据层从真实 8h 序列
        连续摊销并对齐到 K 线索引，见契约 §10.8），正费率多头付空头收。
        None 表示不计资金费率，引擎逐根行为与无 funding 完全一致（金样例
        零影响的硬门槛）。缺失的标的按 0 处理；NaN 视为该根无费率（0）。
        """
        if funding_rates is None:
            return None
        if not isinstance(funding_rates, dict):
            raise ValueError(
                "funding_rates 必须是 {symbol: 每根费率序列} 的 dict，或 None"
            )

        n = len(index)
        out = {}
        for s in symbols:
            series = funding_rates.get(s)
            if series is None:
                out[s] = np.zeros(n, dtype=float)
                continue
            if isinstance(series, pd.Series):
                # tz 容错：直连 API 可能传 tz-aware 索引，而 funding 序列已被
                # data_panel 剥成 tz-naive——标签不匹配会让 reindex 全返回 NaN →
                # funding 被静默清零（round-10）。两侧统一剥成 tz-naive UTC 再
                # reindex；两侧本就 tz-naive 时此分支不触发，行为逐根不变。
                target = index
                if getattr(series.index, "tz", None) is not None:
                    series = series.set_axis(
                        series.index.tz_convert("UTC").tz_localize(None)
                    )
                if getattr(target, "tz", None) is not None:
                    target = target.tz_convert("UTC").tz_localize(None)
                arr = series.reindex(target).to_numpy(dtype=float)
            else:
                arr = np.asarray(series, dtype=float)
            if arr.shape[0] != n:
                raise ValueError(
                    f"{s} 的 funding_rates 长度 {arr.shape[0]} 与 K 线根数 {n} 不一致"
                )
            out[s] = np.nan_to_num(arr, nan=0.0)
        return out

    def _prepare_weights(self, raw_weights, symbols, index):
        if not isinstance(raw_weights, pd.DataFrame):
            raise ValueError(
                "策略必须返回目标权重 DataFrame（index=时间, columns=标的）"
            )

        extra = [c for c in raw_weights.columns if c not in symbols]
        if extra:
            raise ValueError(f"权重包含数据面板之外的标的: {extra}")

        # 缺列与多列同样是契约违例（columns 必须等于 SYMBOLS）：
        # 静默补 0 会把对冲组合无声变成单边裸仓，必须报错
        missing = [s for s in symbols if s not in raw_weights.columns]
        if missing:
            raise ValueError(
                f"权重缺少 SYMBOLS 中声明的标的列: {missing}，"
                "策略必须为每个声明的标的给出权重列（无意见的行可填 NaN）"
            )

        # 索引与面板完全不重叠（整数索引、reset_index 后返回、时间整体偏移）
        # 会让下面的 ffill 无源、全表填 0，静默产出一份「0 笔交易」的正常
        # 报告——这类错误必须报出来而不是吞掉
        if raw_weights.index.intersection(index).empty:
            raise ValueError(
                "权重 DataFrame 的索引与 K 线时间索引完全不重叠，"
                "请用数据面板的时间索引构造权重（如 data[SYMBOLS[0]].index）"
            )

        # 行侧索引重复（同一时间多行权重）与缺列/多列/索引不重叠同属契约违例：
        # 下面的 reindex(index=index) 在重复标签上会抛 pandas 原生
        # "cannot reindex on an axis with duplicate labels"，经 webUI catch-all
        # 显示为无法自查的内部错误，必须在此主动拦成契约级中文提示
        if not raw_weights.index.is_unique:
            raise ValueError(
                "权重 DataFrame 的时间索引存在重复，请在返回前去重，"
                '如 df[~df.index.duplicated(keep="last")]'
            )

        weights = raw_weights.reindex(columns=symbols)
        weights = weights.apply(pd.to_numeric, errors="coerce")

        # 策略明确返回的行：NaN 权重视为 0（契约语义）
        weights = weights.fillna(0.0)

        # 策略未返回的行（索引缺失，如策略做了 dropna）：视为「无意见」，
        # 延续上一行的目标权重。否则缺行会被解释成「目标空仓→强制全平」，
        # 造成平仓-开仓循环的手续费磨损，回测结果系统性偏差且无告警。
        weights = weights.reindex(index=index).ffill().fillna(0.0)

        weights = weights.clip(-1.0, 1.0)

        # 总敞口（|权重|之和）> 1 时整行按比例缩放：确定性地容错 AI 输出
        gross = weights.abs().sum(axis=1)
        over = gross > 1.0
        if over.any():
            weights.loc[over] = weights.loc[over].div(gross[over], axis=0)

        # position_size 作为全局敞口缩放系数
        weights = weights * self.position_size

        return weights
