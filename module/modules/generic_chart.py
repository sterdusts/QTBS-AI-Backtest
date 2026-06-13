import os
import json

from pyecharts.charts import Line, Grid, Kline, Bar, Scatter
from pyecharts import options as opts

from module.modules.file_naming import build_timestamped_filename


LANG_TEXT = {
    "zh": {
        "lang_name": "中文",
        "chart_title": "策略回测图表",
        "subtitle": "主图权益走势为归一化叠加；下方权益图显示真实数值",
        "price": "价格",
        "equity": "权益",
        "volume": "成交量",
        "position": "仓位",
        "kline": "K线",
        "floating_equity_trend": "实时权益走势",
        "realized_equity_trend": "已实现权益走势",
        "floating_equity_real": "实时权益真实值",
        "realized_equity_real": "已实现权益真实值",
        "entry": "开仓点",
        "exit": "平仓点",
        "language": "语言",
        "open": "开盘",
        "close": "收盘",
        "lowest": "最低",
        "highest": "最高",
    },
    "en": {
        "lang_name": "English",
        "chart_title": "Strategy Backtest Chart",
        "subtitle": "Equity trend is normalized on the main chart; real equity values are shown below.",
        "price": "Price",
        "equity": "Equity",
        "volume": "Volume",
        "position": "Position",
        "kline": "Candlestick",
        "floating_equity_trend": "Floating Equity Trend",
        "realized_equity_trend": "Realized Equity Trend",
        "floating_equity_real": "Floating Equity Value",
        "realized_equity_real": "Realized Equity Value",
        "entry": "Entry",
        "exit": "Exit",
        "language": "Language",
        "open": "Open",
        "close": "Close",
        "lowest": "Low",
        "highest": "High",
    },
    "ko": {
        "lang_name": "한국어",
        "chart_title": "전략 백테스트 차트",
        "subtitle": "메인 차트의 자산 곡선은 정규화 표시이며, 실제 자산 값은 아래 차트에 표시됩니다.",
        "price": "가격",
        "equity": "자산",
        "volume": "거래량",
        "position": "포지션",
        "kline": "캔들",
        "floating_equity_trend": "실시간 자산 추세",
        "realized_equity_trend": "실현 자산 추세",
        "floating_equity_real": "실시간 자산 실제값",
        "realized_equity_real": "실현 자산 실제값",
        "entry": "진입점",
        "exit": "청산점",
        "language": "언어",
        "open": "시가",
        "close": "종가",
        "lowest": "저가",
        "highest": "고가",
    },
    "ja": {
        "lang_name": "日本語",
        "chart_title": "戦略バックテストチャート",
        "subtitle": "メインチャートの資産曲線は正規化表示です。実際の資産値は下のチャートに表示されます。",
        "price": "価格",
        "equity": "資産",
        "volume": "出来高",
        "position": "ポジション",
        "kline": "ローソク足",
        "floating_equity_trend": "リアルタイム資産推移",
        "realized_equity_trend": "実現資産推移",
        "floating_equity_real": "リアルタイム資産実値",
        "realized_equity_real": "実現資産実値",
        "entry": "エントリー",
        "exit": "決済",
        "language": "言語",
        "open": "始値",
        "close": "終値",
        "lowest": "安値",
        "highest": "高値",
    },
    "ru": {
        "lang_name": "Русский",
        "chart_title": "График бэктеста стратегии",
        "subtitle": "Кривая капитала на основном графике нормализована; реальные значения показаны ниже.",
        "price": "Цена",
        "equity": "Капитал",
        "volume": "Объём",
        "position": "Позиция",
        "kline": "Свечи",
        "floating_equity_trend": "Текущий капитал",
        "realized_equity_trend": "Реализованный капитал",
        "floating_equity_real": "Текущий капитал, значение",
        "realized_equity_real": "Реализованный капитал, значение",
        "entry": "Вход",
        "exit": "Выход",
        "language": "Язык",
        "open": "Открытие",
        "close": "Закрытие",
        "lowest": "Минимум",
        "highest": "Максимум",
    },
    "ar": {
        "lang_name": "العربية",
        "chart_title": "مخطط اختبار الاستراتيجية",
        "subtitle": "منحنى رأس المال في الرسم الرئيسي مُطبّع؛ القيم الحقيقية تظهر في الرسم السفلي.",
        "price": "السعر",
        "equity": "رأس المال",
        "volume": "الحجم",
        "position": "المركز",
        "kline": "الشموع",
        "floating_equity_trend": "اتجاه رأس المال الحالي",
        "realized_equity_trend": "اتجاه رأس المال المحقق",
        "floating_equity_real": "القيمة الحالية لرأس المال",
        "realized_equity_real": "القيمة المحققة لرأس المال",
        "entry": "نقطة الدخول",
        "exit": "نقطة الخروج",
        "language": "اللغة",
        "open": "الافتتاح",
        "close": "الإغلاق",
        "lowest": "الأدنى",
        "highest": "الأعلى",
    },
}


