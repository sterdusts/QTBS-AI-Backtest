"""
回测结果可视化仪表盘（输出台用）：把 metrics + trades 渲染成带样式的 HTML 卡片——
大字总盈亏 + 权益迷你曲线(内联 SVG) + 关键指标行 + 指标卡（胜率/多空比/总交易数/盈亏比，
各带彩条）。纯函数、返回自带 <style> 的自包含 HTML（gr.HTML 直接吃），便于单测。

指标标签复用 webUI 的 SUMMARY_TEXTS（经 summary_text 传入，不重复翻译）；仪表盘特有的
少量标签（总盈亏/多空比/总费用/盈/亏/多/空 等）在本模块内置六语言。
"""

import html as _html
import math
import uuid

# 历史成交/订单记录区标签（zh+en；其余语言按 _t2 回退 zh）
_TRADE_LABELS = {
    "zh": {"trade_history": "历史成交", "order_records": "订单记录", "f_all": "全部",
           "f_win": "盈利", "f_loss": "亏损", "f_open": "开仓记录", "f_close": "平仓记录",
           "entry": "入场", "exit": "出场", "qty": "数量", "price": "价格",
           "notional": "名义价值", "open_act": "开", "close_act": "平",
           "empty": "无成交记录", "more": "（仅显示前 {n} 条，共 {total} 条）"},
    "en": {"trade_history": "Trades", "order_records": "Orders", "f_all": "All",
           "f_win": "Profit", "f_loss": "Loss", "f_open": "Open", "f_close": "Close",
           "entry": "Entry", "exit": "Exit", "qty": "Qty", "price": "Price",
           "notional": "Notional", "open_act": "Open", "close_act": "Close",
           "empty": "No trades", "more": "(showing first {n} of {total})"},
    "ko": {"trade_history": "체결 내역", "order_records": "주문 내역", "f_all": "전체",
           "f_win": "수익", "f_loss": "손실", "f_open": "진입", "f_close": "청산",
           "entry": "진입", "exit": "청산", "qty": "수량", "price": "가격",
           "notional": "명목가치", "open_act": "진입", "close_act": "청산",
           "empty": "체결 없음", "more": "(상위 {n}건만 표시, 총 {total}건)"},
    "ja": {"trade_history": "約定履歴", "order_records": "注文履歴", "f_all": "全て",
           "f_win": "利益", "f_loss": "損失", "f_open": "新規", "f_close": "決済",
           "entry": "エントリー", "exit": "エグジット", "qty": "数量", "price": "価格",
           "notional": "想定元本", "open_act": "新規", "close_act": "決済",
           "empty": "約定なし", "more": "(先頭{n}件のみ表示、全{total}件)"},
    "ar": {"trade_history": "الصفقات", "order_records": "الأوامر", "f_all": "الكل",
           "f_win": "ربح", "f_loss": "خسارة", "f_open": "فتح", "f_close": "إغلاق",
           "entry": "دخول", "exit": "خروج", "qty": "الكمية", "price": "السعر",
           "notional": "القيمة الاسمية", "open_act": "فتح", "close_act": "إغلاق",
           "empty": "لا صفقات", "more": "(عرض أول {n} من {total})"},
    "ru": {"trade_history": "Сделки", "order_records": "Ордера", "f_all": "Все",
           "f_win": "Прибыль", "f_loss": "Убыток", "f_open": "Открытие", "f_close": "Закрытие",
           "entry": "Вход", "exit": "Выход", "qty": "Кол-во", "price": "Цена",
           "notional": "Номинал", "open_act": "Откр.", "close_act": "Закр.",
           "empty": "Нет сделок", "more": "(показаны первые {n} из {total})"},
}
_REASON_LABELS = {
    "zh": {"signal": "信号", "stop_loss": "止损", "take_profit": "止盈",
           "liquidation": "强平", "end_of_data": "末根结算"},
    "en": {"signal": "Signal", "stop_loss": "Stop", "take_profit": "Take-profit",
           "liquidation": "Liquidation", "end_of_data": "End-of-data"},
    "ko": {"signal": "신호", "stop_loss": "손절", "take_profit": "익절",
           "liquidation": "청산", "end_of_data": "데이터 종료"},
    "ja": {"signal": "シグナル", "stop_loss": "損切り", "take_profit": "利確",
           "liquidation": "強制決済", "end_of_data": "データ終端"},
    "ar": {"signal": "إشارة", "stop_loss": "وقف الخسارة", "take_profit": "جني الأرباح",
           "liquidation": "تصفية", "end_of_data": "نهاية البيانات"},
    "ru": {"signal": "Сигнал", "stop_loss": "Стоп-лосс", "take_profit": "Тейк-профит",
           "liquidation": "Ликвидация", "end_of_data": "Конец данных"},
}
_MAX_LIST_ROWS = 300   # 单个列表最多渲染行数（防超高换手策略产出巨量 HTML）

