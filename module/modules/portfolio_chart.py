"""
组合策略回测图表（契约 v2 结果可视化）。

三段式布局：
1. 组合权益曲线（实时 + 已实现，真实数值）
2. 各标的持仓敞口（正=多 负=空）
3. 各标的归一化价格对比（基期=100）+ 成交标记（开/平/强平）

说明：
- 图表语言由生成时的 UI 语言决定（静态），暂不支持图内切换语言
- 复用 generic_chart 的历史文件切换器
"""

import math
import os
import webbrowser

from pyecharts import options as opts
from pyecharts.charts import Grid, Line, Scatter

from module.modules.file_naming import build_timestamped_filename
from module.modules.generic_chart import (
    _apply_responsive,
    _apply_switcher_block,
    _sync_chart_history,
    make_translator,
)


# 单序列超过该点数即等步长抽样（保留首尾与全部成交点）。
# pyecharts 单文件 HTML 内嵌全部数据，1m 级回测不抽样会产出
# 数百 MB 的 HTML 直接撑爆浏览器；显示用途下 2 万点肉眼无差。
MAX_CHART_POINTS = 20000


PORTFOLIO_TEXT = {
    "zh": {
        "title": "组合策略回测图表",
        "subtitle": "上：组合权益　中：各标的敞口　下：归一化价格（基期=100）与成交标记",
        "floating_equity": "组合实时权益",
        "realized_equity": "组合已实现权益",
        "equity_axis": "权益",
        "exposure_axis": "敞口",
        "price_axis": "价格指数",
        "buy": "买入",
        "sell": "卖出",
        "liquidation": "强平",
    },
    "en": {
        "title": "Portfolio Backtest Chart",
        "subtitle": "Top: portfolio equity | Middle: per-symbol exposure | Bottom: normalized price (base=100) with fills",
        "floating_equity": "Floating Equity",
        "realized_equity": "Realized Equity",
        "equity_axis": "Equity",
        "exposure_axis": "Exposure",
        "price_axis": "Price Index",
        "buy": "Buy",
        "sell": "Sell",
        "liquidation": "Liquidation",
    },
    "ko": {
        "title": "포트폴리오 백테스트 차트",
        "subtitle": "상단: 포트폴리오 자산 | 중단: 종목별 노출 | 하단: 정규화 가격(기준=100)과 체결 표시",
        "floating_equity": "실시간 자산",
        "realized_equity": "실현 자산",
        "equity_axis": "자산",
        "exposure_axis": "노출",
        "price_axis": "가격 지수",
        "buy": "매수",
        "sell": "매도",
        "liquidation": "강제청산",
    },
    "ja": {
        "title": "ポートフォリオバックテストチャート",
        "subtitle": "上：ポートフォリオ資産　中：銘柄別エクスポージャー　下：正規化価格（基準=100）と約定マーク",
        "floating_equity": "リアルタイム資産",
        "realized_equity": "実現資産",
        "equity_axis": "資産",
        "exposure_axis": "エクスポージャー",
        "price_axis": "価格指数",
        "buy": "買い",
        "sell": "売り",
        "liquidation": "強制決済",
    },
    "ru": {
        "title": "График бэктеста портфеля",
        "subtitle": "Сверху: капитал портфеля | Середина: экспозиция | Снизу: нормированная цена (база=100) и сделки",
        "floating_equity": "Текущий капитал",
        "realized_equity": "Реализованный капитал",
        "equity_axis": "Капитал",
        "exposure_axis": "Экспозиция",
        "price_axis": "Индекс цены",
        "buy": "Покупка",
        "sell": "Продажа",
        "liquidation": "Ликвидация",
    },
    "ar": {
        "title": "مخطط اختبار المحفظة",
        "subtitle": "أعلى: رأس مال المحفظة | وسط: التعرض لكل رمز | أسفل: السعر المعياري (الأساس=100) مع الصفقات",
        "floating_equity": "رأس المال الحالي",
        "realized_equity": "رأس المال المحقق",
        "equity_axis": "رأس المال",
        "exposure_axis": "التعرض",
        "price_axis": "مؤشر السعر",
        "buy": "شراء",
        "sell": "بيع",
        "liquidation": "تصفية",
    },
}


# 与 generic 图表共用同一回退语义（缺键降级显示而不是 KeyError 崩掉出图）
_t = make_translator(PORTFOLIO_TEXT)