# 图表标题模板的唯一出处：Python 首次渲染与注入页面的 JS 语言切换
# 共用同一份（此前两侧各写一份格式串，JS 版在页面 load 后必定覆盖
# Python 版，只改一侧的修改 0.4 秒后就被静默冲掉）
CHART_TITLE_TEMPLATES = {
    "zh": "{symbol} {interval} 策略回测图表",
    "en": "{symbol} {interval} Strategy Backtest Chart",
    "ko": "{symbol} {interval} 전략 백테스트 차트",
    "ja": "{symbol} {interval} 戦略バックテストチャート",
    "ru": "{symbol} {interval} график бэктеста стратегии",
    "ar": "{symbol} {interval} مخطط اختبار الاستراتيجية",
}


def make_translator(table):
    """
    图表文案查找工厂：generic 与 portfolio 共用同一回退语义——
    语言缺失回退 zh，键缺失回退 zh 的同键，再缺失回退键名，绝不抛 KeyError
    （文案表漏一个键只应显示降级文本，不应让整次回测在出图环节崩溃）。
    """

    def translate(language: str, key: str) -> str:
        lang_table = table.get(language) or table["zh"]
        return lang_table.get(key, table["zh"].get(key, key))

    return translate


_t = make_translator(LANG_TEXT)


def _to_str_time(x):
    return str(x)


def _get_df(result: dict):
    df = result.get("df", None)
    if df is not None:
        return df

    df = result.get("data", None)
    if df is not None:
        return df

    df = result.get("kline_data", None)
    if df is not None:
        return df

    return None


def _infer_symbol_interval(result: dict, file_prefix: str):
    symbol = (
        result.get("symbol")
        or result.get("trade_symbol")
        or result.get("trading_symbol")
        or result.get("pair")
        or result.get("ticker")
    )

    interval = (
        result.get("interval")
        or result.get("timeframe")
        or result.get("period")
    )

    if (symbol is None or interval is None) and file_prefix:
        parts = file_prefix.split("_")
        if len(parts) >= 2:
            if symbol is None:
                symbol = parts[0]
            if interval is None:
                interval = parts[1]

    symbol = str(symbol).upper() if symbol else ""
    interval = str(interval) if interval else ""

    return symbol, interval


def _build_chart_title(result: dict, file_prefix: str, language: str, title: str):
    symbol, interval = _infer_symbol_interval(result, file_prefix)

    template = CHART_TITLE_TEMPLATES.get(language)
    if symbol and interval and template:
        return template.format(symbol=symbol, interval=interval)

    return title if title else _t(language, "chart_title")


def _get_time_list(df):
    if "time" in df.columns:
        return [_to_str_time(x) for x in df["time"].tolist()]
    if "datetime" in df.columns:
        return [_to_str_time(x) for x in df["datetime"].tolist()]
    if "open_time" in df.columns:
        return [_to_str_time(x) for x in df["open_time"].tolist()]
    return [_to_str_time(x) for x in df.index.tolist()]


def _align_curve_to_x_data(curve, x_data):
    curve_map = {}

    if curve is None:
        return []

    for item in curve:
        if isinstance(item, dict) and "time" in item and "equity" in item:
            curve_map[_to_str_time(item["time"])] = round(float(item["equity"]), 4)

    return [curve_map.get(t, None) for t in x_data]


def _normalize_multiple_curves_to_price_area(curves, price_values):
    """
    多条权益曲线统一归一化到价格区域。
    重点：
    1. 多条曲线共享同一个 min/max，避免实时权益和已实现权益岔开。
    2. 只压缩到价格图下方一小段区域，避免把K线视觉压扁。
    """

    clean_prices = [float(v) for v in price_values if v == v]

    if len(clean_prices) == 0:
        return curves

    price_min = min(clean_prices)
    price_max = max(clean_prices)

    if price_max == price_min:
        return curves

    all_values = []

    for curve in curves:
        all_values.extend([v for v in curve if v is not None])

    if len(all_values) == 0:
        return curves

    value_min = min(all_values)
    value_max = max(all_values)

    price_range = price_max - price_min

    # 关键改这里：
    # 不再占满 8%~92%，而是只放在价格图下方 8%~28%
    target_min = price_min + price_range * 0.08
    target_max = price_min + price_range * 0.70

    if value_max == value_min:
        middle = (target_min + target_max) / 2
        return [
            [None if v is None else middle for v in curve]
            for curve in curves
        ]

    normalized_curves = []

    for curve in curves:
        normalized = []

        for v in curve:
            if v is None:
                normalized.append(None)
            else:
                new_v = (
                    target_min
                    + (v - value_min)
                    / (value_max - value_min)
                    * (target_max - target_min)
                )
                normalized.append(round(float(new_v), 6))

        normalized_curves.append(normalized)

    return normalized_curves

