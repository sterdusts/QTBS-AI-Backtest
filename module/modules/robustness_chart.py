"""
稳健性分析图表（Phase 3 Stage 5）：把 robustness.run_full_analysis 的报告 dict 渲染成
一页 pyecharts（垂直堆叠）：
1. 样本内 vs 样本外——收益类指标对比（总收益/年化/最大回撤，%，同量纲）
2. 样本内 vs 样本外——比率类指标对比（夏普/盈亏比/净胜率，避免与 % 混轴失真）
3. Walk-Forward 各窗样本外收益（正绿负红 + 均值红线）
4. 策略参数热力图（仅当报告含 param_scan，即策略声明了 PARAM_SPACE）

纯渲染层：只读报告 dict、不碰引擎/数据。返回 HTML 路径供 webUI gr.File 下载/查看。
"""

import os
import webbrowser

from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, HeatMap, Page

from module.modules.file_naming import build_timestamped_filename

# 收益类（%，同量纲可同轴）/ 比率类（小量级，单独成图）
_PCT_METRICS = ("total_return_pct", "annual_return_pct", "max_drawdown_pct")
_RATIO_METRICS = ("sharpe_ratio", "profit_factor", "net_win_rate")

_LABELS = {
    "zh": {
        "page_title": "稳健性分析", "in": "样本内(IS)", "out": "样本外(OOS)",
        "pct_title": "样本内 vs 样本外 · 收益类(%)", "ratio_title": "样本内 vs 样本外 · 比率类",
        "wfo_title": "Walk-Forward 各窗样本外收益(%)", "oos_ret": "样本外收益(%)", "mean": "均值",
        "heatmap_title": "策略参数热力图", "metric_value": "指标值", "window": "窗口",
        "m_total_return_pct": "总收益%", "m_annual_return_pct": "年化%", "m_max_drawdown_pct": "最大回撤%",
        "m_sharpe_ratio": "夏普", "m_profit_factor": "盈亏比", "m_net_win_rate": "净胜率",
    },
    "en": {
        "page_title": "Robustness Analysis", "in": "In-Sample (IS)", "out": "Out-of-Sample (OOS)",
        "pct_title": "IS vs OOS · Returns (%)", "ratio_title": "IS vs OOS · Ratios",
        "wfo_title": "Walk-Forward OOS Return per Window (%)", "oos_ret": "OOS Return (%)", "mean": "Mean",
        "heatmap_title": "Strategy Parameter Heatmap", "metric_value": "Metric", "window": "Window",
        "m_total_return_pct": "Total Ret%", "m_annual_return_pct": "Annual%", "m_max_drawdown_pct": "MaxDD%",
        "m_sharpe_ratio": "Sharpe", "m_profit_factor": "PF", "m_net_win_rate": "WinRate",
    },
    "ja": {
        "page_title": "ロバストネス分析", "in": "インサンプル(IS)", "out": "アウトサンプル(OOS)",
        "pct_title": "IS vs OOS · リターン系(%)", "ratio_title": "IS vs OOS · 比率系",
        "wfo_title": "ウォークフォワード 各窓OOSリターン(%)", "oos_ret": "OOSリターン(%)", "mean": "平均",
        "heatmap_title": "戦略パラメータ ヒートマップ", "metric_value": "指標値", "window": "ウィンドウ",
        "m_total_return_pct": "総収益%", "m_annual_return_pct": "年率%", "m_max_drawdown_pct": "最大DD%",
        "m_sharpe_ratio": "シャープ", "m_profit_factor": "PF", "m_net_win_rate": "勝率",
    },
    "ko": {
        "page_title": "강건성 분석", "in": "표본내(IS)", "out": "표본외(OOS)",
        "pct_title": "IS vs OOS · 수익(%)", "ratio_title": "IS vs OOS · 비율",
        "wfo_title": "워크포워드 창별 표본외 수익(%)", "oos_ret": "표본외 수익(%)", "mean": "평균",
        "heatmap_title": "전략 파라미터 히트맵", "metric_value": "지표값", "window": "창",
        "m_total_return_pct": "총수익%", "m_annual_return_pct": "연율%", "m_max_drawdown_pct": "최대낙폭%",
        "m_sharpe_ratio": "샤프", "m_profit_factor": "손익비", "m_net_win_rate": "승률",
    },
    "ru": {
        "page_title": "Анализ устойчивости", "in": "В выборке (IS)", "out": "Вне выборки (OOS)",
        "pct_title": "IS vs OOS · Доходность (%)", "ratio_title": "IS vs OOS · Коэффициенты",
        "wfo_title": "Walk-Forward доходность OOS по окнам (%)", "oos_ret": "Доходность OOS (%)", "mean": "Среднее",
        "heatmap_title": "Тепловая карта параметров", "metric_value": "Метрика", "window": "Окно",
        "m_total_return_pct": "Доход%", "m_annual_return_pct": "Годовых%", "m_max_drawdown_pct": "МаксПросад%",
        "m_sharpe_ratio": "Шарп", "m_profit_factor": "PF", "m_net_win_rate": "Винрейт",
    },
    "ar": {
        "page_title": "تحليل المتانة", "in": "داخل العينة (IS)", "out": "خارج العينة (OOS)",
        "pct_title": "IS مقابل OOS · العوائد (%)", "ratio_title": "IS مقابل OOS · النسب",
        "wfo_title": "عائد OOS لكل نافذة Walk-Forward (%)", "oos_ret": "عائد OOS (%)", "mean": "المتوسط",
        "heatmap_title": "خريطة حرارية لمعاملات الاستراتيجية", "metric_value": "القيمة", "window": "نافذة",
        "m_total_return_pct": "إجمالي%", "m_annual_return_pct": "سنوي%", "m_max_drawdown_pct": "أقصى تراجع%",
        "m_sharpe_ratio": "شارب", "m_profit_factor": "PF", "m_net_win_rate": "نسبة الفوز",
    },
}