def _downsample_positions(bar_count: int, keep_positions: set) -> list | None:
    """
    等步长抽样到 MAX_CHART_POINTS 量级。

    返回保留的索引列表（升序，含首尾与成交点）；点数未超限时返回 None。
    高频成交时成交点本身也按等步长抽样——否则 keep 集会退化为全量，
    点数上限被完全击穿（这正是上限要防的场景）。
    """

    if bar_count <= MAX_CHART_POINTS:
        return None

    stride = math.ceil(bar_count / MAX_CHART_POINTS)
    keep = set(range(0, bar_count, stride))
    keep.add(bar_count - 1)

    fills = sorted(p for p in keep_positions if 0 <= p < bar_count)
    if len(fills) > MAX_CHART_POINTS:
        fill_stride = math.ceil(len(fills) / MAX_CHART_POINTS)
        fills = fills[::fill_stride]
    keep.update(fills)

    return sorted(keep)


def plot_portfolio_result(
    result: dict,
    output_dir: str = "Past_data",
    file_prefix: str = "portfolio",
    timeframe: str = "",
    language: str = "zh",
    auto_open: bool = True,
) -> str:
    """
    渲染组合回测结果为 HTML，返回文件路径。
    """

    language = language if language in PORTFOLIO_TEXT else "zh"

    os.makedirs(output_dir, exist_ok=True)

    output_html_name = os.path.join(
        output_dir, build_timestamped_filename(file_prefix, ".html")
    )

    equity_curve = result["equity_curve"]
    realized_curve = result["realized_equity_curve"]
    exposure_curve = result["exposure_curve"]
    fills = result.get("fills", [])
    data = result["data"]
    symbols = result["metrics"].get("symbols", list(data.keys()))

    x_data = [point["time"] for point in equity_curve]
    bar_count = len(x_data)
    time_to_pos = {t: i for i, t in enumerate(x_data)}

    base_names = [s.replace("USDT", "") for s in symbols]
    title = f"{' + '.join(base_names)} {timeframe} {_t(language, 'title')}".strip()

    # ---------- 全分辨率序列 ----------

    floating_values = [round(float(p["equity_close"]), 4) for p in equity_curve]
    realized_values = [round(float(p["equity"]), 4) for p in realized_curve]

    exposure_series = {
        symbol: [round(float(point.get(symbol, 0.0)), 4) for point in exposure_curve]
        for symbol in symbols
    }

    normalized = {}

    for symbol in symbols:
        closes = data[symbol]["close"].to_numpy(dtype=float)[:bar_count]

        base = None
        for value in closes:
            if not math.isnan(value):
                base = value
                break

        series = []
        for value in closes:
            if base is None or math.isnan(value):
                series.append(None)
            else:
                series.append(round(value / base * 100.0, 4))

        normalized[symbol] = series

    # ---------- 成交标记（在全分辨率序列上定位；落在对应标的的归一化价格上） ----------

    marker_groups = {
        "buy": {"x": [], "y": [], "symbol": "triangle", "size": 11},
        "sell": {"x": [], "y": [], "symbol": "diamond", "size": 10},
        "liquidation": {"x": [], "y": [], "symbol": "pin", "size": 14},
    }
    fill_positions = set()

    for fill in fills:
        position = time_to_pos.get(fill["time"])
        if position is None:
            continue

        series = normalized.get(fill["symbol"])
        if series is None:
            continue

        value = series[position]
        if value is None:
            continue

        if fill.get("action") == "liquidation":
            group = marker_groups["liquidation"]
        elif fill["side"] == "buy":
            group = marker_groups["buy"]
        else:
            group = marker_groups["sell"]

        group["x"].append(fill["time"])
        group["y"].append(value)
        fill_positions.add(position)

    # ---------- 降采样（仅显示用途；成交时间点全部保留，标记仍能命中类目轴） ----------

    keep = _downsample_positions(bar_count, fill_positions)
    if keep is not None:
        x_data = [x_data[i] for i in keep]
        floating_values = [floating_values[i] for i in keep]
        realized_values = [realized_values[i] for i in keep]
        exposure_series = {
            s: [series[i] for i in keep] for s, series in exposure_series.items()
        }
        normalized = {
            s: [series[i] for i in keep] for s, series in normalized.items()
        }

        # 标记与类目轴同步：时间点被抽掉的散点必须一并去掉，
        # 否则 echarts 会把未知类目追加到轴尾、标记错位
        kept_times = set(x_data)
        for group in marker_groups.values():
            pairs = [
                (x, y) for x, y in zip(group["x"], group["y"]) if x in kept_times
            ]
            group["x"] = [p[0] for p in pairs]
            group["y"] = [p[1] for p in pairs]

    # ---------- 1. 组合权益 ----------

    equity_line = Line()
    equity_line.add_xaxis(x_data)
    equity_line.add_yaxis(
        series_name=_t(language, "floating_equity"),
        y_axis=floating_values,
        is_smooth=False,
        is_symbol_show=False,
        label_opts=opts.LabelOpts(is_show=False),
        linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.9),
    )
    equity_line.add_yaxis(
        series_name=_t(language, "realized_equity"),
        y_axis=realized_values,
        is_smooth=False,
        is_step=True,
        is_symbol_show=False,
        label_opts=opts.LabelOpts(is_show=False),
        linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.7),
    )
    equity_line.set_global_opts(
        title_opts=opts.TitleOpts(
            title=title,
            subtitle=_t(language, "subtitle"),
            pos_left="center",
            pos_top="1%",
        ),
        legend_opts=opts.LegendOpts(pos_left="center", pos_top="7%"),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        datazoom_opts=[
            opts.DataZoomOpts(
                type_="inside",
                xaxis_index=[0, 1, 2],
                range_start=0,
                range_end=100,
                filter_mode="filter",
            ),
            opts.DataZoomOpts(
                type_="slider",
                xaxis_index=[0, 1, 2],
                range_start=0,
                range_end=100,
                filter_mode="filter",
                pos_bottom="1%",
                height=18,
            ),
        ],
        xaxis_opts=opts.AxisOpts(
            type_="category",
            boundary_gap=False,
            axislabel_opts=opts.LabelOpts(is_show=False),
        ),
        yaxis_opts=opts.AxisOpts(
            type_="value",
            name=_t(language, "equity_axis"),
            is_scale=True,
            splitarea_opts=opts.SplitAreaOpts(is_show=True),
        ),
    )

    # ---------- 2. 各标的敞口 ----------

    exposure_line = Line()
    exposure_line.add_xaxis(x_data)

    for symbol in symbols:
        exposure_line.add_yaxis(
            series_name=symbol,
            y_axis=exposure_series[symbol],
            is_smooth=False,
            is_step=True,
            is_symbol_show=False,
            label_opts=opts.LabelOpts(is_show=False),
            linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.85),
        )

    exposure_line.set_global_opts(
        legend_opts=opts.LegendOpts(pos_left="center", pos_top="40%"),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        xaxis_opts=opts.AxisOpts(
            type_="category",
            boundary_gap=False,
            axislabel_opts=opts.LabelOpts(is_show=False),
        ),
        yaxis_opts=opts.AxisOpts(
            type_="value",
            name=_t(language, "exposure_axis"),
            is_scale=True,
            split_number=3,
        ),
    )

    # ---------- 3. 归一化价格 + 成交标记 ----------

    price_line = Line()
    price_line.add_xaxis(x_data)

    for symbol in symbols:
        price_line.add_yaxis(
            series_name=symbol,
            y_axis=normalized[symbol],
            is_smooth=False,
            is_symbol_show=False,
            label_opts=opts.LabelOpts(is_show=False),
            linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.85),
        )

    price_line.set_global_opts(
        legend_opts=opts.LegendOpts(pos_left="center", pos_top="64%"),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        xaxis_opts=opts.AxisOpts(
            type_="category",
            boundary_gap=False,
            axislabel_opts=opts.LabelOpts(is_show=False),
        ),
        yaxis_opts=opts.AxisOpts(
            type_="value",
            name=_t(language, "price_axis"),
            is_scale=True,
            split_number=3,
        ),
    )

    for key, group in marker_groups.items():
        if not group["x"]:
            continue

        scatter = Scatter()
        scatter.add_xaxis(group["x"])
        scatter.add_yaxis(
            series_name=_t(language, key),
            y_axis=group["y"],
            symbol=group["symbol"],
            symbol_size=group["size"],
            label_opts=opts.LabelOpts(is_show=False),
        )
        price_line = price_line.overlap(scatter)

    # ---------- 布局 ----------

    grid = Grid(
        init_opts=opts.InitOpts(
            width="100vw",
            height="100vh",
            page_title=title,
        )
    )

    grid.add(
        equity_line,
        grid_opts=opts.GridOpts(
            pos_top="12%", pos_bottom="64%", pos_left="6%", pos_right="4%",
            is_contain_label=True,
        ),
    )
    grid.add(
        exposure_line,
        grid_opts=opts.GridOpts(
            pos_top="44%", pos_bottom="42%", pos_left="6%", pos_right="4%",
            is_contain_label=True,
        ),
    )
    grid.add(
        price_line,
        grid_opts=opts.GridOpts(
            pos_top="68%", pos_bottom="8%", pos_left="6%", pos_right="4%",
            is_contain_label=True,
        ),
    )

    grid.render(output_html_name)

    # 单次读写完成全部后处理：历史切换器 + 全屏自适应
    with open(output_html_name, "r", encoding="utf-8") as f:
        html = f.read()

    html = _apply_switcher_block(html)
    html = _apply_responsive(html)

    with open(output_html_name, "w", encoding="utf-8") as f:
        f.write(html)

    _sync_chart_history(output_dir)

    print(f"组合回测图表已生成：{output_html_name}")

    if auto_open:
        webbrowser.open(output_html_name)

    return output_html_name