def _build_trade_points_from_trades(trades):
    open_x, open_y = [], []
    close_x, close_y = [], []

    if trades is None:
        return open_x, open_y, close_x, close_y

    for trade in trades:
        if not isinstance(trade, dict):
            continue

        entry_time = (
            trade.get("entry_time")
            or trade.get("open_time")
            or trade.get("buy_time")
            or trade.get("short_time")
        )
        entry_price = (
            trade.get("entry_price")
            or trade.get("open_price")
            or trade.get("buy_price")
            or trade.get("short_price")
        )

        exit_time = (
            trade.get("exit_time")
            or trade.get("close_time")
            or trade.get("sell_time")
            or trade.get("cover_time")
        )
        exit_price = (
            trade.get("exit_price")
            or trade.get("close_price")
            or trade.get("sell_price")
            or trade.get("cover_price")
        )

        action = str(trade.get("action", "")).lower()

        if entry_time is not None and entry_price is not None:
            open_x.append(_to_str_time(entry_time))
            open_y.append(round(float(entry_price), 6))

        if exit_time is not None and exit_price is not None:
            close_x.append(_to_str_time(exit_time))
            close_y.append(round(float(exit_price), 6))

        if "time" in trade and "price" in trade:
            t = _to_str_time(trade["time"])
            p = round(float(trade["price"]), 6)

            if any(k in action for k in ["开", "buy", "long", "short", "entry", "open"]):
                open_x.append(t)
                open_y.append(p)
            elif any(k in action for k in ["平", "sell", "exit", "close"]):
                close_x.append(t)
                close_y.append(p)

    return open_x, open_y, close_x, close_y


def _build_trade_points_from_position(df, x_data):
    open_x, open_y = [], []
    close_x, close_y = [], []

    if "target_position" not in df.columns or "close" not in df.columns:
        return open_x, open_y, close_x, close_y

    position = df["target_position"].fillna(0).astype(float).tolist()
    close = df["close"].astype(float).tolist()

    prev = 0

    for i, pos in enumerate(position):
        if i >= len(x_data):
            break

        if prev == 0 and pos != 0:
            open_x.append(x_data[i])
            open_y.append(round(close[i], 6))

        elif prev != 0 and pos == 0:
            close_x.append(x_data[i])
            close_y.append(round(close[i], 6))

        elif prev != 0 and pos != 0 and prev != pos:
            close_x.append(x_data[i])
            close_y.append(round(close[i], 6))
            open_x.append(x_data[i])
            open_y.append(round(close[i], 6))

        prev = pos

    return open_x, open_y, close_x, close_y

# ---------------------------------------------------------
# 历史图表切换器（manifest 方案）
#
# 文件清单只写进一个几 KB 的 manifest.js，每张图注入一段读取
# manifest 的加载器（file:// 下 <script src> 不受 CORS 限制）。
# 旧方案是每次出图把目录下所有历史 HTML 整读整写一遍——单文件
# HTML 内嵌全部序列数据（MB~百 MB 级），代价随历史数量线性增长。
# ---------------------------------------------------------

_SWITCHER_MANIFEST_NAME = "qtbs_chart_manifest.js"
_SWITCHER_START = "<!-- QTBS_MANIFEST_SWITCHER_START -->"
_SWITCHER_END = "<!-- QTBS_MANIFEST_SWITCHER_END -->"
_LEGACY_SWITCHER_START = "<!-- QTBS_HTML_FILE_SWITCHER_START -->"
_LEGACY_SWITCHER_END = "<!-- QTBS_HTML_FILE_SWITCHER_END -->"

# 进程内已做过旧格式迁移的目录：迁移是一次 O(全部历史) 的读写，
# 每个进程每个目录只做一次，之后出图只写 manifest + 新文件本身
_MIGRATED_DIRS: set = set()


def _build_switcher_block() -> str:
    return f"""
{_SWITCHER_START}
<script src="{_SWITCHER_MANIFEST_NAME}"></script>
<div id="html-file-switcher">
    <select id="html-file-select" onchange="switchHtmlFile(this.value)"></select>
</div>

<script>
    function switchHtmlFile(filename) {{
        if (filename) window.location.href = filename;
    }}

    (function () {{
        var files = window.QTBS_CHART_FILES || [];
        var select = document.getElementById("html-file-select");
        var current = window.location.pathname.split(/[\\\\/]/).pop();
        try {{ current = decodeURIComponent(current); }} catch (e) {{}}

        var found = false;
        files.forEach(function (name) {{
            var option = document.createElement("option");
            option.value = name;
            option.textContent = name;
            if (name === current) {{ option.selected = true; found = true; }}
            select.appendChild(option);
        }});

        // 当前文件比 manifest 更新（极少见的竞态）时兜底显示自己
        if (!found && current) {{
            var option = document.createElement("option");
            option.value = current;
            option.textContent = current;
            option.selected = true;
            select.insertBefore(option, select.firstChild);
        }}
    }})();
</script>

<style>
    #html-file-switcher {{
        position: fixed;
        top: 10px;
        right: 150px;
        z-index: 999999;
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid #dcdfe6;
        border-radius: 8px;
        padding: 6px 10px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
    }}

    #html-file-select {{
        width: 280px;
        height: 28px;
        border: 1px solid #cfd3dc;
        border-radius: 6px;
        background: white;
        color: #303133;
        padding: 3px 6px;
        font-size: 13px;
        outline: none;
    }}

    #html-file-select:hover {{
        border-color: #a8abb2;
    }}
</style>
{_SWITCHER_END}
"""


