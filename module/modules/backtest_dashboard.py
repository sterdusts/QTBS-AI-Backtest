"""
回测结果可视化仪表盘（输出台用）：把 metrics + trades 渲染成带样式的 HTML 卡片——
大字总盈亏 + 权益迷你曲线(内联 SVG) + 关键指标行 + 指标卡（胜率/多空比/总交易数/盈亏比，
各带彩条）。纯函数、返回自带 <style> 的自包含 HTML（gr.HTML 直接吃），便于单测。

指标标签复用 webUI 的 SUMMARY_TEXTS（经 summary_text 传入，不重复翻译）；仪表盘特有的
少量标签（总盈亏/多空比/总费用/盈/亏/多/空 等）在本模块内置六语言。
"""

import html as _html

# 仪表盘特有标签（SUMMARY_TEXTS 没有的），六语言
_DASH = {
    "zh": {"total_pnl": "总盈亏", "long_short": "多空比", "total_fee": "总费用",
           "win": "盈利", "loss": "亏损", "long": "多", "short": "空", "trades_unit": "笔交易",
           "placeholder": "运行回测后，结果将在此可视化展示", "na": "—"},
    "en": {"total_pnl": "Total P&L", "long_short": "Long/Short", "total_fee": "Total Fees",
           "win": "Win", "loss": "Loss", "long": "L", "short": "S", "trades_unit": "trades",
           "placeholder": "Run a backtest to see the visual results here", "na": "—"},
    "ko": {"total_pnl": "총 손익", "long_short": "롱/숏", "total_fee": "총 수수료",
           "win": "수익", "loss": "손실", "long": "롱", "short": "숏", "trades_unit": "건",
           "placeholder": "백테스트를 실행하면 여기에 시각화됩니다", "na": "—"},
    "ja": {"total_pnl": "総損益", "long_short": "ロング/ショート", "total_fee": "総手数料",
           "win": "勝ち", "loss": "負け", "long": "買", "short": "売", "trades_unit": "回",
           "placeholder": "バックテストを実行すると結果がここに表示されます", "na": "—"},
    "ar": {"total_pnl": "إجمالي الربح/الخسارة", "long_short": "شراء/بيع", "total_fee": "إجمالي الرسوم",
           "win": "ربح", "loss": "خسارة", "long": "شراء", "short": "بيع", "trades_unit": "صفقة",
           "placeholder": "شغّل اختبارًا خلفيًا لعرض النتائج هنا", "na": "—"},
    "ru": {"total_pnl": "Общий P&L", "long_short": "Лонг/Шорт", "total_fee": "Сумма комиссий",
           "win": "Приб.", "loss": "Убыт.", "long": "Л", "short": "Ш", "trades_unit": "сделок",
           "placeholder": "Запустите бэктест, чтобы увидеть результаты", "na": "—"},
}

_GREEN = "#16c784"
_RED = "#ea3943"

_STYLE = """
<style>
.qtbs-dash{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a2e;}
.qtbs-dash .qd-head{font-size:13px;color:#6b7280;margin-bottom:6px;}
.qtbs-dash .qd-pnl{font-size:34px;font-weight:800;line-height:1.1;}
.qtbs-dash .qd-pnl-abs{font-size:16px;font-weight:600;margin-top:2px;}
.qtbs-dash .qd-spark{margin:10px 0 12px;}
.qtbs-dash .qd-row{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-top:1px solid #eef0f4;font-size:14px;}
.qtbs-dash .qd-row .ql{color:#6b7280;}
.qtbs-dash .qd-row .qv{font-weight:700;}
.qtbs-dash .qd-cards{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;}
.qtbs-dash .qd-card{background:#f7f8fa;border-radius:12px;padding:12px 14px;}
.qtbs-dash .qd-card .qc-t{font-size:12px;color:#6b7280;}
.qtbs-dash .qd-card .qc-v{font-size:22px;font-weight:800;margin:2px 0 1px;}
.qtbs-dash .qd-card .qc-sub{font-size:11px;color:#9aa0ab;}
.qtbs-dash .qd-bar{height:6px;border-radius:3px;background:#ea3943;margin-top:8px;overflow:hidden;}
.qtbs-dash .qd-bar > i{display:block;height:100%;background:#16c784;border-radius:3px;}
.qtbs-dash .qd-foot{margin-top:10px;font-size:12px;color:#9aa0ab;}
.qtbs-dash .qd-empty{padding:36px 12px;text-align:center;color:#9aa0ab;font-size:14px;
  background:#f7f8fa;border-radius:12px;}
</style>
"""