# 历史详情区（提示词 / 代码 / 参数）标签，六语言
_HIST_LABELS = {
    "zh": {"prompt": "自然语言提示词", "code": "回测代码", "params": "回测参数",
           "summary": "回测摘要", "empty": "（空）", "no_prompt": "（直接粘贴代码回测，无提示词）"},
    "en": {"prompt": "Prompt", "code": "Strategy Code", "params": "Parameters",
           "summary": "Summary", "empty": "(empty)", "no_prompt": "(pasted code directly, no prompt)"},
    "ko": {"prompt": "자연어 프롬프트", "code": "백테스트 코드", "params": "파라미터",
           "summary": "요약", "empty": "(없음)", "no_prompt": "(코드 직접 입력, 프롬프트 없음)"},
    "ja": {"prompt": "自然言語プロンプト", "code": "バックテストコード", "params": "パラメータ",
           "summary": "サマリー", "empty": "（空）", "no_prompt": "（コード直接貼り付け、プロンプトなし）"},
    "ar": {"prompt": "المُوجّه النصي", "code": "كود الاختبار", "params": "المعطيات",
           "summary": "الملخص", "empty": "(فارغ)", "no_prompt": "(تم لصق الكود مباشرة، بدون موجّه)"},
    "ru": {"prompt": "Промпт", "code": "Код стратегии", "params": "Параметры",
           "summary": "Сводка", "empty": "(пусто)", "no_prompt": "(код вставлен напрямую, без промпта)"},
}