def _apply_switcher_block(html_text: str) -> str:
    """剥掉旧格式内嵌清单（如有），注入/替换 manifest 加载器（幂等）。"""

    block = _build_switcher_block()

    if _LEGACY_SWITCHER_START in html_text and _LEGACY_SWITCHER_END in html_text:
        start = html_text.find(_LEGACY_SWITCHER_START)
        end = html_text.find(_LEGACY_SWITCHER_END) + len(_LEGACY_SWITCHER_END)
        html_text = html_text[:start] + html_text[end:]

    if _SWITCHER_START in html_text and _SWITCHER_END in html_text:
        start = html_text.find(_SWITCHER_START)
        end = html_text.find(_SWITCHER_END) + len(_SWITCHER_END)
        return html_text[:start] + block + html_text[end:]

    # 插在最后一个 </body> 前（首个可能落在内嵌数据里）
    idx = html_text.rfind("</body>")
    if idx != -1:
        return html_text[:idx] + block + "\n" + html_text[idx:]

    return html_text


def _tail_contains(file_path: str, marker: str) -> bool:
    """只读文件尾部探测标记是否存在。

    切换器块注入在 </body> 前、离文件尾很近：不必为一次格式探测
    把上百 MB 的单文件 HTML 整个读进内存。
    """

    size = os.path.getsize(file_path)

    with open(file_path, "rb") as f:
        f.seek(max(0, size - 262_144))
        return marker.encode("utf-8") in f.read()


def _sync_chart_history(output_dir: str):
    """
    维护历史图表切换器。**绝不向上抛异常**：历史目录里的脏文件、
    编码异常或文件占用，不应让已经成功完成的回测与出图被报告为失败。

    1. 重写 manifest.js（全部 HTML 文件名，按 mtime 新→旧）
    2. 首次调用时把含旧格式标记的自家文件迁移到 manifest 加载器
       （每进程每目录一次；逐文件异常隔离；只动带 QTBS 旧标记的文件，
       外来 HTML 不改写）

    新生成的 HTML 由调用方在写盘前用 _apply_switcher_block 注入，
    到这里已是新格式，迁移循环的尾部探测会直接跳过它。
    """

    html_files = []

    if os.path.exists(output_dir):
        for filename in os.listdir(output_dir):
            if not filename.endswith(".html"):
                continue
            file_path = os.path.join(output_dir, filename)
            try:
                html_files.append((filename, os.path.getmtime(file_path)))
            except OSError:
                continue  # listdir 与 stat 之间被删除等竞态

    html_files.sort(key=lambda item: item[1], reverse=True)
    names = [name for name, _ in html_files]

    manifest_path = os.path.join(output_dir, _SWITCHER_MANIFEST_NAME)
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(
                "window.QTBS_CHART_FILES = "
                + json.dumps(names, ensure_ascii=False) + ";"
            )
    except OSError as e:
        # manifest 写失败：新图表的切换器会退化为只显示自身，不致命
        print(f"历史图表清单写入失败（不影响本次出图）: {e}")

    dir_key = os.path.abspath(output_dir)
    if dir_key in _MIGRATED_DIRS:
        return

    for name in names:
        file_path = os.path.join(output_dir, name)

        try:
            if _tail_contains(file_path, _SWITCHER_START):
                continue  # 已是新格式

            # 只迁移带 QTBS 旧标记的自家文件：
            # 用户存进目录的外来 HTML 不应被改写内容
            if not _tail_contains(file_path, _LEGACY_SWITCHER_START):
                continue

            with open(file_path, "r", encoding="utf-8") as f:
                old_text = f.read()

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(_apply_switcher_block(old_text))
        except (OSError, UnicodeError) as e:
            # 单个坏文件（占用锁定/编码异常）跳过，不阻断其余迁移与本次出图
            print(f"历史图表迁移跳过 {name}: {e}")
            continue

    _MIGRATED_DIRS.add(dir_key)

# 全屏自适应样式：generic 与 portfolio 图表共用。
# .chart-container 是 pyecharts 生成的容器类名——若升级 pyecharts 后
# 图表白屏/裁切，只需要改这一处，两类图表同时生效。
FULLSCREEN_CSS = """
<style>
html, body {
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 0;
    overflow: hidden;
    background: #ffffff;
}

.chart-container {
    width: 100vw !important;
    height: 100vh !important;
}
</style>
"""

# 仅供不带多语言面板的最小注入路径（portfolio 图表）使用；
# generic 图表的 resize 监听在多语言脚本内（语言切换后需要重绘）
RESIZE_JS = """
<script>
window.addEventListener("resize", function() {
    setTimeout(function() {
        if (typeof echarts === "undefined") return;
        document.querySelectorAll("div[_echarts_instance_]").forEach(function(dom) {
            var chart = echarts.getInstanceByDom(dom);
            if (chart) chart.resize();
        });
    }, 100);
});
</script>
"""