def _t(lang, key):
    table = _LABELS.get(lang, _LABELS["zh"])
    return table.get(key, _LABELS["zh"].get(key, key))


def _round(v):
    return round(v, 4) if isinstance(v, (int, float)) else None


def _title_legend(title, with_legend=True):
    """统一：标题居中置顶、图例在标题【下方】（不再与标题重叠）。"""
    opts_d = {
        "title_opts": opts.TitleOpts(title=title, pos_left="center", pos_top="3%"),
        "tooltip_opts": opts.TooltipOpts(trigger="axis"),
    }
    if with_legend:
        opts_d["legend_opts"] = opts.LegendOpts(pos_left="center", pos_top="12%")
    else:
        opts_d["legend_opts"] = opts.LegendOpts(is_show=False)
    return opts_d


def _wrap(chart, height="380px", pos_top="27%", pos_bottom="14%"):
    """把单图包进 Grid：标题/图例在画布顶部 27% 内，绘图区从 27% 起——彻底避开重叠。
    宽度 100% ⇒ 每图独占一行、自适应容器宽（修 SimplePageLayout flex-wrap 的 2 列错排）。"""
    grid = Grid(init_opts=opts.InitOpts(width="100%", height=height))
    grid.add(chart, grid_opts=opts.GridOpts(
        pos_top=pos_top, pos_bottom=pos_bottom, pos_left="8%", pos_right="6%",
        is_contain_label=True))
    return grid


def _is_oos_bar(report, metrics, title, lang):
    """IS/OOS 双系列柱（指标子集）。degradation 不可用 ⇒ None（不出该图）。"""
    deg = (report.get("in_out") or {}).get("degradation") or {}
    if not deg.get("available"):
        return None
    per = deg.get("metrics", {})
    labels = [_t(lang, f"m_{m}") for m in metrics]
    is_vals = [_round((per.get(m) or {}).get("in")) for m in metrics]
    oos_vals = [_round((per.get(m) or {}).get("out")) for m in metrics]

    bar = Bar(init_opts=opts.InitOpts(width="960px", height="360px"))
    bar.add_xaxis(labels)
    bar.add_yaxis(_t(lang, "in"), is_vals, label_opts=opts.LabelOpts(is_show=False))
    bar.add_yaxis(_t(lang, "out"), oos_vals, label_opts=opts.LabelOpts(is_show=False))
    bar.set_global_opts(**_title_legend(title))
    return _wrap(bar, height="380px", pos_top="27%", pos_bottom="12%")