def _lang(lang_code):
    return _DASH.get(lang_code, _DASH["zh"])


def _num(v, nd=2, na="—"):
    if v is None or (isinstance(v, float) and v != v):   # None / NaN
        return na
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return na


def _color(v):
    try:
        return _GREEN if float(v) >= 0 else _RED
    except (TypeError, ValueError):
        return "#6b7280"


def _spark_svg(values, w=520, h=88):
    """权益曲线内联 SVG（降采样到 ~160 点）。空/无效 → 空字符串。"""
    pts = [float(v) for v in (values or []) if v is not None and v == v]
    if len(pts) < 2:
        return ""
    if len(pts) > 160:
        step = len(pts) / 160.0
        pts = [pts[int(i * step)] for i in range(160)]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(pts)
    up = pts[-1] >= pts[0]
    color = _GREEN if up else _RED
    coords = []
    for i, v in enumerate(pts):
        x = i / (n - 1) * w
        y = h - (v - lo) / span * (h - 6) - 3   # 留 3px 上下边距
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    return (
        f'<svg class="qd-spark" viewBox="0 0 {w} {h}" width="100%" height="{h}" '
        f'preserveAspectRatio="none">'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def build_dashboard_placeholder(lang_code="zh"):
    t = _lang(lang_code)
    return _STYLE + f'<div class="qtbs-dash"><div class="qd-empty">{_html.escape(t["placeholder"])}</div></div>'


def _bar(frac):
    frac = max(0.0, min(1.0, frac))
    return f'<div class="qd-bar"><i style="width:{frac*100:.1f}%"></i></div>'


def build_dashboard_html(metrics, trades, meta, summary_text, lang_code="zh"):
    """metrics: 引擎 metrics dict；trades: 成交/片段列表；meta: {symbol,timeframe,start,end,
    kline_count,initial_cash,equity:[...]}; summary_text: get_summary_text(lang) 标签 dict。"""
    if not metrics:
        return build_dashboard_placeholder(lang_code)

    t = _lang(lang_code)
    st = summary_text
    na = t["na"]
    esc = _html.escape

    initial = metrics.get("initial_cash") or 0.0
    final = metrics.get("final_equity")
    total_ret = metrics.get("total_return_pct")
    abs_pnl = (final - initial) if (final is not None) else None

    # ---- 从成交派生：多空数、盈亏笔数、毛盈/毛亏、总费用 ----
    longs = sum(1 for x in trades if x.get("side") == "long")
    shorts = sum(1 for x in trades if x.get("side") == "short")
    wins = sum(1 for x in trades if (x.get("net_pnl") or 0) > 0)
    losses = sum(1 for x in trades if (x.get("net_pnl") or 0) < 0)
    gross_profit = sum((x.get("net_pnl") or 0) for x in trades if (x.get("net_pnl") or 0) > 0)
    gross_loss = sum((x.get("net_pnl") or 0) for x in trades if (x.get("net_pnl") or 0) < 0)
    # v1 成交：open_fee+close_fee；v2 片段：fees（总）。两种结构都兼容
    total_fee = sum(
        (x["fees"] if "fees" in x else (x.get("open_fee") or 0) + (x.get("close_fee") or 0)) or 0
        for x in trades
    )

    win_rate = metrics.get("net_win_rate")
    if win_rate is None and (wins + losses):
        win_rate = wins / (wins + losses) * 100
    ls_total = longs + shorts
    long_frac = (longs / ls_total) if ls_total else 0.0
    pf_total = gross_profit + abs(gross_loss)
    pf_frac = (gross_profit / pf_total) if pf_total else 0.0
    win_frac = ((wins / (wins + losses)) if (wins + losses) else 0.0)

    head = " · ".join(str(x) for x in [
        meta.get("symbol", ""), meta.get("timeframe", ""),
        f'{meta.get("start","")} ~ {meta.get("end","")}',
        f'{meta.get("kline_count","")}',
    ] if x not in ("", None))

    pnl_color = _color(total_ret if total_ret is not None else 0)
    sign = "+" if (total_ret is not None and total_ret >= 0) else ""
    abs_sign = "+" if (abs_pnl is not None and abs_pnl >= 0) else ""

    # ---- 指标行 ----
    rows = [
        (st.get("initial_cash", "initial_cash"), _num(initial), None),
        (st.get("final_equity", "final_equity"), _num(final, na=na), _color(abs_pnl if abs_pnl is not None else 0)),
        (st.get("annual_return", "annual_return"), _num(metrics.get("annual_return_pct"), na=na) + "%",
         _color(metrics.get("annual_return_pct") or 0)),
        (st.get("sharpe_ratio", "sharpe_ratio"), _num(metrics.get("sharpe_ratio"), na=na), None),
        (st.get("max_drawdown", "max_drawdown"), _num(metrics.get("max_drawdown_pct"), na=na) + "%", _RED),
    ]
    rows_html = "".join(
        f'<div class="qd-row"><span class="ql">{esc(str(lbl))}</span>'
        f'<span class="qv"{(f" style=color:{c}" if c else "")}>{esc(str(val))}</span></div>'
        for (lbl, val, c) in rows
    )

    # ---- 指标卡 ----
    cards = [
        (st.get("net_win_rate", "win_rate"), (_num(win_rate, na=na) + "%"),
         f'{wins} {t["win"]} / {losses} {t["loss"]}', win_frac),
        (t["long_short"], (_num(long_frac * 100, na=na) + "%"),
         f'{longs}{t["long"]} / {shorts}{t["short"]}', long_frac),
        (st.get("trade_count", "trades"), str(metrics.get("trade_count", len(trades))),
         f'{_num(metrics.get("avg_holding_hours"), 1, na)}h · {t["total_fee"]} {_num(total_fee)}', None),
        (st.get("profit_factor", "PF"), _num(metrics.get("profit_factor"), na=na),
         f'+{_num(gross_profit)} / {_num(gross_loss)}', pf_frac),
    ]
    cards_html = ""
    for (title, val, sub, frac) in cards:
        bar = _bar(frac) if frac is not None else ""
        cards_html += (
            f'<div class="qd-card"><div class="qc-t">{esc(str(title))}</div>'
            f'<div class="qc-v">{esc(str(val))}</div>'
            f'<div class="qc-sub">{esc(str(sub))}</div>{bar}</div>'
        )

    spark = _spark_svg(meta.get("equity"))

    return _STYLE + (
        f'<div class="qtbs-dash">'
        f'<div class="qd-head">{esc(head)}</div>'
        f'<div class="qd-pnl" style="color:{pnl_color}">{sign}{_num(total_ret, na=na)}%</div>'
        f'<div class="qd-pnl-abs" style="color:{pnl_color}">{abs_sign}{_num(abs_pnl, na=na)} '
        f'<span style="color:#9aa0ab;font-weight:400">({t["total_pnl"]})</span></div>'
        f'{spark}'
        f'{rows_html}'
        f'<div class="qd-cards">{cards_html}</div>'
        f'</div>'
    )