def _apply_responsive(html: str) -> str:
    """全屏 CSS + 窗口缩放跟随（不带多语言面板的最小版本，纯文本变换）。"""

    # </head> 取首个、</body> 取最后一个且只插一处：内嵌的序列数据里
    # 若出现同名字面量，replace 全替换会把 CSS/JS 重复注入到数据中间
    if "</head>" in html:
        html = html.replace("</head>", FULLSCREEN_CSS + "\n</head>", 1)

    idx = html.rfind("</body>")
    if idx != -1:
        html = html[:idx] + RESIZE_JS + "\n" + html[idx:]

    return html


def _apply_responsive_and_multilingual(
    html: str,
    default_language: str = "zh",
    symbol: str = "",
    interval: str = ""
) -> str:
    """全屏自适应 + 多语言面板（纯文本变换，由调用方统一读写文件）。"""

    language_options = ""
    for code, info in LANG_TEXT.items():
        selected = "selected" if code == default_language else ""
        language_options += f'<option value="{code}" {selected}>{info["lang_name"]}</option>'

    translations_json = json.dumps(LANG_TEXT, ensure_ascii=False)
    title_templates_json = json.dumps(CHART_TITLE_TEMPLATES, ensure_ascii=False)
    meta_json = json.dumps(
        {
            "symbol": symbol,
            "interval": interval,
        },
        ensure_ascii=False
    )



    css = FULLSCREEN_CSS + """
<style>
#qtbs-language-panel {
    position: fixed;
    top: 10px;
    right: 16px;
    z-index: 999999;
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    padding: 6px 10px;
    font-family: Arial, sans-serif;
    font-size: 13px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

#qtbs-language-panel select {
    border: 1px solid #cfd3dc;
    border-radius: 6px;
    padding: 3px 6px;
    background: white;
}
</style>
"""

    js = f"""
<div id="qtbs-language-panel">
    <span id="qtbs-language-label">{_t(default_language, "language")}</span>
    <select id="qtbs-language-select">
        {language_options}
    </select>
</div>

<script>
const QTBS_TRANSLATIONS = {translations_json};
const QTBS_TITLE_TEMPLATES = {title_templates_json};
const QTBS_CHART_META = {meta_json};
const QTBS_INITIAL_LANGUAGE = "{default_language}";
let QTBS_CURRENT_LANGUAGE = QTBS_INITIAL_LANGUAGE;

function qtbsGetChart() {{
    if (typeof echarts === "undefined") return null;

    const chartDoms = document.querySelectorAll("div[_echarts_instance_]");
    if (!chartDoms || chartDoms.length === 0) return null;

    return echarts.getInstanceByDom(chartDoms[0]);
}}

function qtbsResizeAllCharts() {{
    if (typeof echarts === "undefined") return;

    const chartDoms = document.querySelectorAll("div[_echarts_instance_]");
    chartDoms.forEach(function(dom) {{
        const chart = echarts.getInstanceByDom(dom);
        if (chart) chart.resize();
    }});
}}

function qtbsBuildTitle(lang) {{
    const t = QTBS_TRANSLATIONS[lang] || QTBS_TRANSLATIONS["zh"];
    const symbol = QTBS_CHART_META.symbol || "";
    const interval = QTBS_CHART_META.interval || "";
    const template = QTBS_TITLE_TEMPLATES[lang];

    if (symbol && interval && template) {{
        return template.replace("{{symbol}}", symbol).replace("{{interval}}", interval);
    }}

    return t.chart_title;
}}

function qtbsBuildNameMap(targetLang) {{
    const target = QTBS_TRANSLATIONS[targetLang] || QTBS_TRANSLATIONS["zh"];

    const keys = [
        "price",
        "equity",
        "volume",
        "position",
        "kline",
        "floating_equity_trend",
        "realized_equity_trend",
        "floating_equity_real",
        "realized_equity_real",
        "entry",
        "exit"
    ];

    const map = {{}};

    Object.keys(QTBS_TRANSLATIONS).forEach(function(lang) {{
        const source = QTBS_TRANSLATIONS[lang];

        keys.forEach(function(key) {{
            if (source[key] && target[key]) {{
                map[source[key]] = target[key];
            }}
        }});
    }});

    map["Candlestick"] = target.kline;
    map["Floating Equity Trend"] = target.floating_equity_trend;
    map["Realized Equity Trend"] = target.realized_equity_trend;
    map["Floating Equity Value"] = target.floating_equity_real;
    map["Realized Equity Value"] = target.realized_equity_real;
    map["Entry"] = target.entry;
    map["Exit"] = target.exit;
    map["Volume"] = target.volume;
    map["Position"] = target.position;
    map["Price"] = target.price;
    map["Equity"] = target.equity;

    map["K线"] = target.kline;
    map["实时权益走势"] = target.floating_equity_trend;
    map["已实现权益走势"] = target.realized_equity_trend;
    map["实时权益真实值"] = target.floating_equity_real;
    map["已实现权益真实值"] = target.realized_equity_real;
    map["开仓点"] = target.entry;
    map["平仓点"] = target.exit;
    map["成交量"] = target.volume;
    map["仓位"] = target.position;
    map["价格"] = target.price;
    map["权益"] = target.equity;

    return map;
}}

function qtbsFormatNumber(value) {{
    if (value === null || value === undefined || value === "-") return "-";
    const num = Number(value);
    if (Number.isNaN(num)) return value;
    return num.toLocaleString(undefined, {{
        maximumFractionDigits: 6
    }});
}}

function qtbsTooltipFormatter(params) {{
    const lang = QTBS_CURRENT_LANGUAGE || "zh";
    const t = QTBS_TRANSLATIONS[lang] || QTBS_TRANSLATIONS["zh"];
    const nameMap = qtbsBuildNameMap(lang);

    if (!Array.isArray(params)) {{
        params = [params];
    }}

    if (!params.length) return "";

    let html = "";
    html += "<div style='font-size:14px;margin-bottom:6px;'>" + params[0].axisValue + "</div>";

    params.forEach(function(p) {{
        const rawName = p.seriesName || "";
        const name = nameMap[rawName] || rawName;
        const marker = p.marker || "";

        if (rawName === "Candlestick" || rawName === "K线" || name === t.kline) {{
            const v = p.data || p.value || [];
            const open = v[1] !== undefined ? v[1] : v[0];
            const close = v[2] !== undefined ? v[2] : v[1];
            const low = v[3] !== undefined ? v[3] : v[2];
            const high = v[4] !== undefined ? v[4] : v[3];

            html += "<div style='margin-top:4px;'>" + marker + name + "</div>";
            html += "<div style='padding-left:14px;'>" + t.open + ": <b>" + qtbsFormatNumber(open) + "</b></div>";
            html += "<div style='padding-left:14px;'>" + t.close + ": <b>" + qtbsFormatNumber(close) + "</b></div>";
            html += "<div style='padding-left:14px;'>" + t.lowest + ": <b>" + qtbsFormatNumber(low) + "</b></div>";
            html += "<div style='padding-left:14px;'>" + t.highest + ": <b>" + qtbsFormatNumber(high) + "</b></div>";
        }} else {{
            html += "<div>" + marker + name + ": <b>" + qtbsFormatNumber(p.value) + "</b></div>";
        }}
    }});

    return html;
}}

function qtbsApplyLanguage(lang) {{
    QTBS_CURRENT_LANGUAGE = lang;

    const chart = qtbsGetChart();
    const t = QTBS_TRANSLATIONS[lang] || QTBS_TRANSLATIONS["zh"];

    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";

    const label = document.getElementById("qtbs-language-label");
    if (label) label.innerText = t.language;

    if (!chart) return;

    const option = chart.getOption();
    const nameMap = qtbsBuildNameMap(lang);

    if (option.title && option.title[0]) {{
        option.title[0].text = qtbsBuildTitle(lang);
        option.title[0].subtext = t.subtitle;
    }}

    if (option.series) {{
        option.series.forEach(function(s) {{
            if (nameMap[s.name]) s.name = nameMap[s.name];
        }});
    }}

    if (option.yAxis) {{
        option.yAxis.forEach(function(y) {{
            if (nameMap[y.name]) y.name = nameMap[y.name];
        }});
    }}

    if (option.legend) {{
        option.legend.forEach(function(lg) {{
            if (lg.data) {{
                lg.data = lg.data.map(function(name) {{
                    return nameMap[name] || name;
                }});
            }}
        }});
    }}

    if (option.tooltip) {{
        option.tooltip.forEach(function(tp) {{
            tp.formatter = qtbsTooltipFormatter;
        }});
    }}

    chart.setOption(option, true);
    setTimeout(qtbsResizeAllCharts, 100);
}}

window.addEventListener("load", function() {{
    const selector = document.getElementById("qtbs-language-select");

    if (selector) {{
        selector.addEventListener("change", function() {{
            qtbsApplyLanguage(this.value);
        }});
    }}

    setTimeout(function() {{
        qtbsApplyLanguage(QTBS_INITIAL_LANGUAGE);
        qtbsResizeAllCharts();
    }}, 400);
}});

window.addEventListener("resize", function() {{
    setTimeout(qtbsResizeAllCharts, 100);
}});
</script>
"""

    # 与 _apply_responsive 同理：定位单一插入点，避免数据中的同名
    # 字面量被 replace 全替换造成重复注入
    if "</head>" in html:
        html = html.replace("</head>", css + "\n</head>", 1)

    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + js, 1)
    else:
        idx = html.rfind("</body>")
        if idx != -1:
            html = html[:idx] + js + "\n" + html[idx:]

    return html