def _wfo_bar(report, lang):
    """Walk-Forward 各窗 OOS 收益柱（正绿负红）+ 均值红线。无有效窗 ⇒ None。"""
    wfo = report.get("walk_forward") or {}
    windows = wfo.get("windows") or []
    xs, ys = [], []
    for w in windows:
        t = w.get("test") or {}
        if t.get("insufficient"):
            continue
        ys.append(_round((t.get("metrics") or {}).get("total_return_pct")))
        xs.append(f'{_t(lang, "window")} {w.get("index")}')
    if not any(v is not None for v in ys):
        return None

    # 正绿负红：逐柱 itemStyle
    data = [opts.BarItem(name=x, value=v,
                         itemstyle_opts=opts.ItemStyleOpts(
                             color="#2e9e5b" if (v is not None and v >= 0) else "#d6453d"))
            for x, v in zip(xs, ys)]
    bar = Bar(init_opts=opts.InitOpts(width="960px", height="360px"))
    bar.add_xaxis(xs)
    bar.add_yaxis(_t(lang, "oos_ret"), data, label_opts=opts.LabelOpts(is_show=False))

    agg = wfo.get("aggregate") or {}
    mean_ret = agg.get("mean_out_return")
    markline = None
    if isinstance(mean_ret, (int, float)):
        markline = opts.MarkLineOpts(data=[opts.MarkLineItem(y=round(mean_ret, 4), name=_t(lang, "mean"))])
        bar.set_series_opts(markline_opts=markline)

    go = _title_legend(_t(lang, "wfo_title"))
    go["xaxis_opts"] = opts.AxisOpts(axislabel_opts=opts.LabelOpts(rotate=30))
    bar.set_global_opts(**go)
    return _wrap(bar, height="420px", pos_top="26%", pos_bottom="20%")


def _heatmap(report, lang):
    """策略参数热力图（仅 param_scan 存在时）。1 维则单行。无有效值 ⇒ None。"""
    scan = report.get("param_scan")
    if not scan:
        return None
    x_values = scan.get("x_values") or []
    y_param = scan.get("y_param")
    y_values = scan.get("y_values") if y_param else [scan.get("metric", "metric")]
    matrix = scan.get("matrix") or []

    x_labels = [str(v) for v in x_values]
    y_labels = [str(v) for v in y_values]

    data, vals = [], []
    for yi, row in enumerate(matrix):
        for xi, v in enumerate(row):
            if isinstance(v, (int, float)):
                rv = round(v, 4)
                data.append([xi, yi, rv])
                vals.append(rv)
            else:
                data.append([xi, yi, "-"])
    if not vals:
        return None

    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1  # 退化为单值时给 visualmap 一个区间

    x_name = scan.get("x_param", "x")
    title = f'{_t(lang, "heatmap_title")} · {scan.get("metric", "")}'
    if y_param:
        title += f" ({x_name} × {y_param})"
    else:
        title += f" ({x_name})"

    hm = HeatMap(init_opts=opts.InitOpts(width="960px", height="420px"))
    hm.add_xaxis(x_labels)
    hm.add_yaxis(_t(lang, "metric_value"), y_labels, data,
                 label_opts=opts.LabelOpts(is_show=True, position="inside"))
    hm.set_global_opts(
        title_opts=opts.TitleOpts(title=title, pos_left="center", pos_top="3%"),
        legend_opts=opts.LegendOpts(is_show=False),
        visualmap_opts=opts.VisualMapOpts(min_=lo, max_=hi, is_calculable=True,
                                          orient="horizontal", pos_left="center", pos_bottom="3%"),
        xaxis_opts=opts.AxisOpts(name=x_name, type_="category"),
        yaxis_opts=opts.AxisOpts(name=(y_param or ""), type_="category"),
    )
    # 顶部 18% 留标题、底部 24% 留水平 visualmap + x 轴标签
    return _wrap(hm, height="440px", pos_top="18%", pos_bottom="24%")


def plot_robustness(report, output_dir="Past_data", file_prefix="robustness",
                    language="zh", auto_open=False):
    """渲染稳健性报告为一页 HTML，返回路径。report=run_full_analysis 的返回。
    available=False（数据不足）时返回 None（webUI 只展示文字原因、不出图）。"""
    if not report or not report.get("available"):
        return None

    os.makedirs(output_dir, exist_ok=True)
    output_html = os.path.join(output_dir, build_timestamped_filename(file_prefix, ".html"))

    charts = [
        _is_oos_bar(report, _PCT_METRICS, _t(language, "pct_title"), language),
        _is_oos_bar(report, _RATIO_METRICS, _t(language, "ratio_title"), language),
        _wfo_bar(report, language),
        _heatmap(report, language),
    ]
    charts = [c for c in charts if c is not None]

    page = Page(layout=Page.SimplePageLayout, page_title=_t(language, "page_title"))
    for c in charts:
        page.add(c)
    page.render(output_html)

    if auto_open:
        try:
            webbrowser.open(output_html)
        except Exception:
            pass
    return output_html