# 仪表盘特有标签（SUMMARY_TEXTS 没有的），六语言
_DASH = {
    "zh": {"total_pnl": "总盈亏", "long_short": "多空比", "total_fee": "总费用",
           "win": "盈利", "loss": "亏损", "long": "多", "short": "空", "trades_unit": "笔交易", "sum_pl": "合计", "avg_pl": "单笔均",
           "placeholder": "运行回测后，结果将在此可视化展示", "na": "—"},
    "en": {"total_pnl": "Total P&L", "long_short": "Long/Short", "total_fee": "Total Fees",
           "win": "Win", "loss": "Loss", "long": "L", "short": "S", "trades_unit": "trades", "sum_pl": "Total", "avg_pl": "Avg/trade",
           "placeholder": "Run a backtest to see the visual results here", "na": "—"},
    "ko": {"total_pnl": "총 손익", "long_short": "롱/숏", "total_fee": "총 수수료",
           "win": "수익", "loss": "손실", "long": "롱", "short": "숏", "trades_unit": "건", "sum_pl": "합계", "avg_pl": "건당 평균",
           "placeholder": "백테스트를 실행하면 여기에 시각화됩니다", "na": "—"},
    "ja": {"total_pnl": "総損益", "long_short": "ロング/ショート", "total_fee": "総手数料",
           "win": "勝ち", "loss": "負け", "long": "買", "short": "売", "trades_unit": "回", "sum_pl": "合計", "avg_pl": "1回平均",
           "placeholder": "バックテストを実行すると結果がここに表示されます", "na": "—"},
    "ar": {"total_pnl": "إجمالي الربح/الخسارة", "long_short": "شراء/بيع", "total_fee": "إجمالي الرسوم",
           "win": "ربح", "loss": "خسارة", "long": "شراء", "short": "بيع", "trades_unit": "صفقة", "sum_pl": "الإجمالي", "avg_pl": "متوسط/صفقة",
           "placeholder": "شغّل اختبارًا خلفيًا لعرض النتائج هنا", "na": "—"},
    "ru": {"total_pnl": "Общий P&L", "long_short": "Лонг/Шорт", "total_fee": "Сумма комиссий",
           "win": "Приб.", "loss": "Убыт.", "long": "Л", "short": "Ш", "trades_unit": "сделок", "sum_pl": "Сумма", "avg_pl": "Сред.",
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
.qtbs-dash .qd-sec-t{font-size:15px;font-weight:700;margin:20px 0 8px;}
.qtbs-dash .qd-tabs > input{display:none;}
.qtbs-dash .qd-tabbar{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;}
.qtbs-dash .qd-tabbar label{font-size:13px;color:#5b6470;background:#eef0f4;border-radius:14px;
  padding:4px 13px;cursor:pointer;user-select:none;}
.qtbs-dash .qd-list{max-height:520px;overflow-y:auto;}
.qtbs-dash .qd-trow{background:#f7f8fa;border-radius:12px;padding:10px 14px;margin-bottom:8px;}
.qtbs-dash .qd-trow .tl1{display:flex;justify-content:space-between;align-items:center;}
/* 成交行头：标的+方向徽章在前，盈亏额紧随其后（左对齐，不甩到最右便于扫读） */
.qtbs-dash .qd-trow .tlh{display:flex;align-items:center;gap:10px;}
.qtbs-dash .qd-trow .lft{display:flex;align-items:center;}
.qtbs-dash .qd-sym{font-weight:700;font-size:15px;}
.qtbs-dash .qd-badge{font-size:11px;border-radius:6px;padding:1px 8px;margin-left:8px;}
.qtbs-dash .qd-badge.bl{background:#e3f7ec;color:#16a564;}
.qtbs-dash .qd-badge.bs{background:#fde8ea;color:#d6453d;}
.qtbs-dash .qd-pnlv{font-weight:800;font-size:15px;}
.qtbs-dash .qd-pnlpct{font-size:11px;font-weight:700;margin-left:3px;vertical-align:super;opacity:.85;}
.qtbs-dash .qd-time{font-size:11px;color:#9aa0ab;margin:3px 0 7px;}
.qtbs-dash .qd-fields{display:flex;gap:22px;font-size:13px;flex-wrap:wrap;}
.qtbs-dash .qd-fields .fk{color:#9aa0ab;margin-right:5px;}
.qtbs-dash .qd-reason{font-size:11px;color:#9aa0ab;margin-top:7px;}
.qtbs-dash .qd-more{font-size:11px;color:#9aa0ab;text-align:center;padding:6px;}
.qtbs-dash .qd-pre{background:#f7f8fa;border-radius:10px;padding:12px 14px;margin:0 0 6px;
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.5;color:#2b2f36;
  white-space:pre-wrap;word-break:break-word;max-height:360px;overflow:auto;}
/* CSS-only 过滤标签（3 选 1）：按行 class c1/c2 显隐，每行只渲染一次 */
.qtbs-dash .qd-tabs>input.f1:checked~.qd-list>.qd-trow:not(.c1),
.qtbs-dash .qd-tabs>input.f2:checked~.qd-list>.qd-trow:not(.c2){display:none;}
.qtbs-dash .qd-tabs>input.f0:checked~.qd-tabbar label.l0,
.qtbs-dash .qd-tabs>input.f1:checked~.qd-tabbar label.l1,
.qtbs-dash .qd-tabs>input.f2:checked~.qd-tabbar label.l2{background:#5b8ff9;color:#fff;}
</style>
"""


def _lang(lang_code):
    return _DASH.get(lang_code, _DASH["zh"])


def _t2(lang, key):
    # 终值用 .get 兜底（缺键降级为键名而非 KeyError 整盘崩），与 _reason 同口径
    d = _TRADE_LABELS.get(lang) or {}
    return d.get(key) or _TRADE_LABELS["zh"].get(key) or key


def _reason(lang, r):
    if not r:
        return ""
    d = _REASON_LABELS.get(lang) or {}
    return d.get(r) or _REASON_LABELS["zh"].get(r, str(r))


def _fmt_time(s):
    """'YYYY-MM-DD HH:MM:SS' → 'MM-DD HH:MM'（截断，失败原样返回）。"""
    s = str(s) if s is not None else ""
    return s[5:16] if len(s) >= 16 and s[4] == "-" else s


def _f(tr, *keys):
    """取首个非 None 字段并转 float（失败返回 None）。"""
    for k in keys:
        v = tr.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _notional_of(tr):
    """名义价值：v1 有 entry_notional；v2 用 max_abs_qty × entry_price 推算。"""
    n = _f(tr, "entry_notional", "notional")
    if n is not None:
        return abs(n)
    q = _f(tr, "max_abs_qty", "position", "qty")
    p = _f(tr, "entry_price")
    return abs(q * p) if (q is not None and p is not None) else None


def _num(v, nd=2, na="—"):
    # None / 不可解析 / 非有限(NaN,±inf) 一律占位符。仅判 v!=v 会漏掉 inf
    # （如 profit_factor 零毛亏=inf），导致仪表盘直接显示字面 "inf"。
    if v is None:
        return na
    try:
        f = float(v)
    except (TypeError, ValueError):
        return na
    return f"{f:,.{nd}f}" if math.isfinite(f) else na


def _color(v):
    try:
        return _GREEN if float(v) >= 0 else _RED
    except (TypeError, ValueError):
        return "#6b7280"


def _spark_svg(values, w=520, h=88):
    """权益曲线内联 SVG（降采样到 ~160 点）。空/无效 → 空字符串。"""
    # 只保留【有限】值：剔除 None/NaN/±inf。仅用 v==v 会漏掉 inf（inf==inf 为真），
    # 一个 inf 会让 min/max/span 变 inf、(v-lo)/span 算出 nan 坐标、整条折线损坏（审查 F1）。
    pts = []
    for v in (values or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            pts.append(f)
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


def _trade_row(tr, default_sym, lang):
    """单条成交 → VergeX 风格卡片行。class c1=盈利 / c2=亏损 供 CSS 过滤。"""
    esc = _html.escape
    side = tr.get("side")
    is_long = side == "long"
    badge_cls = "bl" if is_long else "bs"
    side_txt = _DASH.get(lang, _DASH["zh"])["long"] if is_long else _DASH.get(lang, _DASH["zh"])["short"]
    sym = esc(str(tr.get("symbol") or default_sym or ""))

    pnl = _f(tr, "net_pnl", "pnl")
    pnl_cls = "c1" if (pnl is not None and pnl > 0) else ("c2" if (pnl is not None and pnl < 0) else "")
    pnl_color = _color(pnl if pnl is not None else 0)
    pnl_sign = "+" if (pnl is not None and pnl >= 0) else ""

    # 盈亏百分比小字角标，跟在盈亏额后（净盈亏 / 入场权益，引擎口径 net_pnl_pct）
    pnl_pct = _f(tr, "net_pnl_pct", "pnl_pct")
    pct_txt = _num(pnl_pct, na="") if pnl_pct is not None else ""
    pct_html = (
        f'<sup class="qd-pnlpct" style="color:{pnl_color}">'
        f'{("+" if pnl_pct >= 0 else "")}{pct_txt}%</sup>'
    ) if pct_txt else ""

    ep, xp = _f(tr, "entry_price"), _f(tr, "exit_price")
    notion = _notional_of(tr)
    reason = _reason(lang, tr.get("exit_reason"))
    t_in = _fmt_time(tr.get("entry_time"))
    t_out = _fmt_time(tr.get("exit_time"))
    time_part = f'{esc(t_in)} → {esc(t_out)}' if (t_in or t_out) else ""

    fields = (
        f'<span><i class="fk">{esc(_t2(lang, "entry"))}</i>{_num(ep, na="—")}</span>'
        f'<span><i class="fk">{esc(_t2(lang, "exit"))}</i>{_num(xp, na="—")}</span>'
        f'<span><i class="fk">{esc(_t2(lang, "notional"))}</i>{_num(notion, na="—")}</span>'
    )
    reason_html = f'<div class="qd-reason">{esc(reason)}</div>' if reason else ""
    return (
        f'<div class="qd-trow {pnl_cls}">'
        f'<div class="tlh"><span class="qd-sym">{sym}</span>'
        f'<span class="qd-badge {badge_cls}">{esc(side_txt)}</span>'
        f'<span class="qd-pnlv" style="color:{pnl_color}">{pnl_sign}{_num(pnl, na="—")}{pct_html}</span></div>'
        f'<div class="qd-time">{time_part}</div>'
        f'<div class="qd-fields">{fields}</div>'
        f'{reason_html}</div>'
    )


def _order_rows(tr, default_sym, lang):
    """单条成交 → 两条订单记录（开仓 + 平仓）。class c1=开仓 / c2=平仓 供过滤。"""
    esc = _html.escape
    d = _DASH.get(lang, _DASH["zh"])
    side = tr.get("side")
    is_long = side == "long"
    sym = esc(str(tr.get("symbol") or default_sym or ""))
    notion = _notional_of(tr)
    qty = _f(tr, "max_abs_qty", "position", "qty")
    ep, xp = _f(tr, "entry_price"), _f(tr, "exit_price")
    t_in, t_out = _fmt_time(tr.get("entry_time")), _fmt_time(tr.get("exit_time"))
    reason = _reason(lang, tr.get("exit_reason"))

    def _row(cls, act_key, act_color, act_label, price, when, val, extra=""):
        fields = (
            f'<span><i class="fk">{esc(_t2(lang, "price"))}</i>{_num(price, na="—")}</span>'
            f'<span><i class="fk">{esc(_t2(lang, "notional"))}</i>{_num(val, na="—")}</span>'
        )
        if qty is not None:
            fields += f'<span><i class="fk">{esc(_t2(lang, "qty"))}</i>{_num(abs(qty), 4)}</span>'
        extra_html = f'<div class="qd-reason">{esc(extra)}</div>' if extra else ""
        return (
            f'<div class="qd-trow {cls}">'
            f'<div class="tl1"><span class="lft"><span class="qd-sym">{sym}</span>'
            f'<span class="qd-badge {act_color}">{esc(act_label)}</span></span>'
            f'<span class="qd-time" style="margin:0">{esc(when)}</span></div>'
            f'<div class="qd-fields">{fields}</div>{extra_html}</div>'
        )

    open_color = "bl" if is_long else "bs"
    open_label = f'{d["long"] if is_long else d["short"]} · {_t2(lang, "open_act")}'
    close_label = f'{d["short"] if is_long else d["long"]} · {_t2(lang, "close_act")}'
    return (
        _row("c1", "open", open_color, open_label, ep, t_in, notion)
        + _row("c2", "close", "bs" if is_long else "bl", close_label, xp, t_out, notion, reason)
    )


def _tab_list(uid, labels3, rows, shown, total, lang):
    """CSS-only 三选一过滤标签 + 滚动列表。labels3=(全部,c1标签,c2标签)。rows 已含 c1/c2 class。
    shown/total 为「展示条数 / 总条数」（同单位：成交=笔，订单=条），仅在截断时提示。"""
    esc = _html.escape
    if not rows:
        return f'<div class="qd-list"><div class="qd-empty">{esc(_t2(lang, "empty"))}</div></div>'
    name = f"f_{uid}"
    bar = (
        f'<input type="radio" class="f0" name="{name}" id="{name}_0" checked>'
        f'<input type="radio" class="f1" name="{name}" id="{name}_1">'
        f'<input type="radio" class="f2" name="{name}" id="{name}_2">'
        f'<div class="qd-tabbar">'
        f'<label class="l0" for="{name}_0">{esc(labels3[0])}</label>'
        f'<label class="l1" for="{name}_1">{esc(labels3[1])}</label>'
        f'<label class="l2" for="{name}_2">{esc(labels3[2])}</label></div>'
    )
    more = ""
    if total > shown:
        more = f'<div class="qd-more">{_t2(lang, "more").format(n=shown, total=total)}</div>'
    return f'<div class="qd-tabs">{bar}<div class="qd-list">{"".join(rows)}{more}</div></div>'


def _trades_section(trades, default_sym, lang, uid):
    esc = _html.escape
    capped = trades[:_MAX_LIST_ROWS]
    rows = [_trade_row(tr, default_sym, lang) for tr in capped]
    labels3 = (_t2(lang, "f_all"), _t2(lang, "f_win"), _t2(lang, "f_loss"))
    body = _tab_list(f"trd{uid}", labels3, rows, len(rows), len(trades), lang)
    return f'<div class="qd-sec-t">{esc(_t2(lang, "trade_history"))}</div>{body}'


def _orders_section(trades, default_sym, lang, uid):
    esc = _html.escape
    capped = trades[:_MAX_LIST_ROWS // 2]   # 每笔 2 条订单，列表上限对齐
    rows = [_order_rows(tr, default_sym, lang) for tr in capped]
    labels3 = (_t2(lang, "f_all"), _t2(lang, "f_open"), _t2(lang, "f_close"))
    # 订单单位为「条」：每笔成交 = 2 条订单（开+平）
    body = _tab_list(f"ord{uid}", labels3, rows, len(rows) * 2, len(trades) * 2, lang)
    return f'<div class="qd-sec-t">{esc(_t2(lang, "order_records"))}</div>{body}'


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
    # 进度条与显示的胜率%同源（用 win_rate/100），避免含保本单(net_pnl==0)时
    # "卡片数字按 net_win_rate(分母含全部交易) 而条形按 wins/(wins+losses)" 不一致
    win_frac = (win_rate / 100.0) if win_rate is not None else (
        (wins / (wins + losses)) if (wins + losses) else 0.0)

    _start, _end = meta.get("start"), meta.get("end")
    date_part = f"{_start} ~ {_end}" if (_start or _end) else ""   # 都缺时不要裸 " ~ "
    head = " · ".join(str(x) for x in [
        meta.get("symbol", ""), meta.get("timeframe", ""),
        date_part, meta.get("kline_count", ""),
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
    # 盈亏比 = 平均盈利/平均亏损（payoff_ratio）；与 Profit Factor(总盈/总亏)不同口径。
    # 条形按 平均盈利/(平均盈利+|平均亏损|) 直观对比单笔盈亏体量。
    avg_p = metrics.get("avg_profit") or 0.0
    avg_l = metrics.get("avg_loss") or 0.0
    payoff_frac = (avg_p / (avg_p + abs(avg_l))) if (avg_p + abs(avg_l)) else 0.0
    cards = [
        (st.get("net_win_rate", "win_rate"), (_num(win_rate, na=na) + "%"),
         f'{wins} {t["win"]} / {losses} {t["loss"]}', win_frac),
        (t["long_short"], (_num(long_frac * 100, na=na) + "%"),
         f'{longs}{t["long"]} / {shorts}{t["short"]}', long_frac),
        # 盈亏比 与 交易次数 互换：盈亏比占普通卡片位。副标题标「单笔均」区别于 PF 的「合计」
        (st.get("payoff_ratio", "payoff_ratio"), _num(metrics.get("payoff_ratio"), na=na),
         f'{t["avg_pl"]} +{_num(avg_p)} / {_num(avg_l)}', payoff_frac),
        (st.get("profit_factor", "PF"), _num(metrics.get("profit_factor"), na=na),
         f'{t["sum_pl"]} +{_num(gross_profit)} / {_num(gross_loss)}', pf_frac),
    ]
    # 交易次数 改为整行卡片置于末尾（与盈亏比互换）
    cards.append((st.get("trade_count", "trades"), str(metrics.get("trade_count", len(trades))),
                  f'{_num(metrics.get("avg_holding_hours"), 1, na)}h · {t["total_fee"]} {_num(total_fee)}', None, True))

    cards_html = ""
    for card in cards:
        title, val, sub, frac = card[0], card[1], card[2], card[3]
        wide = len(card) > 4 and card[4]
        bar = _bar(frac) if frac is not None else ""
        style = ' style="grid-column:1/-1"' if wide else ""
        cards_html += (
            f'<div class="qd-card"{style}><div class="qc-t">{esc(str(title))}</div>'
            f'<div class="qc-v">{esc(str(val))}</div>'
            f'<div class="qc-sub">{esc(str(sub))}</div>{bar}</div>'
        )

    spark = _spark_svg(meta.get("equity"))

    # 历史成交 + 订单记录（VergeX 卡片列表 + CSS 过滤标签）。uid 隔离多仪表盘共存时的 radio name。
    uid = uuid.uuid4().hex[:8]
    default_sym = meta.get("symbol", "")
    trades_html = _trades_section(trades, default_sym, lang_code, uid) if trades else ""
    orders_html = _orders_section(trades, default_sym, lang_code, uid) if trades else ""

    return _STYLE + (
        f'<div class="qtbs-dash">'
        f'<div class="qd-head">{esc(head)}</div>'
        f'<div class="qd-pnl" style="color:{pnl_color}">{sign}{_num(total_ret, na=na)}%</div>'
        f'<div class="qd-pnl-abs" style="color:{pnl_color}">{abs_sign}{_num(abs_pnl, na=na)} '
        f'<span style="color:#9aa0ab;font-weight:400">({t["total_pnl"]})</span></div>'
        f'{spark}'
        f'{rows_html}'
        f'<div class="qd-cards">{cards_html}</div>'
        f'{trades_html}'
        f'{orders_html}'
        f'</div>'
    )


def build_history_detail_html(record, lang_code="zh"):
    """历史记录详情：独立展示当时的【自然语言提示词 / 回测代码 / 参数 / 摘要】。
    record 为 run_history 落盘的一条 JSON（load_run_record 读回）。自带 <style>。"""
    h = _HIST_LABELS.get(lang_code, _HIST_LABELS["zh"])
    esc = _html.escape
    if not record:
        return _STYLE + f'<div class="qtbs-dash"><div class="qd-empty">{esc(h["empty"])}</div></div>'

    prompt = (record.get("prompt") or "").strip()
    code = (record.get("strategy_code") or "").strip()
    summary = (record.get("summary") or "").strip()
    params = record.get("params") or {}

    def _pre(text):
        return f'<pre class="qd-pre">{esc(text)}</pre>'

    prompt_block = _pre(prompt) if prompt else f'<div class="qd-empty">{esc(h["no_prompt"])}</div>'
    code_block = _pre(code) if code else f'<div class="qd-empty">{esc(h["empty"])}</div>'

    # 参数：key/value 行（值为 list 时逗号连接），紧凑两列
    param_rows = ""
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        param_rows += (
            f'<div class="qd-row"><span class="ql">{esc(str(k))}</span>'
            f'<span class="qv">{esc(str(v))}</span></div>'
        )
    params_block = param_rows or f'<div class="qd-empty">{esc(h["empty"])}</div>'

    summary_block = _pre(summary) if summary else ""
    summary_sec = (
        f'<div class="qd-sec-t">{esc(h["summary"])}</div>{summary_block}' if summary else ""
    )

    return _STYLE + (
        f'<div class="qtbs-dash">'
        f'<div class="qd-sec-t">{esc(h["prompt"])}</div>{prompt_block}'
        f'<div class="qd-sec-t">{esc(h["params"])}</div>{params_block}'
        f'<div class="qd-sec-t">{esc(h["code"])}</div>{code_block}'
        f'{summary_sec}'
        f'</div>'
    )