def plot_generic_equity_curves(
    result: dict,
    output_dir: str = "Past_data",
    file_prefix: str = "generic_strategy",
    title: str = "",
    auto_open: bool = True,
    focus_mode: str = "both",
    language: str = "zh",
    default_visible_percent: int = 8,
):
    language = language if language in LANG_TEXT else "zh"

    # 缩放模式：用百分比控制默认可见范围，但价格轴仍然保持真实价格。
    # 8 表示默认只显示最后 8% 的K线；想更放大就改成 5，想看更多就改成 15/20。
    try:
        default_visible_percent = int(default_visible_percent)
    except Exception:
        default_visible_percent = 8

    default_visible_percent = max(3, min(default_visible_percent, 100))
    datazoom_start = max(0, 100 - default_visible_percent)
    datazoom_end = 100

    os.makedirs(output_dir, exist_ok=True)

    output_html_name = os.path.join(
        output_dir, build_timestamped_filename(file_prefix, ".html")
    )

    symbol, interval = _infer_symbol_interval(result, file_prefix)
    chart_title = _build_chart_title(result, file_prefix, language, title)

    df = _get_df(result)
    trades = result.get("trades", [])
    equity_curve = result.get("equity_curve", [])
    realized_equity_curve = result.get("realized_equity_curve", [])

    has_kline = (
        df is not None
        and hasattr(df, "columns")
        and all(col in df.columns for col in ["open", "high", "low", "close"])
    )

    if not has_kline:
        raise ValueError(
            "result 中没有可用于绘制K线的 df。请确认 result 里包含 df，并且有 open/high/low/close 列。"
        )

    x_data = _get_time_list(df)

    if focus_mode == "price_focus":
        equity_opacity = 0.28
    elif focus_mode == "equity_focus":
        equity_opacity = 0.95
    else:
        equity_opacity = 0.65

    grid = Grid(
        init_opts=opts.InitOpts(
            width="100vw",
            height="100vh",
            page_title=chart_title
        )
    )

    datazoom = [
        opts.DataZoomOpts(
            type_="inside",
            xaxis_index=[0, 1, 2, 3],
            range_start=datazoom_start,
            range_end=datazoom_end,
            filter_mode="filter",
        ),
        opts.DataZoomOpts(
            type_="slider",
            xaxis_index=[0, 1, 2, 3],
            range_start=datazoom_start,
            range_end=datazoom_end,
            filter_mode="filter",
            pos_bottom="1%",
            height=18,
        ),
    ]

    kline_data = []
    for _, row in df.iterrows():
        kline_data.append([
            round(float(row["open"]), 6),
            round(float(row["close"]), 6),
            round(float(row["low"]), 6),
            round(float(row["high"]), 6),
        ])

    kline = Kline()
    kline.add_xaxis(x_data)
    kline.add_yaxis(
        series_name=_t(language, "kline"),
        y_axis=kline_data,
        itemstyle_opts=opts.ItemStyleOpts(
            # ECharts/Pyecharts: color 是上涨K线，color0 是下跌K线。
            # 这里保持国内常用习惯：绿涨、红跌。
            color="#14b143",
            color0="#ef232a",
            border_color="#14b143",
            border_color0="#ef232a",
        ),
    )

    price_values = df["close"].astype(float).tolist()

    overlay_equity_line = Line()
    overlay_equity_line.add_xaxis(x_data)

    floating_equity_raw = []
    realized_equity_raw = []

    if equity_curve is not None and len(equity_curve) > 0:
        floating_equity_raw = _align_curve_to_x_data(equity_curve, x_data)
    else:
        floating_equity_raw = []

    if realized_equity_curve is not None and len(realized_equity_curve) > 0:
        realized_equity_raw = _align_curve_to_x_data(realized_equity_curve, x_data)
    else:
        realized_equity_raw = []

    # 统一归一化
    floating_equity_overlay, realized_equity_overlay = _normalize_multiple_curves_to_price_area(
        [floating_equity_raw, realized_equity_raw],
        price_values
    )

    # 添加到 overlay_equity_line
    if len(floating_equity_overlay) > 0:
        overlay_equity_line.add_yaxis(
            series_name=_t(language, "floating_equity_trend"),
            y_axis=floating_equity_overlay,
            is_smooth=False,
            is_symbol_show=False,
            tooltip_opts=opts.TooltipOpts(is_show=False),
            label_opts=opts.LabelOpts(is_show=False),
            linestyle_opts=opts.LineStyleOpts(width=2, opacity=equity_opacity),
        )

    if len(realized_equity_overlay) > 0:
        overlay_equity_line.add_yaxis(
            series_name=_t(language, "realized_equity_trend"),
            y_axis=realized_equity_overlay,
            is_smooth=False,
            is_step=True,
            is_symbol_show=False,
            tooltip_opts=opts.TooltipOpts(is_show=False),
            label_opts=opts.LabelOpts(is_show=False),
            linestyle_opts=opts.LineStyleOpts(width=2, opacity=max(equity_opacity - 0.18, 0.2)),
        )

    kline = kline.overlap(overlay_equity_line)

    open_x, open_y, close_x, close_y = _build_trade_points_from_trades(trades)
    if len(open_x) == 0 and len(close_x) == 0:
        open_x, open_y, close_x, close_y = _build_trade_points_from_position(df, x_data)

    if len(open_x) > 0:
        open_scatter = Scatter()
        open_scatter.add_xaxis(open_x)
        open_scatter.add_yaxis(
            series_name=_t(language, "entry"),
            y_axis=open_y,
            symbol="triangle",
            symbol_size=12,
            label_opts=opts.LabelOpts(is_show=False),
        )
        kline = kline.overlap(open_scatter)

    if len(close_x) > 0:
        close_scatter = Scatter()
        close_scatter.add_xaxis(close_x)
        close_scatter.add_yaxis(
            series_name=_t(language, "exit"),
            y_axis=close_y,
            symbol="diamond",
            symbol_size=10,
            label_opts=opts.LabelOpts(is_show=False),
        )
        kline = kline.overlap(close_scatter)

    kline.set_global_opts(
        title_opts=opts.TitleOpts(
            title=chart_title,
            subtitle=_t(language, "subtitle"),
            pos_left="center",
            pos_top="1%",
        ),
        legend_opts=opts.LegendOpts(pos_left="center", pos_top="8%"),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        datazoom_opts=datazoom,
        xaxis_opts=opts.AxisOpts(
            type_="category",
            boundary_gap=False,
            axislabel_opts=opts.LabelOpts(is_show=False),
        ),
        yaxis_opts=opts.AxisOpts(
            type_="value",
            name=_t(language, "price"),
            is_scale=True,
            min_="dataMin",
            max_="dataMax",
            splitarea_opts=opts.SplitAreaOpts(is_show=True),
        ),
    )

    grid.add(
        kline,
        grid_opts=opts.GridOpts(pos_top="14%", pos_bottom="43%", pos_left="6%", pos_right="5%", is_contain_label=True),
    )

    if len(floating_equity_raw) > 0 or len(realized_equity_raw) > 0:
        real_equity_line = Line()
        real_equity_line.add_xaxis(x_data)

        if len(floating_equity_raw) > 0:
            real_equity_line.add_yaxis(
                series_name=_t(language, "floating_equity_real"),
                y_axis=floating_equity_raw,
                is_smooth=False,
                is_symbol_show=False,
                label_opts=opts.LabelOpts(is_show=False),
                linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.85),
            )

        if len(realized_equity_raw) > 0:
            real_equity_line.add_yaxis(
                series_name=_t(language, "realized_equity_real"),
                y_axis=realized_equity_raw,
                is_smooth=False,
                is_step=True,
                is_symbol_show=False,
                label_opts=opts.LabelOpts(is_show=False),
                linestyle_opts=opts.LineStyleOpts(width=2, opacity=0.75),
            )

        real_equity_line.set_global_opts(
            legend_opts=opts.LegendOpts(pos_left="center", pos_top="59%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            xaxis_opts=opts.AxisOpts(type_="category", boundary_gap=False, axislabel_opts=opts.LabelOpts(is_show=False)),
            yaxis_opts=opts.AxisOpts(type_="value", name=_t(language, "equity"), is_scale=True, split_number=3),
        )

        grid.add(
            real_equity_line,
            grid_opts=opts.GridOpts(pos_top="63%", pos_bottom="20%", pos_left="7%", pos_right="5%", is_contain_label=True),
        )

    if "volume" in df.columns:
        volume = [round(float(v), 4) for v in df["volume"].fillna(0).tolist()]

        bar = Bar()
        bar.add_xaxis(x_data)
        bar.add_yaxis(
            series_name=_t(language, "volume"),
            y_axis=volume,
            label_opts=opts.LabelOpts(is_show=False),
        )
        bar.set_global_opts(
            legend_opts=opts.LegendOpts(is_show=False),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            xaxis_opts=opts.AxisOpts(type_="category", axislabel_opts=opts.LabelOpts(is_show=False)),
            yaxis_opts=opts.AxisOpts(type_="value", name=_t(language, "volume"), split_number=2),
        )

        grid.add(
            bar,
            grid_opts=opts.GridOpts(pos_top="86%", pos_bottom="7%", pos_left="7%", pos_right="5%", is_contain_label=True),
        )


    grid.render(output_html_name)

    # 单次读写完成全部后处理：历史切换器 + 全屏自适应 + 多语言
    # （1m 级单文件 HTML 可达上百 MB，多一轮读写就多一份磁盘往返）
    with open(output_html_name, "r", encoding="utf-8") as f:
        html = f.read()

    html = _apply_switcher_block(html)
    html = _apply_responsive_and_multilingual(
        html,
        default_language=language,
        symbol=symbol,
        interval=interval,
    )

    with open(output_html_name, "w", encoding="utf-8") as f:
        f.write(html)

    _sync_chart_history(output_dir)

    print(f"回测图表已生成：{output_html_name}")

    if auto_open:
        import webbrowser
        webbrowser.open(output_html_name)

    return output_html_name