import re
import os
import math
import html
import webbrowser
from datetime import datetime, timezone

import gradio as gr
from module.AI.api_config import LANGUAGE_DISPLAY_NAMES, clamp_score
from module.AI.deepseek_strategy_reviewer import review_strategy_code_with_deepseek
from module.AI.deepseek_code_generator import generate_strategy_code_with_deepseek
from module.Strategy.backtest_runner import (
    InsufficientKlinesError,
    load_for_backtest,
    resolve_strategy_route,
    run_once,
    run_prepared,
)
from module.Strategy.behavior_check import format_behavior_summary, run_behavior_check
from module.Strategy.strategy_loader import (
    load_strategy_func_from_code,
    parse_strategy_metadata,
    save_strategy_code_audit,
    validate_strategy_metadata,
)
from module.modules.Load_real_kline import get_base_asset, normalize_symbol
from module.modules.code_backtest_core import CodeBacktestCore
from module.modules.portfolio_backtest_core import PortfolioBacktestCore
from module.modules import fetch_queue
from module.modules.data_panel import (
    DEFAULT_DATA_DIR,
    build_funding_rates,
    filter_df_by_date,
    list_local_symbols,
    load_aligned_panel,
    load_symbol_kline,
)
from module.modules.generic_chart import plot_generic_equity_curves
from module.modules.portfolio_chart import plot_portfolio_result
from module.modules.robustness_chart import plot_robustness
from module.modules.backtest_dashboard import build_dashboard_html, build_dashboard_placeholder
from module.modules.run_history import (
    build_run_record,
    list_run_records,
    load_run_record,
    save_run_record,
)
from module.modules.backtest_dashboard import build_history_detail_html
from module.analysis.robustness import run_full_analysis
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# 语言配置
# =========================================================

LANGUAGE_CHOICES = [
    ("中文", "zh"),
    ("한국어", "ko"),
    ("English", "en"),
    ("日本語", "ja"),
    ("العربية", "ar"),
    ("Русский", "ru"),
]

# AI 输出语言显示名单源在 api_config（LANGUAGE_DISPLAY_NAMES，
# 生成与审查共用同一份，新增语言只改一处）


UI_TEXTS = {
    "zh": {
        "header": """
# QTBS AI 量化策略前端

左边输入自然语言策略，右上角切换语言，右边设置市场、交易标的、时间周期、回测时间、初始资金、杠杆、仓位比例、手续费和滑点。  
先生成策略代码，再运行回测。
""",
        "language_label": "语言",
        "strategy_label": "策略，自然语言输入框",
        "strategy_placeholder": "例如：MA20 上穿 MA60 开多，MA20 下穿 MA60 平仓，不做空。",
        "market_label": "交易市场选择",
        "market_choice": "加密货币",
        "symbol_label": "交易标的",
        "symbol_placeholder": "不输入默认 BTC，例如 BTC / ETH / SOL",
        "param_priority_short": "参数优先级：标的以代码声明优先，其余以本面板为准",
        "param_priority_note": "策略代码声明了标的（SYMBOLS，多标的对冲/组合必然如此）时，交易标的以代码为准、上方输入不生效；周期、回测时间、资金、杠杆、仓位、费率、滑点始终以本面板为准；策略内部参数（回看长度、阈值等）以代码为准。",
        "timeframe_label": "时间周期",
        "start_label": "起始时间",
        "end_label": "结束时间",
        "initial_cash_label": "初始资金",
        "leverage_label": "杠杆倍数",
        "position_size_label": "仓位比例（%）",
        "fee_rate_label": "手续费率（%）",
        "slippage_label": "滑点（%）",
        "generate_button": "生成策略代码",
        "backtest_button": "运行回测",
        "code_output_label": "AI 生成的策略代码",
        "result_output_label": "回测结果",
        "empty_strategy_error": "策略输入不能为空。",
        "start_time_error": "起始时间不能早于 2017-01-01。",
        "invalid_date_error": "日期格式无效。推荐使用 YYYY-MM-DD，例如 2017-01-13；也支持 2017.1.13、2017/1/13、2017年1月13日、13/1/2017。",
        "date_order_error": "结束时间不能早于起始时间。",
        "api_fail_error": "AI API 调用失败",
        "backtest_fail_error": "回测运行失败",
        "no_code_error": "策略代码不能为空，请先生成策略代码。",
        "invalid_number_error": "请输入有效数字。",
        "invalid_initial_cash_error": "初始资金必须大于 0。",
        "invalid_leverage_error": "杠杆倍数必须是 0 到 200 之间的整数。0 表示不启用杠杆，实际按 1 倍计算。",
        "invalid_position_size_error": "仓位比例必须在 0 到 100 之间。",
        "invalid_fee_rate_error": "手续费率不能小于 0。请输入 0 或正数，例如 0.05 表示 0.05%，也就是万分之五。",
        "invalid_slippage_error": "滑点不能小于 0。请输入 0 或正数，例如 0.02 表示 0.02%。",
        "too_few_klines_error": "过滤后的 K线数量太少，当前只有 {count} 条。请扩大时间范围。",
        "view_chart_button": "查看详细图表",
        "view_robustness_chart_button": "查看稳健性图表",
        "view_chart_missing": "暂无可用图表，请先运行一次回测。",
        "history_select_label": "历史记录（选择一条查看）",
        "history_refresh": "刷新历史列表",
        "history_empty": "暂无历史记录",
    },
    "ko": {
        "header": """
# QTBS AI Quant Strategy Frontend

왼쪽에는 자연어 전략을 입력하고, 오른쪽 위에서 언어를 변경하며, 오른쪽에서는 시장, 거래 종목, 시간 주기, 백테스트 기간, 초기 자금, 레버리지, 포지션 비율, 수수료와 슬리피지를 설정합니다.  
먼저 전략 코드를 생성한 뒤 백테스트를 실행합니다.
""",
        "language_label": "언어",
        "strategy_label": "전략 자연어 입력창",
        "strategy_placeholder": "예: MA20이 MA60을 상향 돌파하면 진입, MA20이 MA60을 하향 돌파하면 청산, 공매도 없음.",
        "market_label": "거래 시장 선택",
        "market_choice": "암호화폐",
        "symbol_label": "거래 종목",
        "symbol_placeholder": "입력하지 않으면 기본값은 BTC입니다. 예: BTC / ETH / SOL",
        "param_priority_short": "우선순위: 코드에 선언된 종목이 우선, 나머지는 이 패널 기준",
        "param_priority_note": "전략 코드에 종목(SYMBOLS)이 선언된 경우(멀티 종목 헤지/포트폴리오는 항상 해당) 코드의 종목이 우선하며 위의 거래 종목 입력은 무시됩니다. 주기·백테스트 기간·자금·레버리지·포지션 비율·수수료·슬리피지는 항상 이 패널 기준이고, 전략 내부 파라미터(룩백 길이, 임계값 등)는 코드 기준입니다.",
        "timeframe_label": "시간 주기",
        "start_label": "시작 시간",
        "end_label": "종료 시간",
        "initial_cash_label": "초기 자금",
        "leverage_label": "레버리지",
        "position_size_label": "포지션 비율（%）",
        "fee_rate_label": "수수료율（%）",
        "slippage_label": "슬리피지（%）",
        "generate_button": "전략 코드 생성",
        "backtest_button": "백테스트 실행",
        "code_output_label": "AI가 생성한 전략 코드",
        "result_output_label": "백테스트 결과",
        "empty_strategy_error": "전략 입력은 비워둘 수 없습니다.",
        "start_time_error": "시작 시간은 2017-01-01보다 이를 수 없습니다.",
        "invalid_date_error": "날짜 형식이 올바르지 않습니다. 권장 형식은 YYYY-MM-DD입니다. 예: 2017-01-13. 2017.1.13, 2017/1/13, 2017년1월13일, 13/1/2017 형식도 지원합니다.",
        "date_order_error": "종료 시간은 시작 시간보다 이를 수 없습니다.",
        "api_fail_error": "AI API 호출 실패",
        "backtest_fail_error": "백테스트 실행 실패",
        "no_code_error": "전략 코드가 비어 있습니다. 먼저 전략 코드를 생성하세요.",
        "invalid_number_error": "유효한 숫자를 입력하세요.",
        "invalid_initial_cash_error": "초기 자금은 0보다 커야 합니다.",
        "invalid_leverage_error": "레버리지는 0에서 200 사이의 정수여야 합니다. 0은 레버리지를 사용하지 않는다는 뜻이며 실제 계산은 1배로 처리됩니다.",
        "invalid_position_size_error": "포지션 비율은 0에서 100 사이여야 합니다.",
        "invalid_fee_rate_error": "수수료율은 0보다 작을 수 없습니다. 0 또는 양수를 입력하세요. 예: 0.05는 0.05%, 즉 0.0005를 의미합니다.",
        "invalid_slippage_error": "슬리피지는 0보다 작을 수 없습니다. 0 또는 양수를 입력하세요. 예: 0.02는 0.02%를 의미합니다.",
        "too_few_klines_error": "필터링 후 K라인 수가 너무 적습니다. 현재 {count}개입니다. 기간을 넓혀 주세요.",
        "view_chart_button": "상세 차트 보기",
        "view_robustness_chart_button": "강건성 차트 보기",
        "view_chart_missing": "사용 가능한 차트가 없습니다. 먼저 백테스트를 실행하세요.",
        "history_select_label": "기록 (선택하여 보기)",
        "history_refresh": "기록 새로고침",
        "history_empty": "기록이 없습니다",
    },
    "en": {
        "header": """
# QTBS AI Quant Strategy Frontend

Enter your natural-language strategy on the left, switch language at the top right, and configure market, symbol, timeframe, backtest period, initial cash, leverage, position size, fee, and slippage on the right.  
Generate strategy code first, then run the backtest.
""",
        "language_label": "Language",
        "strategy_label": "Strategy Natural-Language Input",
        "strategy_placeholder": "Example: Go long when MA20 crosses above MA60, exit when MA20 crosses below MA60, no shorting.",
        "market_label": "Market Selection",
        "market_choice": "Cryptocurrency",
        "symbol_label": "Trading Symbol",
        "symbol_placeholder": "If empty, default is BTC. Example: BTC / ETH / SOL",
        "param_priority_short": "Precedence: code-declared symbols win; everything else uses this panel",
        "param_priority_note": "When the strategy code declares symbols (SYMBOLS — always the case for multi-symbol hedge/portfolio strategies), the code wins and the Trading Symbol field above is ignored. Timeframe, date range, capital, leverage, position size, fee and slippage always come from this panel; strategy-internal parameters (lookback, thresholds, etc.) come from the code.",
        "timeframe_label": "Timeframe",
        "start_label": "Start Time",
        "end_label": "End Time",
        "initial_cash_label": "Initial Cash",
        "leverage_label": "Leverage",
        "position_size_label": "Position Size（%）",
        "fee_rate_label": "Fee Rate（%）",
        "slippage_label": "Slippage（%）",
        "generate_button": "Generate Strategy Code",
        "backtest_button": "Run Backtest",
        "code_output_label": "Strategy Code Generated by AI",
        "result_output_label": "Backtest Result",
        "empty_strategy_error": "Strategy input cannot be empty.",
        "start_time_error": "Start time cannot be earlier than 2017-01-01.",
        "invalid_date_error": "Invalid date format. Recommended format: YYYY-MM-DD, for example 2017-01-13. Also supports 2017.1.13, 2017/1/13, 2017年1月13日, 13/1/2017, and 1/13/2017.",
        "date_order_error": "End time cannot be earlier than start time.",
        "api_fail_error": "AI API call failed",
        "backtest_fail_error": "Backtest failed",
        "no_code_error": "Strategy code is empty. Generate strategy code first.",
        "invalid_number_error": "Please enter a valid number.",
        "invalid_initial_cash_error": "Initial cash must be greater than 0.",
        "invalid_leverage_error": "Leverage must be an integer between 0 and 200. 0 means no leverage and will be calculated as 1x.",
        "invalid_position_size_error": "Position size must be between 0 and 100.",
        "invalid_fee_rate_error": "Fee rate cannot be less than 0. Enter 0 or a positive number. Example: 0.05 means 0.05%, equal to 0.0005.",
        "invalid_slippage_error": "Slippage cannot be less than 0. Enter 0 or a positive number. Example: 0.02 means 0.02%.",
        "too_few_klines_error": "Too few K-lines after filtering. Current count: {count}. Please expand the time range.",
        "view_chart_button": "View Detailed Chart",
        "view_robustness_chart_button": "View Robustness Chart",
        "view_chart_missing": "No chart available yet. Run a backtest first.",
        "history_select_label": "History (select one to view)",
        "history_refresh": "Refresh List",
        "history_empty": "No history yet",
    },
    "ja": {
        "header": """
# QTBS AI 量的戦略フロントエンド

左側に自然言語の戦略を入力し、右上で言語を切り替え、右側で市場、取引銘柄、時間足、バックテスト期間、初期資金、レバレッジ、ポジション比率、手数料、スリッページを設定します。  
まず戦略コードを生成し、その後バックテストを実行します。
""",
        "language_label": "言語",
        "strategy_label": "戦略・自然言語入力欄",
        "strategy_placeholder": "例：MA20がMA60を上抜けしたら買い、MA20がMA60を下抜けしたら決済、空売りなし。",
        "market_label": "市場選択",
        "market_choice": "暗号資産",
        "symbol_label": "取引銘柄",
        "symbol_placeholder": "未入力の場合は BTC がデフォルトです。例：BTC / ETH / SOL",
        "param_priority_short": "優先順位：コード宣言の銘柄が優先、その他は本パネル基準",
        "param_priority_note": "戦略コードに銘柄（SYMBOLS）が宣言されている場合（マルチ銘柄のヘッジ/ポートフォリオでは必ず宣言されます）、コード内の銘柄が優先され、上の取引銘柄欄は無視されます。時間足・期間・資金・レバレッジ・ポジション比率・手数料・スリッページは常に本パネルの設定が適用され、戦略内部のパラメータ（ルックバック、しきい値など）はコードに従います。",
        "timeframe_label": "時間足",
        "start_label": "開始時間",
        "end_label": "終了時間",
        "initial_cash_label": "初期資金",
        "leverage_label": "レバレッジ",
        "position_size_label": "ポジション比率（%）",
        "fee_rate_label": "手数料率（%）",
        "slippage_label": "スリッページ（%）",
        "generate_button": "戦略コードを生成",
        "backtest_button": "バックテストを実行",
        "code_output_label": "AI が生成した戦略コード",
        "result_output_label": "バックテスト結果",
        "empty_strategy_error": "戦略入力は空にできません。",
        "start_time_error": "開始時間は 2017-01-01 より前にできません。",
        "invalid_date_error": "日付形式が無効です。推奨形式は YYYY-MM-DD です。例：2017-01-13。2017.1.13、2017/1/13、2017年1月13日、13/1/2017 も対応しています。",
        "date_order_error": "終了時間は開始時間より前にできません。",
        "api_fail_error": "AI API の呼び出しに失敗しました",
        "backtest_fail_error": "バックテスト実行に失敗しました",
        "no_code_error": "戦略コードが空です。先に戦略コードを生成してください。",
        "invalid_number_error": "有効な数値を入力してください。",
        "invalid_initial_cash_error": "初期資金は 0 より大きい必要があります。",
        "invalid_leverage_error": "レバレッジは 0 から 200 までの整数である必要があります。0 はレバレッジなしを意味し、実際の計算では 1倍として扱います。",
        "invalid_position_size_error": "ポジション比率は 0 から 100 の間である必要があります。",
        "invalid_fee_rate_error": "手数料率は 0 未満にできません。0 または正の数を入力してください。例：0.05 は 0.05%、つまり 0.0005 を意味します。",
        "invalid_slippage_error": "スリッページは 0 未満にできません。0 または正の数を入力してください。例：0.02 は 0.02% を意味します。",
        "too_few_klines_error": "フィルタ後のK線数が少なすぎます。現在 {count} 本です。期間を広げてください。",
        "view_chart_button": "詳細チャートを表示",
        "view_robustness_chart_button": "ロバストネスチャートを表示",
        "view_chart_missing": "利用可能なチャートがありません。先にバックテストを実行してください。",
        "history_select_label": "履歴（選択して表示）",
        "history_refresh": "履歴を更新",
        "history_empty": "履歴がありません",
    },
    "ar": {
        "header": """
# واجهة QTBS AI لاستراتيجيات التداول الكمي

أدخل الاستراتيجية باللغة الطبيعية في الجهة اليسرى، وغيّر اللغة من أعلى اليمين، واضبط السوق ورمز التداول والإطار الزمني وفترة الاختبار ورأس المال الأولي والرافعة المالية وحجم الصفقة والرسوم والانزلاق السعري في الجهة اليمنى.  
قم بإنشاء كود الاستراتيجية أولاً، ثم شغّل الاختبار الخلفي.
""",
        "language_label": "اللغة",
        "strategy_label": "حقل إدخال الاستراتيجية باللغة الطبيعية",
        "strategy_placeholder": "مثال: افتح شراء عندما يتقاطع MA20 فوق MA60، وأغلق عندما يهبط MA20 أسفل MA60، بدون بيع على المكشوف.",
        "market_label": "اختيار السوق",
        "market_choice": "العملات المشفرة",
        "symbol_label": "رمز التداول",
        "symbol_placeholder": "إذا تُرك فارغًا فالقيمة الافتراضية هي BTC. مثال: BTC / ETH / SOL",
        "param_priority_short": "الأولوية: رموز الكود أولاً، والباقي من هذه اللوحة",
        "param_priority_note": "عندما يعلن كود الاستراتيجية الرموز (SYMBOLS — وهذا دائمًا حال استراتيجيات التحوط/المحافظ متعددة الرموز) تكون الأولوية للكود ويتم تجاهل حقل رمز التداول أعلاه. الإطار الزمني وفترة الاختبار ورأس المال والرافعة ونسبة المركز والرسوم والانزلاق تُؤخذ دائمًا من هذه اللوحة؛ أما المعاملات الداخلية للاستراتيجية (فترة النظر، العتبات...) فمن الكود.",
        "timeframe_label": "الإطار الزمني",
        "start_label": "وقت البداية",
        "end_label": "وقت النهاية",
        "initial_cash_label": "رأس المال الأولي",
        "leverage_label": "الرافعة المالية",
        "position_size_label": "حجم الصفقة（%）",
        "fee_rate_label": "نسبة الرسوم（%）",
        "slippage_label": "الانزلاق السعري（%）",
        "generate_button": "إنشاء كود الاستراتيجية",
        "backtest_button": "تشغيل الاختبار الخلفي",
        "code_output_label": "كود الاستراتيجية الذي أنشأه AI",
        "result_output_label": "نتيجة الاختبار الخلفي",
        "empty_strategy_error": "لا يمكن أن يكون إدخال الاستراتيجية فارغًا.",
        "start_time_error": "لا يمكن أن يكون وقت البداية أقدم من 2017-01-01.",
        "invalid_date_error": "تنسيق التاريخ غير صالح. التنسيق الموصى به هو YYYY-MM-DD، مثال: 2017-01-13. يتم أيضًا دعم 2017.1.13 و 2017/1/13 و 2017年1月13日 و 13/1/2017.",
        "date_order_error": "لا يمكن أن يكون وقت النهاية أقدم من وقت البداية.",
        "api_fail_error": "فشل استدعاء واجهة AI API",
        "backtest_fail_error": "فشل تشغيل الاختبار الخلفي",
        "no_code_error": "كود الاستراتيجية فارغ. يرجى إنشاء الكود أولاً.",
        "invalid_number_error": "يرجى إدخال رقم صالح.",
        "invalid_initial_cash_error": "يجب أن يكون رأس المال الأولي أكبر من 0.",
        "invalid_leverage_error": "يجب أن تكون الرافعة المالية عددًا صحيحًا بين 0 و 200. القيمة 0 تعني عدم استخدام الرافعة ويتم الحساب فعليًا على أساس 1x.",
        "invalid_position_size_error": "يجب أن يكون حجم الصفقة بين 0 و 100.",
        "invalid_fee_rate_error": "لا يمكن أن تكون نسبة الرسوم أقل من 0. أدخل 0 أو رقمًا موجبًا. مثال: 0.05 يعني 0.05%، أي 0.0005.",
        "invalid_slippage_error": "لا يمكن أن يكون الانزلاق السعري أقل من 0. أدخل 0 أو رقمًا موجبًا. مثال: 0.02 يعني 0.02%.",
        "too_few_klines_error": "عدد شموع K-line بعد التصفية قليل جدًا. العدد الحالي: {count}. يرجى توسيع الفترة الزمنية.",
        "view_chart_button": "عرض الرسم البياني التفصيلي",
        "view_robustness_chart_button": "عرض رسم المتانة",
        "view_chart_missing": "لا يوجد رسم بياني متاح بعد. شغّل اختبارًا خلفيًا أولًا.",
        "history_select_label": "السجل (اختر واحدًا للعرض)",
        "history_refresh": "تحديث القائمة",
        "history_empty": "لا يوجد سجل بعد",
    },
    "ru": {
        "header": """
# QTBS AI фронтенд для количественных стратегий

Слева введите стратегию на естественном языке, переключите язык в правом верхнем углу, а справа настройте рынок, инструмент, таймфрейм, период тестирования, начальный капитал, кредитное плечо, размер позиции, комиссию и проскальзывание.  
Сначала сгенерируйте код стратегии, затем запустите бэктест.
""",
        "language_label": "Язык",
        "strategy_label": "Поле ввода стратегии на естественном языке",
        "strategy_placeholder": "Пример: открыть long, когда MA20 пересекает MA60 снизу вверх, закрыть позицию при обратном пересечении, без short.",
        "market_label": "Выбор рынка",
        "market_choice": "Криптовалюта",
        "symbol_label": "Торговый инструмент",
        "symbol_placeholder": "Если не указано, по умолчанию BTC. Например: BTC / ETH / SOL",
        "param_priority_short": "Приоритет: символы из кода важнее; остальное — из панели",
        "param_priority_note": "Если код стратегии объявляет инструменты (SYMBOLS — для мультиактивных хедж/портфельных стратегий это всегда так), приоритет у кода, а поле «Торговый инструмент» выше игнорируется. Таймфрейм, период, капитал, плечо, доля позиции, комиссия и проскальзывание всегда берутся из этой панели; внутренние параметры стратегии (окно, пороги и т.д.) — из кода.",
        "timeframe_label": "Таймфрейм",
        "start_label": "Начальное время",
        "end_label": "Конечное время",
        "initial_cash_label": "Начальный капитал",
        "leverage_label": "Кредитное плечо",
        "position_size_label": "Размер позиции（%）",
        "fee_rate_label": "Комиссия（%）",
        "slippage_label": "Проскальзывание（%）",
        "generate_button": "Сгенерировать код стратегии",
        "backtest_button": "Запустить бэктест",
        "code_output_label": "Код стратегии, сгенерированный AI",
        "result_output_label": "Результат бэктеста",
        "empty_strategy_error": "Поле стратегии не может быть пустым.",
        "start_time_error": "Начальная дата не может быть раньше 2017-01-01.",
        "invalid_date_error": "Неверный формат даты. Рекомендуемый формат: YYYY-MM-DD, например 2017-01-13. Также поддерживаются 2017.1.13, 2017/1/13, 2017年1月13日 и 13/1/2017.",
        "date_order_error": "Конечное время не может быть раньше начального времени.",
        "api_fail_error": "Ошибка вызова AI API",
        "backtest_fail_error": "Ошибка запуска бэктеста",
        "no_code_error": "Код стратегии пуст. Сначала сгенерируйте код стратегии.",
        "invalid_number_error": "Введите корректное число.",
        "invalid_initial_cash_error": "Начальный капитал должен быть больше 0.",
        "invalid_leverage_error": "Кредитное плечо должно быть целым числом от 0 до 200. 0 означает отсутствие плеча и фактически рассчитывается как 1x.",
        "invalid_position_size_error": "Размер позиции должен быть от 0 до 100.",
        "invalid_fee_rate_error": "Комиссия не может быть меньше 0. Введите 0 или положительное число. Например: 0.05 означает 0.05%, то есть 0.0005.",
        "invalid_slippage_error": "Проскальзывание не может быть меньше 0. Введите 0 или положительное число. Например: 0.02 означает 0.02%.",
        "too_few_klines_error": "После фильтрации осталось слишком мало K-line данных. Текущее количество: {count}. Расширьте временной диапазон.",
        "view_chart_button": "Открыть детальный график",
        "view_robustness_chart_button": "Открыть график устойчивости",
        "view_chart_missing": "График пока недоступен. Сначала запустите бэктест.",
        "history_select_label": "История (выберите запись)",
        "history_refresh": "Обновить список",
        "history_empty": "История пуста",
    },
}


SUMMARY_TEXTS = {
    "zh": {
        "completed": "回测完成。",
        "market": "市场",
        "symbol": "交易标的",
        "timeframe": "周期",
        "start_time": "起始时间",
        "end_time": "结束时间",
        "kline_count": "K线数量",
        "initial_cash": "初始资金",
        "leverage": "杠杆倍数",
        "effective_leverage": "实际计算杠杆",
        "position_size": "仓位比例",
        "final_equity": "最终权益",
        "total_return": "总收益率",
        "gross_win_rate": "毛胜率",
        "net_win_rate": "净胜率",
        "avg_profit": "平均盈利",
        "avg_loss": "平均亏损",
        "payoff_ratio": "盈亏比",
        "profit_factor": "Profit Factor",
        "max_drawdown": "最大回撤",
        "annual_return": "年化收益",
        "sharpe_ratio": "夏普比率",
        "trade_count": "交易次数",
        "max_consecutive_wins": "最大连续盈利次数",
        "max_consecutive_losses": "最大连续亏损次数",
        "avg_holding_hours": "平均持仓时间（小时）",
        "fee_rate": "手续费率",
        "slippage": "滑点",
        "funding_cost": "资金费率净支出",
        "chart_file": "图表文件",
        "chart_path_missing": "图表已生成，但 generic_chart.py 没有 return 文件路径。",
        "na": "无",
    },
    "ko": {
        "completed": "백테스트 완료.",
        "market": "시장",
        "symbol": "거래 종목",
        "timeframe": "주기",
        "start_time": "시작 시간",
        "end_time": "종료 시간",
        "kline_count": "K라인 수",
        "initial_cash": "초기 자금",
        "leverage": "레버리지",
        "effective_leverage": "실제 계산 레버리지",
        "position_size": "포지션 비율",
        "final_equity": "최종 자산",
        "total_return": "총 수익률",
        "gross_win_rate": "총 승률",
        "net_win_rate": "순 승률",
        "avg_profit": "평균 수익",
        "avg_loss": "평균 손실",
        "payoff_ratio": "손익비",
        "profit_factor": "Profit Factor",
        "max_drawdown": "최대 낙폭",
        "annual_return": "연환산 수익률",
        "sharpe_ratio": "샤프 비율",
        "trade_count": "거래 횟수",
        "max_consecutive_wins": "최대 연속 수익 횟수",
        "max_consecutive_losses": "최대 연속 손실 횟수",
        "avg_holding_hours": "평균 보유 시간（시간）",
        "fee_rate": "수수료율",
        "slippage": "슬리피지",
        "funding_cost": "자금 조달 비용 순지출",
        "chart_file": "차트 파일",
        "chart_path_missing": "차트는 생성되었지만 generic_chart.py가 파일 경로를 반환하지 않았습니다.",
        "na": "없음",
    },
    "en": {
        "completed": "Backtest completed.",
        "market": "Market",
        "symbol": "Trading Symbol",
        "timeframe": "Timeframe",
        "start_time": "Start Time",
        "end_time": "End Time",
        "kline_count": "K-line Count",
        "initial_cash": "Initial Cash",
        "leverage": "Leverage",
        "effective_leverage": "Effective Leverage",
        "position_size": "Position Size",
        "final_equity": "Final Equity",
        "total_return": "Total Return",
        "gross_win_rate": "Gross Win Rate",
        "net_win_rate": "Net Win Rate",
        "avg_profit": "Average Profit",
        "avg_loss": "Average Loss",
        "payoff_ratio": "Payoff Ratio",
        "profit_factor": "Profit Factor",
        "max_drawdown": "Max Drawdown",
        "annual_return": "Annualized Return",
        "sharpe_ratio": "Sharpe Ratio",
        "trade_count": "Trade Count",
        "max_consecutive_wins": "Max Consecutive Wins",
        "max_consecutive_losses": "Max Consecutive Losses",
        "avg_holding_hours": "Average Holding Time（hours）",
        "fee_rate": "Fee Rate",
        "slippage": "Slippage",
        "funding_cost": "Funding Cost (net)",
        "chart_file": "Chart File",
        "chart_path_missing": "The chart was generated, but generic_chart.py did not return a file path.",
        "na": "N/A",
    },
    "ja": {
        "completed": "バックテスト完了。",
        "market": "市場",
        "symbol": "取引銘柄",
        "timeframe": "時間足",
        "start_time": "開始時間",
        "end_time": "終了時間",
        "kline_count": "K線数",
        "initial_cash": "初期資金",
        "leverage": "レバレッジ",
        "effective_leverage": "実際の計算レバレッジ",
        "position_size": "ポジション比率",
        "final_equity": "最終資産",
        "total_return": "総収益率",
        "gross_win_rate": "グロス勝率",
        "net_win_rate": "ネット勝率",
        "avg_profit": "平均利益",
        "avg_loss": "平均損失",
        "payoff_ratio": "損益比",
        "profit_factor": "Profit Factor",
        "max_drawdown": "最大ドローダウン",
        "annual_return": "年率収益",
        "sharpe_ratio": "シャープレシオ",
        "trade_count": "取引回数",
        "max_consecutive_wins": "最大連続勝利回数",
        "max_consecutive_losses": "最大連続損失回数",
        "avg_holding_hours": "平均保有時間（時間）",
        "fee_rate": "手数料率",
        "slippage": "スリッページ",
        "funding_cost": "資金調達コスト（純支出）",
        "chart_file": "チャートファイル",
        "chart_path_missing": "チャートは生成されましたが、generic_chart.py がファイルパスを返していません。",
        "na": "なし",
    },
    "ar": {
        "completed": "اكتمل الاختبار الخلفي.",
        "market": "السوق",
        "symbol": "رمز التداول",
        "timeframe": "الإطار الزمني",
        "start_time": "وقت البداية",
        "end_time": "وقت النهاية",
        "kline_count": "عدد شموع K-line",
        "initial_cash": "رأس المال الأولي",
        "leverage": "الرافعة المالية",
        "effective_leverage": "الرافعة الفعلية في الحساب",
        "position_size": "حجم الصفقة",
        "final_equity": "القيمة النهائية",
        "total_return": "إجمالي العائد",
        "gross_win_rate": "نسبة الربح الإجمالية",
        "net_win_rate": "نسبة الربح الصافية",
        "avg_profit": "متوسط الربح",
        "avg_loss": "متوسط الخسارة",
        "payoff_ratio": "نسبة الربح إلى الخسارة",
        "profit_factor": "Profit Factor",
        "max_drawdown": "أقصى تراجع",
        "annual_return": "العائد السنوي",
        "sharpe_ratio": "نسبة شارب",
        "trade_count": "عدد الصفقات",
        "max_consecutive_wins": "أقصى عدد أرباح متتالية",
        "max_consecutive_losses": "أقصى عدد خسائر متتالية",
        "avg_holding_hours": "متوسط مدة الاحتفاظ（بالساعات）",
        "fee_rate": "نسبة الرسوم",
        "slippage": "الانزلاق السعري",
        "funding_cost": "تكلفة التمويل (صافي)",
        "chart_file": "ملف الرسم البياني",
        "chart_path_missing": "تم إنشاء الرسم البياني، لكن generic_chart.py لم يُرجع مسار الملف.",
        "na": "غير متاح",
    },
    "ru": {
        "completed": "Бэктест завершен.",
        "market": "Рынок",
        "symbol": "Торговый инструмент",
        "timeframe": "Таймфрейм",
        "start_time": "Начальное время",
        "end_time": "Конечное время",
        "kline_count": "Количество K-line",
        "initial_cash": "Начальный капитал",
        "leverage": "Кредитное плечо",
        "effective_leverage": "Фактическое плечо в расчете",
        "position_size": "Размер позиции",
        "final_equity": "Итоговый капитал",
        "total_return": "Общая доходность",
        "gross_win_rate": "Валовая доля прибыльных сделок",
        "net_win_rate": "Чистая доля прибыльных сделок",
        "avg_profit": "Средняя прибыль",
        "avg_loss": "Средний убыток",
        "payoff_ratio": "Соотношение прибыль/убыток",
        "profit_factor": "Profit Factor",
        "max_drawdown": "Максимальная просадка",
        "annual_return": "Годовая доходность",
        "sharpe_ratio": "Коэффициент Шарпа",
        "trade_count": "Количество сделок",
        "max_consecutive_wins": "Максимальная серия прибыльных сделок",
        "max_consecutive_losses": "Максимальная серия убыточных сделок",
        "avg_holding_hours": "Среднее время удержания（часы）",
        "fee_rate": "Комиссия",
        "slippage": "Проскальзывание",
        "funding_cost": "Стоимость финансирования (нетто)",
        "chart_file": "Файл графика",
        "chart_path_missing": "График создан, но generic_chart.py не вернул путь к файлу.",
        "na": "нет данных",
    },
}

REVIEW_TEXTS = {
    "zh": {
        "title": "AI 策略审查",
        "match": "策略匹配度",
        "desc_title": "匹配说明",
        "empty": "生成策略代码后，这里会显示 AI 匹配度评分。",
        "note": "80%+：初步可用，建议进入回测验证。90%+：高度匹配，但仍需人工复核。评分不代表结果保证，请结合图表检查开仓、平仓、方向、仓位、手续费滑点与插针风险。",
        "review_failed": "AI 审查失败",
        "behavior_pass_short": "行为检查通过",
        "behavior_fail_short": "行为检查失败",
        "behavior_pass": "行为检查通过：代码已在 {bars} 根合成K线上实际运行，完成 {trades} 笔交易",
        "behavior_fail": "行为检查失败：代码在合成数据上运行出错——{error}",
        "behavior_tip": "行为检查＝把生成的代码在 720 根合成K线（上涨/下跌/震荡三段行情）上真正跑一遍回测引擎。✓ 表示代码能正常运行、产生交易、没有运行时错误；✗ 表示运行报错（会写明原因）。它只验证「代码能不能跑」，不代表「代码符合你的描述」——是否符合描述由上方的策略匹配度判断。",
    },
    "en": {
        "title": "AI Strategy Review",
        "match": "Strategy Match",
        "desc_title": "Match Notes",
        "empty": "After generating strategy code, the AI match score will appear here.",
        "note": "80%+ means initially usable and ready for backtest validation. 90%+ means highly aligned, but still requires manual review. Scores are not guarantees. Check entries, exits, direction, position, fees, slippage, spike equity, and liquidation risk on the chart.",
        "review_failed": "AI review failed",
        "behavior_pass_short": "Behavior check passed",
        "behavior_fail_short": "Behavior check failed",
        "behavior_pass": "Behavior check passed: code executed on {bars} synthetic bars, {trades} trades completed",
        "behavior_fail": "Behavior check failed: runtime error on synthetic data — {error}",
        "behavior_tip": "Behavior check = the generated code is actually run through the engine on 720 synthetic bars (uptrend/downtrend/range). ✓ means it runs, produces trades, and has no runtime error; ✗ means it errored (reason shown). It only verifies the code *runs*, not that it *matches your description* — matching is judged by the Strategy Match score above.",
    },
    "ko": {
        "title": "AI 전략 검토",
        "match": "전략 일치도",
        "desc_title": "일치도 설명",
        "empty": "전략 코드를 생성하면 여기에 AI 일치도 점수가 표시됩니다.",
        "note": "80% 이상은 초기 사용 가능 상태이며 백테스트 검증을 권장합니다. 90% 이상은 높은 일치도를 의미하지만 수동 검토가 필요합니다. 점수는 결과 보장이 아니며 차트에서 진입, 청산, 방향, 포지션, 수수료, 슬리피지, 급등락 자산, 강제청산 위험을 확인하세요.",
        "review_failed": "AI 검토 실패",
        "behavior_pass_short": "행동 검사 통과",
        "behavior_fail_short": "행동 검사 실패",
        "behavior_pass": "행동 검사 통과: 합성 K라인 {bars}개에서 실제 실행됨, 거래 {trades}건 완료",
        "behavior_fail": "행동 검사 실패: 합성 데이터 실행 중 오류 — {error}",
        "behavior_tip": "행동 검사＝생성된 코드를 720개의 합성 K라인(상승/하락/횡보 3구간)에서 실제로 백테스트 엔진에 돌려봅니다. ✓ 는 코드가 정상 실행되어 거래가 발생하고 런타임 오류가 없음을, ✗ 는 실행 중 오류(원인 표시)를 의미합니다. 이는 「코드가 실행되는지」만 검증하며 「설명과 일치하는지」는 위의 전략 일치도로 판단합니다.",
    },
    "ja": {
        "title": "AI 戦略レビュー",
        "match": "戦略一致度",
        "desc_title": "一致度説明",
        "empty": "戦略コード生成後、ここに AI 一致度スコアが表示されます。",
        "note": "80%以上は初期利用可能な状態で、バックテスト検証を推奨します。90%以上は高い一致度を示しますが、手動確認は必要です。スコアは保証ではありません。チャートでエントリー、決済、方向、ポジション、手数料、スリッページ、急変時の資産、強制決済リスクを確認してください。",
        "review_failed": "AI レビュー失敗",
        "behavior_pass_short": "動作チェック合格",
        "behavior_fail_short": "動作チェック失敗",
        "behavior_pass": "動作チェック合格：合成K線 {bars} 本で実行済み、取引 {trades} 件完了",
        "behavior_fail": "動作チェック失敗：合成データでの実行エラー — {error}",
        "behavior_tip": "動作チェック＝生成されたコードを 720 本の合成K線（上昇/下降/レンジの3区間）でバックテストエンジンに実際に通します。✓ はコードが正常に動作し取引が発生、ランタイムエラーなしを、✗ は実行エラー（原因を表示）を意味します。これは「コードが動くか」だけを検証し、「説明と一致するか」は上の戦略一致度で判断します。",
    },
    "ar": {
        "title": "مراجعة الاستراتيجية بالذكاء الاصطناعي",
        "match": "درجة مطابقة الاستراتيجية",
        "desc_title": "ملاحظات المطابقة",
        "empty": "بعد إنشاء كود الاستراتيجية ستظهر هنا درجة المطابقة من الذكاء الاصطناعي.",
        "note": "أكثر من 80% يعني أنها قابلة للاستخدام مبدئيًا وتحتاج إلى اختبار خلفي. أكثر من 90% يعني تطابقًا عاليًا، لكنه لا يغني عن المراجعة اليدوية. النتيجة ليست ضمانًا. تحقق من الدخول والخروج والاتجاه والمركز والرسوم والانزلاق السعري ومخاطر الذبذبات والتصفية عبر الرسم البياني.",
        "review_failed": "فشلت مراجعة الذكاء الاصطناعي",
        "behavior_pass_short": "اجتاز فحص السلوك",
        "behavior_fail_short": "فشل فحص السلوك",
        "behavior_pass": "اجتاز فحص السلوك: تم تنفيذ الكود على {bars} شمعة اصطناعية وأُنجزت {trades} صفقة",
        "behavior_fail": "فشل فحص السلوك: خطأ أثناء التشغيل على البيانات الاصطناعية — {error}",
        "behavior_tip": "فحص السلوك = تشغيل الكود المُولَّد فعليًا عبر محرك الاختبار على 720 شمعة اصطناعية (صعود/هبوط/تذبذب). ✓ يعني أن الكود يعمل ويُنتج صفقات دون أخطاء تشغيل؛ ✗ يعني حدوث خطأ (يُعرض سببه). إنه يتحقق فقط من «أن الكود يعمل»، لا من «مطابقته لوصفك» — المطابقة تُقيَّم بدرجة مطابقة الاستراتيجية أعلاه.",
    },
    "ru": {
        "title": "AI-проверка стратегии",
        "match": "Соответствие стратегии",
        "desc_title": "Пояснение соответствия",
        "empty": "После генерации кода стратегии здесь появится AI-оценка соответствия.",
        "note": "80%+ означает начальную пригодность для проверки в бэктесте. 90%+ означает высокое соответствие, но ручная проверка всё равно нужна. Оценка не является гарантией. Проверьте входы, выходы, направление, позицию, комиссии, проскальзывание, экстремумы капитала и риск ликвидации на графике.",
        "review_failed": "Ошибка AI-проверки",
        "behavior_pass_short": "Поведенческая проверка пройдена",
        "behavior_fail_short": "Поведенческая проверка не пройдена",
        "behavior_pass": "Поведенческая проверка пройдена: код выполнен на {bars} синтетических барах, сделок: {trades}",
        "behavior_fail": "Поведенческая проверка не пройдена: ошибка при выполнении на синтетических данных — {error}",
        "behavior_tip": "Поведенческая проверка = сгенерированный код реально прогоняется через движок на 720 синтетических барах (рост/падение/боковик). ✓ — код работает, создаёт сделки, без ошибок выполнения; ✗ — произошла ошибка (причина показана). Проверяется только то, что код *запускается*, а не то, что он *соответствует вашему описанию* — соответствие оценивает «Соответствие стратегии» выше.",
    },
}


# 数据拉取/更新进度区文案（六语言）
FETCH_TEXTS = {
    "zh": {
        "idle": "数据已是最新",
        "initial": "首次拉取",
        "update": "更新数据",
        "running": "数据{mode}中",
        "total": "总进度",
        "current": "当前",
        "done_all": "数据{mode}完成",
        "failed": "失败",
        "button": "更新数据",
        "button_running": "更新中…",
        "button_integrity": "检查并修复",
        "tip_initial": "本次将首次拉取以下交易对（2017 至今的 1 分钟数据）：",
        "tip_update": "本次将更新以下交易对的数据：",
        "tip_idle": "启动时自动扫描本地交易对并更新；首次使用则初次拉取默认币种。点「更新数据」可手动更新。",
    },
    "en": {
        "idle": "Data is up to date",
        "initial": "Initial download",
        "update": "Update",
        "running": "{mode} in progress",
        "total": "Total",
        "current": "Current",
        "done_all": "{mode} complete",
        "failed": "failed",
        "button": "Update Data",
        "button_running": "Updating…",
        "button_integrity": "Check & Repair",
        "tip_initial": "Initial download of these symbols (1m data since 2017):",
        "tip_update": "Updating data for these symbols:",
        "tip_idle": "On startup, local symbols are scanned and updated automatically; first use downloads the default symbols. Click 'Update Data' to update manually.",
    },
    "ko": {
        "idle": "데이터가 최신 상태입니다",
        "initial": "최초 다운로드",
        "update": "데이터 업데이트",
        "running": "{mode} 진행 중",
        "total": "전체",
        "current": "현재",
        "done_all": "{mode} 완료",
        "failed": "실패",
        "button": "데이터 업데이트",
        "button_running": "업데이트 중…",
        "button_integrity": "검사 및 복구",
        "tip_initial": "다음 종목을 최초 다운로드합니다(2017년부터의 1분 데이터):",
        "tip_update": "다음 종목의 데이터를 업데이트합니다:",
        "tip_idle": "시작 시 로컬 종목을 스캔하여 자동 업데이트하며, 최초 사용 시 기본 종목을 다운로드합니다. '데이터 업데이트'로 수동 업데이트할 수 있습니다.",
    },
    "ja": {
        "idle": "データは最新です",
        "initial": "初回取得",
        "update": "データ更新",
        "running": "{mode}中",
        "total": "全体",
        "current": "現在",
        "done_all": "{mode}完了",
        "failed": "失敗",
        "button": "データ更新",
        "button_running": "更新中…",
        "button_integrity": "チェックと修復",
        "tip_initial": "以下の銘柄を初回取得します（2017年以降の1分データ）：",
        "tip_update": "以下の銘柄のデータを更新します：",
        "tip_idle": "起動時にローカル銘柄をスキャンして自動更新し、初回利用時はデフォルト銘柄を取得します。「データ更新」で手動更新できます。",
    },
    "ar": {
        "idle": "البيانات محدّثة",
        "initial": "التنزيل الأول",
        "update": "تحديث البيانات",
        "running": "جارٍ {mode}",
        "total": "الإجمالي",
        "current": "الحالي",
        "done_all": "اكتمل {mode}",
        "failed": "فشل",
        "button": "تحديث البيانات",
        "button_running": "جارٍ التحديث…",
        "button_integrity": "فحص وإصلاح",
        "tip_initial": "سيتم التنزيل الأول للرموز التالية (بيانات الدقيقة منذ 2017):",
        "tip_update": "سيتم تحديث بيانات الرموز التالية:",
        "tip_idle": "عند بدء التشغيل تُفحص الرموز المحلية وتُحدّث تلقائيًا؛ الاستخدام الأول ينزّل الرموز الافتراضية. اضغط «تحديث البيانات» للتحديث يدويًا.",
    },
    "ru": {
        "idle": "Данные актуальны",
        "initial": "Первая загрузка",
        "update": "Обновление",
        "running": "{mode}…",
        "total": "Всего",
        "current": "Текущий",
        "done_all": "{mode}: готово",
        "failed": "ошибка",
        "button": "Обновить данные",
        "button_running": "Обновление…",
        "button_integrity": "Проверить и исправить",
        "tip_initial": "Первая загрузка этих инструментов (1-мин данные с 2017):",
        "tip_update": "Обновление данных по этим инструментам:",
        "tip_idle": "При запуске локальные инструменты сканируются и обновляются автоматически; при первом запуске загружаются инструменты по умолчанию. Нажмите «Обновить данные» для ручного обновления.",
    },
}

# 稳健性分析面板文案（六语言）。指标名复用 SUMMARY_TEXTS，这里只放结构性标签。
ROBUSTNESS_TEXTS = {
    "zh": {
        "panel_title": "稳健性分析", "panel_desc": "样本内外切分 · Walk-Forward · 参数热力图",
        "run_button": "运行稳健性分析", "split_label": "样本内占比 (IS)",
        "title": "稳健性分析", "io_section": "样本内 / 样本外", "boundary": "切分点",
        "col_in": "样本内", "col_out": "样本外", "col_ratio": "外/内",
        "flags_title": "过拟合红线", "no_flags": "未触发过拟合红线",
        "wfo_section": "Walk-Forward 前推",
        "wfo_real": "传统 WFO（每窗样本内寻优 → 样本外评估）",
        "wfo_stability": "固定策略分段样本外稳定性扫描（未声明 PARAM_SPACE）",
        "wfo_windows": "窗口数", "wfo_valid": "有效窗口", "wfo_pos_ratio": "正收益窗口占比",
        "wfo_mean_ret": "样本外平均收益%", "wfo_std_ret": "样本外收益波动",
        "wfo_mean_sharpe": "样本外平均夏普", "wfo_worst_dd": "最差窗口回撤%",
        "wfo_chosen": "各窗最优参数",
        "scan_section": "参数热力图", "scan_best": "最优参数格",
        "chart_note": "可视化见下方 HTML 图表文件",
        "no_code": "请先生成或填入策略代码。", "fail": "稳健性分析失败",
        "unavailable": "无法分析", "computing": "稳健性分析后台计算中，回测结果已先行展示…",
    },
    "en": {
        "panel_title": "Robustness", "panel_desc": "IS/OOS split · Walk-Forward · Param heatmap",
        "run_button": "Run Robustness Analysis", "split_label": "In-Sample ratio (IS)",
        "title": "Robustness Analysis", "io_section": "In-Sample / Out-of-Sample", "boundary": "Split point",
        "col_in": "IS", "col_out": "OOS", "col_ratio": "OOS/IS",
        "flags_title": "Overfit red flags", "no_flags": "No overfit red flag triggered",
        "wfo_section": "Walk-Forward",
        "wfo_real": "True WFO (optimize on IS → evaluate on OOS each window)",
        "wfo_stability": "Fixed-strategy segmented OOS stability scan (no PARAM_SPACE)",
        "wfo_windows": "Windows", "wfo_valid": "Valid windows", "wfo_pos_ratio": "Positive-window ratio",
        "wfo_mean_ret": "Mean OOS return%", "wfo_std_ret": "OOS return std",
        "wfo_mean_sharpe": "Mean OOS Sharpe", "wfo_worst_dd": "Worst-window drawdown%",
        "wfo_chosen": "Chosen params per window",
        "scan_section": "Parameter heatmap", "scan_best": "Best cell",
        "chart_note": "See the HTML chart file below for visualization",
        "no_code": "Please generate or paste strategy code first.", "fail": "Robustness analysis failed",
        "unavailable": "Cannot analyze", "computing": "Robustness running in background; backtest result shown first…",
    },
    "ko": {
        "panel_title": "강건성 분석", "panel_desc": "표본 내/외 분할 · 워크포워드 · 파라미터 히트맵",
        "run_button": "강건성 분석 실행", "split_label": "표본내 비율 (IS)",
        "title": "강건성 분석", "io_section": "표본내 / 표본외", "boundary": "분할점",
        "col_in": "표본내", "col_out": "표본외", "col_ratio": "외/내",
        "flags_title": "과적합 경고선", "no_flags": "과적합 경고선 없음",
        "wfo_section": "워크포워드",
        "wfo_real": "정통 WFO (창마다 표본내 최적화 → 표본외 평가)",
        "wfo_stability": "고정 전략 구간별 표본외 안정성 스캔 (PARAM_SPACE 없음)",
        "wfo_windows": "창 수", "wfo_valid": "유효 창", "wfo_pos_ratio": "양(+) 수익 창 비율",
        "wfo_mean_ret": "표본외 평균수익%", "wfo_std_ret": "표본외 수익 변동",
        "wfo_mean_sharpe": "표본외 평균 샤프", "wfo_worst_dd": "최악 창 낙폭%",
        "wfo_chosen": "창별 최적 파라미터",
        "scan_section": "파라미터 히트맵", "scan_best": "최적 셀",
        "chart_note": "시각화는 아래 HTML 차트 파일 참조",
        "no_code": "먼저 전략 코드를 생성하거나 입력하세요.", "fail": "강건성 분석 실패",
        "unavailable": "분석 불가", "computing": "강건성 분석을 백그라운드에서 계산 중입니다. 백테스트 결과를 먼저 표시했습니다…",
    },
    "ja": {
        "panel_title": "ロバストネス分析", "panel_desc": "イン/アウト分割 · ウォークフォワード · パラメータヒートマップ",
        "run_button": "ロバストネス分析を実行", "split_label": "インサンプル比率 (IS)",
        "title": "ロバストネス分析", "io_section": "イン / アウトサンプル", "boundary": "分割点",
        "col_in": "イン", "col_out": "アウト", "col_ratio": "外/内",
        "flags_title": "過剰最適化レッドライン", "no_flags": "過剰最適化レッドラインなし",
        "wfo_section": "ウォークフォワード",
        "wfo_real": "正統WFO（各窓でイン最適化 → アウト評価）",
        "wfo_stability": "固定戦略の区間別アウト安定性スキャン（PARAM_SPACE未宣言）",
        "wfo_windows": "ウィンドウ数", "wfo_valid": "有効ウィンドウ", "wfo_pos_ratio": "プラス収益ウィンドウ比率",
        "wfo_mean_ret": "アウト平均収益%", "wfo_std_ret": "アウト収益変動",
        "wfo_mean_sharpe": "アウト平均シャープ", "wfo_worst_dd": "最悪ウィンドウDD%",
        "wfo_chosen": "各窓の最適パラメータ",
        "scan_section": "パラメータヒートマップ", "scan_best": "最適セル",
        "chart_note": "可視化は下のHTMLチャートファイルを参照",
        "no_code": "先に戦略コードを生成または入力してください。", "fail": "ロバストネス分析に失敗",
        "unavailable": "分析できません", "computing": "ロバストネス分析をバックグラウンドで計算中。バックテスト結果を先に表示しました…",
    },
    "ar": {
        "panel_title": "تحليل المتانة", "panel_desc": "تقسيم داخل/خارج العينة · Walk-Forward · خريطة حرارية للمعاملات",
        "run_button": "تشغيل تحليل المتانة", "split_label": "نسبة داخل العينة (IS)",
        "title": "تحليل المتانة", "io_section": "داخل / خارج العينة", "boundary": "نقطة التقسيم",
        "col_in": "داخل", "col_out": "خارج", "col_ratio": "خارج/داخل",
        "flags_title": "خطوط حمراء للإفراط في التحسين", "no_flags": "لم تُفعَّل أي خطوط حمراء",
        "wfo_section": "Walk-Forward",
        "wfo_real": "WFO تقليدي (تحسين داخل العينة ← تقييم خارجها لكل نافذة)",
        "wfo_stability": "مسح استقرار خارج العينة لاستراتيجية ثابتة (لا يوجد PARAM_SPACE)",
        "wfo_windows": "عدد النوافذ", "wfo_valid": "نوافذ صالحة", "wfo_pos_ratio": "نسبة النوافذ الموجبة",
        "wfo_mean_ret": "متوسط عائد خارج العينة%", "wfo_std_ret": "تقلب عائد خارج العينة",
        "wfo_mean_sharpe": "متوسط شارب خارج العينة", "wfo_worst_dd": "أسوأ تراجع نافذة%",
        "wfo_chosen": "أفضل معاملات لكل نافذة",
        "scan_section": "خريطة حرارية للمعاملات", "scan_best": "أفضل خلية",
        "chart_note": "انظر ملف الرسم HTML أدناه للتصور",
        "no_code": "يرجى توليد أو لصق كود الاستراتيجية أولاً.", "fail": "فشل تحليل المتانة",
        "unavailable": "تعذّر التحليل", "computing": "يجري تحليل المتانة في الخلفية، وقد عُرضت نتيجة الاختبار الخلفي أولاً…",
    },
    "ru": {
        "panel_title": "Устойчивость", "panel_desc": "Разбиение IS/OOS · Walk-Forward · Тепловая карта",
        "run_button": "Запустить анализ устойчивости", "split_label": "Доля в выборке (IS)",
        "title": "Анализ устойчивости", "io_section": "В выборке / Вне выборки", "boundary": "Точка разбиения",
        "col_in": "В выб.", "col_out": "Вне выб.", "col_ratio": "Вне/В",
        "flags_title": "Красные флаги переобучения", "no_flags": "Красные флаги переобучения не сработали",
        "wfo_section": "Walk-Forward",
        "wfo_real": "Классический WFO (оптимизация на IS → оценка на OOS в каждом окне)",
        "wfo_stability": "Скан стабильности OOS фиксированной стратегии (нет PARAM_SPACE)",
        "wfo_windows": "Окон", "wfo_valid": "Валидных окон", "wfo_pos_ratio": "Доля прибыльных окон",
        "wfo_mean_ret": "Средняя доходность OOS%", "wfo_std_ret": "Волатильность доходности OOS",
        "wfo_mean_sharpe": "Средний Шарп OOS", "wfo_worst_dd": "Худшая просадка окна%",
        "wfo_chosen": "Лучшие параметры по окнам",
        "scan_section": "Тепловая карта параметров", "scan_best": "Лучшая ячейка",
        "chart_note": "Визуализацию см. в HTML-файле графика ниже",
        "no_code": "Сначала сгенерируйте или вставьте код стратегии.", "fail": "Анализ устойчивости не удался",
        "unavailable": "Невозможно проанализировать", "computing": "Анализ устойчивости считается в фоне; результат бэктеста показан первым…",
    },
}
# =========================================================
# 基础工具函数
# =========================================================

def get_ui_text(lang_code: str) -> dict:
    return UI_TEXTS.get(lang_code, UI_TEXTS["zh"])


def get_summary_text(lang_code: str) -> dict:
    return SUMMARY_TEXTS.get(lang_code, SUMMARY_TEXTS["zh"])


def normalize_date(date_value, default_value: str, lang_code: str = "zh") -> str:
    """
    把用户输入的日期统一转成 YYYY-MM-DD。

    支持：
        2017-01-13
        2017/01/13
        2017.1.13
        2017年1月13日
        13/1/2017
        13-1-2017
        13.1.2017
        1/13/2017

    规则：
        1. 如果第一个数字是 4 位，按 年/月/日 解析。
        2. 如果第三个数字是 4 位：
           - 第一位 > 12，按 日/月/年。
           - 第二位 > 12，按 月/日/年。
           - 都不大于 12 时，默认按 日/月/年。
    """

    text = get_ui_text(lang_code)

    if date_value is None or str(date_value).strip() == "":
        return default_value

    raw = str(date_value).strip()

    # Gradio DateTime 可能返回：
    # 2017-01-13 00:00:00
    # 2017-01-13T00:00:00
    raw = raw.split(" ")[0].strip()
    raw = raw.split("T")[0].strip()

    # 提取数字，兼容 2017.1.13 / 13/1/2017 / 2017年1月13日
    parts = re.findall(r"\d+", raw)

    if len(parts) < 3:
        raise ValueError(text["invalid_date_error"])

    a, b, c = parts[0], parts[1], parts[2]

    try:
        # 年/月/日：2017-01-13、2017.1.13、2017/1/13
        if len(a) == 4:
            year = int(a)
            month = int(b)
            day = int(c)

        # 日/月/年 或 月/日/年：13/1/2017、1/13/2017
        elif len(c) == 4:
            year = int(c)
            first = int(a)
            second = int(b)

            if first > 12 and second <= 12:
                # 13/1/2017 -> 2017-01-13
                day = first
                month = second
            elif second > 12 and first <= 12:
                # 1/13/2017 -> 2017-01-13
                month = first
                day = second
            else:
                # 1/2/2017 这种有歧义，默认按 日/月/年
                day = first
                month = second

        else:
            raise ValueError(text["invalid_date_error"])

        parsed_date = datetime(year, month, day)
        return parsed_date.strftime("%Y-%m-%d")

    except Exception:
        raise ValueError(text["invalid_date_error"])


def validate_date_range(start_str: str, end_str: str, lang_code: str):
    """
    校验日期范围。
    start_str / end_str 必须已经是 YYYY-MM-DD。
    """

    text = get_ui_text(lang_code)

    if start_str < "2017-01-01":
        raise ValueError(text["start_time_error"])

    if end_str < start_str:
        raise ValueError(text["date_order_error"])


def format_number(value, digits: int = 2, na_text: str = "-") -> str:
    if value is None:
        return na_text

    try:
        value = float(value)
    except Exception:
        return na_text

    if math.isnan(value):
        return na_text

    if math.isinf(value):
        return "∞"

    return f"{value:.{digits}f}"


def parse_float_input(value, default_value: float, lang_code: str) -> float:
    text = get_ui_text(lang_code)

    if value is None:
        return default_value

    value_str = str(value).strip()

    if value_str == "":
        return default_value

    try:
        parsed = float(value_str)
    except Exception:
        raise ValueError(text["invalid_number_error"])

    if not math.isfinite(parsed):
        raise ValueError(text["invalid_number_error"])

    return parsed


def parse_int_input(value, default_value: int, lang_code: str) -> int:
    text = get_ui_text(lang_code)

    if value is None:
        return default_value

    value_str = str(value).strip()

    if value_str == "":
        return default_value

    try:
        value_float = float(value_str)
    except Exception:
        raise ValueError(text["invalid_number_error"])

    if not value_float.is_integer():
        raise ValueError(text["invalid_leverage_error"])

    return int(value_float)


def validate_backtest_params(
    initial_cash,
    leverage,
    position_size_percent,
    fee_rate_percent,
    slippage_percent,
    lang_code: str,
):
    text = get_ui_text(lang_code)

    initial_cash_value = parse_float_input(initial_cash, 1000.0, lang_code)
    leverage_value = parse_int_input(leverage, 1, lang_code)
    position_size_percent_value = parse_float_input(position_size_percent, 100.0, lang_code)
    fee_rate_percent_value = parse_float_input(fee_rate_percent, 0.05, lang_code)
    slippage_percent_value = parse_float_input(slippage_percent, 0.0, lang_code)

    if initial_cash_value <= 0:
        raise ValueError(text["invalid_initial_cash_error"])

    if leverage_value < 0 or leverage_value > 200:
        raise ValueError(text["invalid_leverage_error"])

    if position_size_percent_value < 0 or position_size_percent_value > 100:
        raise ValueError(text["invalid_position_size_error"])

    if fee_rate_percent_value < 0:
        raise ValueError(text["invalid_fee_rate_error"])

    if slippage_percent_value < 0:
        raise ValueError(text["invalid_slippage_error"])

    effective_leverage_value = 1 if leverage_value == 0 else leverage_value

    return (
        initial_cash_value,
        leverage_value,
        effective_leverage_value,
        position_size_percent_value,
        fee_rate_percent_value,
        slippage_percent_value,
    )


def get_review_text(lang_code: str) -> dict:
    return REVIEW_TEXTS.get(lang_code, REVIEW_TEXTS["zh"])


def get_fetch_text(lang_code: str) -> dict:
    return FETCH_TEXTS.get(lang_code, FETCH_TEXTS["zh"])


def get_robustness_text(lang_code: str) -> dict:
    return ROBUSTNESS_TEXTS.get(lang_code, ROBUSTNESS_TEXTS["zh"])


def tooltip_html(inner_html: str) -> str:
    """统一的 "!" 悬停气泡（参数优先级 / 进度清单 / 行为检查共用）。

    inner_html 必须是调用方已转义/构造好的安全 HTML（可含 <br>）。
    """
    return (
        '<span class="qtbs-tooltip">'
        '<span class="qtbs-tooltip-icon">!</span>'
        f'<span class="qtbs-tooltip-text">{inner_html}</span>'
        "</span>"
    )


def _fetch_batch_mode(snapshot: dict) -> str:
    """整批的模式标签：只要含任一首次拉取的币种就算「首次拉取」。"""
    modes = [m for _, m in snapshot.get("symbols", [])]
    return "initial" if "initial" in modes else "update"


def build_fetch_progress_html(snapshot: dict, lang_code: str) -> str:
    """渲染数据拉取进度区：总进度条 + 当前币种脉冲 + 小字 + "!" 悬停清单。"""

    text = get_fetch_text(lang_code)
    running = snapshot.get("running")
    recently_done = snapshot.get("recently_done")
    total = snapshot.get("total", 0)
    done = snapshot.get("done", 0)
    current = snapshot.get("current")
    symbols = snapshot.get("symbols", [])
    errors = snapshot.get("errors", [])

    batch_mode = _fetch_batch_mode(snapshot)
    mode_label = text[batch_mode]

    # 悬停清单：本批全部币种 + 各自模式；空闲时给说明
    if symbols:
        tip_head = text["tip_initial"] if batch_mode == "initial" else text["tip_update"]
        lines = []
        for sym, m in symbols:
            tag = text[m]
            mark = ""
            if sym in errors:
                mark = f" ({text['failed']})"
            elif current == sym:
                mark = " ●"
            lines.append(f"{html.escape(sym)} — {tag}{mark}")
        tip = tip_head + "<br>" + "<br>".join(lines)
    else:
        tip = text["tip_idle"]

    tip_html = tooltip_html(tip)

    pct = int(done / total * 100) if total > 0 else 0

    if running:
        status = text["running"].format(mode=mode_label)
        cur_name = html.escape(current) if current else ""
        cur_tag = text[snapshot.get("current_mode") or "update"]
        current_line = (
            f'<div class="fetch-current-label">{text["current"]}: '
            f'<b>{cur_name}</b> · {cur_tag}</div>'
            '<div class="fetch-pulse-track"><div class="fetch-pulse-bar"></div></div>'
        )
    elif recently_done:
        status = text["done_all"].format(mode=mode_label)
        current_line = ""
        pct = 100
    else:
        status = text["idle"]
        current_line = ""

    return f"""
    <div id="fetch-progress" class="{'fetch-active' if running else ''}">
        <div class="fetch-head">
            <span class="fetch-status">{html.escape(status)}</span>
            {tip_html}
        </div>
        <div class="fetch-total-row">
            <div class="fetch-total-track">
                <div class="fetch-total-bar" style="width:{pct}%;"></div>
            </div>
            <span class="fetch-total-num">{done}/{total}</span>
        </div>
        {current_line}
    </div>
    """


def refresh_fetch_progress(lang_code: str):
    """Timer 轮询：渲染进度 HTML + 按钮可用性（拉取中禁用）。"""
    snap = fetch_queue.snapshot()
    text = get_fetch_text(lang_code)
    running = snap.get("running")
    return (
        build_fetch_progress_html(snap, lang_code),
        gr.update(
            value=text["button_running"] if running else text["button"],
            interactive=not running,
        ),
    )


def _enqueue_local_or_default():
    """扫描本地交易对入队更新；首次使用（本地为空）则入队默认币种初次拉取。"""
    local = list_local_symbols(DEFAULT_DATA_DIR)
    targets = local if local else fetch_queue.DEFAULT_INITIAL_SYMBOLS
    fetch_queue.enqueue(targets, DEFAULT_DATA_DIR)


def on_app_start(lang_code: str):
    """页面加载：空闲则扫描本地交易对依次更新（首次使用拉默认币种）。

    用 is_running 守卫而非一次性 latch：若上次启动批量失败（如启动时断网），
    后续页面加载/刷新会重试，不会永久停在「数据已是最新」的误导态。
    """
    if not fetch_queue.is_running():
        _enqueue_local_or_default()
    return refresh_fetch_progress(lang_code)


def on_manual_update(lang_code: str):
    """手动更新按钮：更新本地全部交易对；正在拉取时点击无效。"""
    if not fetch_queue.is_running():
        _enqueue_local_or_default()
    return refresh_fetch_progress(lang_code)


def on_integrity_check(lang_code: str):
    """手动「检查并修复数据」按钮：对本地全部交易对【强制全量】缺口检查 + 回补
    （忽略白名单），随后增量更新；正在拉取时点击无效。复用同一拉取队列与进度区。"""
    if not fetch_queue.is_running():
        local = list_local_symbols(DEFAULT_DATA_DIR)
        targets = local if local else fetch_queue.DEFAULT_INITIAL_SYMBOLS
        fetch_queue.enqueue(targets, DEFAULT_DATA_DIR, force_integrity=True)
    return refresh_fetch_progress(lang_code)


def parse_common_ui_params(
    output_language: str,
    symbol,
    start_time,
    end_time,
    initial_cash,
    leverage,
    position_size_percent,
    fee_rate_percent,
    slippage_percent,
):
    """
    生成与回测两个入口共用的 UI 参数解析（缺省值与校验规则单源）。

    两个入口各抄一份时，任何缺省值/校验规则只改一边都会让 AI 生成
    所见的回测环境与实际回测参数静默分叉。

    返回 (start_str, end_str, symbol, 校验后的六元组)；解析失败抛异常。
    """

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    start_str = normalize_date(start_time, "2017-01-01", output_language)
    end_str = normalize_date(end_time, today_utc, output_language)
    validate_date_range(start_str, end_str, output_language)

    if symbol is None or symbol.strip() == "":
        symbol = "BTC"
    symbol = normalize_symbol(symbol)

    params = validate_backtest_params(
        initial_cash=initial_cash,
        leverage=leverage,
        position_size_percent=position_size_percent,
        fee_rate_percent=fee_rate_percent,
        slippage_percent=slippage_percent,
        lang_code=output_language,
    )

    return start_str, end_str, symbol, params


# resolve_strategy_route 已抽到 module/Strategy/backtest_runner.py（无 Gradio，
# 供 webUI 与稳健性分析层共用），此处经顶部 import 再导出，test_webui_helpers
# 的 `from webUI import resolve_strategy_route` 不受影响。


def build_review_html(review: dict | None, lang_code: str = "zh") -> str:
    text = get_review_text(lang_code)

    if not review:
        return f"""
        <div class="review-card">
            <div class="review-title">{text["title"]}</div>
            <div class="review-empty">{text["empty"]}</div>
        </div>
        """

    match_score = clamp_score(review.get("match_score", 0))
    # AI 返回的文本插入 HTML 前必须转义，防止内容里的 < > & 破坏页面结构
    match_summary = html.escape(str(review.get("match_summary", "")))

    behavior = review.get("behavior")
    behavior_html = ""
    if behavior:
        # 表面只显示「✓ 行为检查通过 / ✗ 行为检查失败」；具体跑了多少根、
        # 多少笔交易（或报错原因）+ 行为检查是什么，全部进 "!" 悬停详解
        if behavior.get("ok"):
            short = text["behavior_pass_short"]
            detail = text["behavior_pass"].format(
                bars=behavior.get("synthetic_bars", 0),
                trades=behavior.get("trade_count", 0),
            )
            cls, icon = "behavior-pass", "✓"
        else:
            short = text["behavior_fail_short"]
            detail = text["behavior_fail"].format(error=str(behavior.get("error", "")))
            cls, icon = "behavior-fail", "✗"

        # detail 与 behavior_tip 各自转义后用 <br> 拼接（<br> 不能被转义）
        tip_full = (
            html.escape(detail) + "<br><br>" + html.escape(text.get("behavior_tip", ""))
        )
        behavior_html = (
            f'<div class="review-behavior {cls}">'
            f'<span>{icon} {html.escape(short)}</span> {tooltip_html(tip_full)}</div>'
        )

    return f"""
    <div class="review-card">
        <div class="review-layout">
            <div class="review-left">
                <div class="review-title">{text["match"]}</div>

                <div class="score-row">
                    <div class="score-bar-bg">
                        <div class="score-bar-fill" style="width:{match_score}%;"></div>
                    </div>
                    <div class="score-number">{match_score:.2f}%</div>
                </div>
                {behavior_html}
            </div>

            <div class="review-right">
                <div class="review-summary-title">{text["desc_title"]}</div>
                <div class="review-summary">{match_summary}</div>
                <div class="review-note">{text["note"]}</div>
            </div>
        </div>
    </div>
    """

def build_backtest_summary(
    metrics: dict,
    lang_code: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_str: str,
    end_str: str,
    kline_count: int,
    leverage_value: int,
    effective_leverage_value: int,
    position_size_percent_value: float,
    fee_rate_percent_value: float,
    slippage_percent_value: float,
    chart_path: str,
) -> str:
    text = get_summary_text(lang_code)
    na = text["na"]

    return f"""
{text["completed"]}

{text["market"]}：{market}
{text["symbol"]}：{symbol}
{text["timeframe"]}：{timeframe}
{text["start_time"]}：{start_str}
{text["end_time"]}：{end_str}
{text["kline_count"]}：{kline_count}

{text["initial_cash"]}：{format_number(metrics.get("initial_cash"), 2, na)}
{text["leverage"]}：{leverage_value}x
{text["effective_leverage"]}：{effective_leverage_value}x
{text["position_size"]}：{format_number(position_size_percent_value, 2, na)}%
{text["fee_rate"]}：{format_number(fee_rate_percent_value, 4, na)}%
{text["slippage"]}：{format_number(slippage_percent_value, 4, na)}%

{text["final_equity"]}：{format_number(metrics.get("final_equity"), 2, na)}
{text["total_return"]}：{format_number(metrics.get("total_return_pct"), 2, na)}%
{text["annual_return"]}：{format_number(metrics.get("annual_return_pct"), 2, na)}%
{text["max_drawdown"]}：{format_number(metrics.get("max_drawdown_pct"), 2, na)}%
{text["sharpe_ratio"]}：{format_number(metrics.get("sharpe_ratio"), 2, na)}
{text["funding_cost"]}：{format_number(metrics.get("total_funding_cost", 0.0), 2, na)}

{text["trade_count"]}：{metrics.get("trade_count", 0)}
{text["gross_win_rate"]}：{format_number(metrics.get("gross_win_rate"), 2, na)}%
{text["net_win_rate"]}：{format_number(metrics.get("net_win_rate"), 2, na)}%
{text["avg_profit"]}：{format_number(metrics.get("avg_profit"), 2, na)}
{text["avg_loss"]}：{format_number(metrics.get("avg_loss"), 2, na)}
{text["payoff_ratio"]}：{format_number(metrics.get("payoff_ratio"), 2, na)}
{text["profit_factor"]}：{format_number(metrics.get("profit_factor"), 2, na)}

{text["max_consecutive_wins"]}：{metrics.get("max_consecutive_wins", 0)}
{text["max_consecutive_losses"]}：{metrics.get("max_consecutive_losses", 0)}
{text["avg_holding_hours"]}：{format_number(metrics.get("avg_holding_hours"), 2, na)}

{text["chart_file"]}：
{chart_path}
"""


# =========================================================
# UI 文案动态更新
# =========================================================

def build_param_priority_html(text: dict) -> str:
    """一行简短声明 + 悬停感叹号展开详情（纯 CSS 气泡，不依赖 JS）。"""

    short = html.escape(text["param_priority_short"])
    detail = html.escape(text["param_priority_note"])

    return (
        '<div id="param-priority-note">'
        f"<span>{short}</span>"
        f"{tooltip_html(detail)}"
        "</div>"
    )


def update_ui_language(
    lang_code: str,
    market: str = "crypto",
    timeframe: str = "4h",
    initial_cash=1000,
):
    text = get_ui_text(lang_code)
    _snap = fetch_queue.snapshot()  # 进度区与按钮共用一次快照

    return [
        build_studio_header_markdown(lang_code),
        build_status_cards_html(lang_code, market, timeframe, initial_cash),
        gr.update(
            label=text["strategy_label"],
            placeholder=text["strategy_placeholder"],
        ),
        gr.update(
            label=text["language_label"],
            choices=LANGUAGE_CHOICES,
            value=lang_code,
        ),
        gr.update(
            label=text["market_label"],
            choices=[(text["market_choice"], "crypto")],
            value="crypto",
        ),
        gr.update(
            label=text["symbol_label"],
            placeholder=text["symbol_placeholder"],
        ),
        gr.update(
            label=text["timeframe_label"],
        ),
        build_param_priority_html(text),
        gr.update(
            label=text["start_label"],
        ),
        gr.update(
            label=text["end_label"],
        ),
        gr.update(
            label=text["initial_cash_label"],
        ),
        gr.update(
            label=text["leverage_label"],
        ),
        gr.update(
            label=text["position_size_label"],
        ),
        gr.update(
            label=text["fee_rate_label"],
        ),
        gr.update(
            label=text["slippage_label"],
        ),
        gr.update(
            value=text["generate_button"],
        ),
        gr.update(
            value=text["backtest_button"],
        ),
        # 代码折叠面板的标题随语言切换（标题在 Accordion 上，不是内部 gr.Code）
        gr.update(label=text["code_output_label"]),
        gr.update(
            value=build_review_html(None, lang_code),
        ),
        # 回测结果现为 gr.HTML 可视化仪表盘：切语言时不重渲染（保留当前结果、不清空），
        # 下次运行回测会以新语言重画。gr.update() 无参 = 不改动该组件
        gr.update(),
        # 进度区按新语言重渲染（读一次快照，不重置进度，也不再额外 is_running）
        gr.update(value=build_fetch_progress_html(_snap, lang_code)),
        gr.update(
            value=get_fetch_text(lang_code)[
                "button_running" if _snap["running"] else "button"
            ]
        ),
        # 数据「检查并修复」按钮随语言切换
        gr.update(value=get_fetch_text(lang_code)["button_integrity"]),
        # 「查看详细图表」+「查看稳健性图表」+「查看历史」标签随语言切换
        # （4 项，与 outputs 末尾 4 个组件一一对应）
        gr.update(value=text["view_chart_button"]),
        gr.update(value=text["view_robustness_chart_button"]),
        gr.update(label=text["history_select_label"]),
        gr.update(value=text["history_refresh"]),
    ]

# =========================================================
# AI 生成策略代码
# =========================================================

def generate_code_from_ui(
    strategy_text: str,
    output_language: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_time,
    end_time,
    initial_cash,
    leverage,
    position_size_percent,
    fee_rate_percent,
    slippage_percent,
):
    text = get_ui_text(output_language)

    if strategy_text is None or strategy_text.strip() == "":
        return f"# {text['empty_strategy_error']}", build_review_html(None, output_language)

    try:
        start_str, end_str, symbol, (
            initial_cash_value,
            leverage_value,
            effective_leverage_value,
            position_size_percent_value,
            fee_rate_percent_value,
            slippage_percent_value,
        ) = parse_common_ui_params(
            output_language, symbol, start_time, end_time,
            initial_cash, leverage, position_size_percent,
            fee_rate_percent, slippage_percent,
        )
    except Exception as e:
        return f"# {str(e)}", build_review_html(None, output_language)

    try:
        strategy_code = generate_strategy_code_with_deepseek(
            user_text=strategy_text,
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            language=LANGUAGE_DISPLAY_NAMES.get(output_language, "简体中文"),
            allow_short=False,
            initial_cash=initial_cash_value,
            fee_rate_percent=fee_rate_percent_value,
            slippage_percent=slippage_percent_value,
            available_symbols=list_local_symbols(),
        )

        # 行为审查：交给审查 AI 之前先在合成数据上真实跑一遍引擎
        # （不调用 API、永不抛异常）。运行时错误在这里就拦截成事实，
        # 审查 AI 拿到的是「代码实际做了什么」而不只是代码文本
        behavior = run_behavior_check(strategy_code)

        try:
            review = review_strategy_code_with_deepseek(
                user_strategy_text=strategy_text,
                generated_code=strategy_code,
                language=output_language,
                behavior_summary=format_behavior_summary(behavior),
            )

            review["behavior"] = behavior
            review_html = build_review_html(review, output_language)

        except Exception as review_error:
            review_text = get_review_text(output_language)

            review_html = build_review_html(
                {
                    "match_score": 0,
                    "match_summary": f"{review_text['review_failed']}：{str(review_error)}",
                    "behavior": behavior,
                },
                output_language,
            )

        return strategy_code, review_html

    except Exception as e:
        return (
            f"# {text['api_fail_error']}\n# {str(e)}",
            build_review_html(None, output_language),
        )


# =========================================================
# 运行回测
# =========================================================

def _result_error_html(msg: str) -> str:
    """回测出错时在结果仪表盘位以醒目红字展示（gr.HTML）。"""
    return (
        '<div style="padding:14px;border-radius:12px;background:#fdeced;'
        f'color:#ea3943;font-size:14px;line-height:1.5">{html.escape(str(msg))}</div>'
    )


def _robustness_html(report_text: str, lang_code: str) -> str:
    """把稳健性文字报告包成自带样式的 HTML 块（紧随回测仪表盘展示，等宽 <pre>）。
    report_text 为空 ⇒ 空串（不占版面）。"""
    if not report_text:
        return ""
    title = get_robustness_text(lang_code).get("title", "Robustness")
    esc = html.escape
    return (
        '<div style="margin-top:16px">'
        f'<div style="font-size:15px;font-weight:700;margin:8px 0">{esc(title)}</div>'
        '<pre style="background:#f7f8fa;border-radius:12px;padding:14px 16px;margin:0;'
        'font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.55;'
        'color:#2b2f36;white-space:pre-wrap;word-break:break-word;max-height:480px;overflow:auto">'
        f'{esc(report_text)}</pre></div>'
    )


def _robustness_pending_html(lang_code: str) -> str:
    """回测结果先出、稳健性后台计算时的占位（带轻动效的「计算中」提示）。"""
    rt = get_robustness_text(lang_code)
    title = rt.get("title", "Robustness")
    pending = rt.get("computing", "…")
    esc = html.escape
    return (
        '<div style="margin-top:16px">'
        f'<div style="font-size:15px;font-weight:700;margin:8px 0">{esc(title)}</div>'
        '<div style="background:#f7f8fa;border-radius:12px;padding:16px;color:#9aa0ab;'
        f'font-size:13px">⏳ {esc(pending)}</div></div>'
    )


def _compute_robustness(strategy_code, prepared, engine_params, symbol, timeframe,
                        start_str, end_str, output_language):
    """在【已加载的 prepared】上跑稳健性分析（默认 70% IS 切分）+ 出图(不自动弹) + 渲染报告。
    返回 (报告HTML, 图表路径, 报告文本)。复用 prepared 故数据不二次加载；最佳努力——
    失败/数据不足返回提示文本，绝不影响回测主结果展示。"""
    rt = get_robustness_text(output_language)
    try:
        report = run_full_analysis(
            strategy_code, symbol, timeframe, start_str, end_str,
            engine_params=engine_params, split_ratio=0.7, prepared=prepared,
        )
        chart = plot_robustness(
            report, output_dir="Past_data",
            file_prefix=f"{symbol}_{timeframe}_webui_robustness",
            language=output_language, auto_open=False,
        )
        text = build_robustness_summary(report, output_language, chart)
        return _robustness_html(text, output_language), chart, text
    except Exception as e:
        text = f"{rt['fail']}：{e}"
        return _robustness_html(text, output_language), None, text


def run_backtest_from_ui(
    strategy_code: str,
    output_language: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_time,
    end_time,
    initial_cash,
    leverage,
    position_size_percent,
    fee_rate_percent,
    slippage_percent,
    prompt="",
):
    text = get_ui_text(output_language)
    summary_text = get_summary_text(output_language)

    try:
        if strategy_code is None or strategy_code.strip() == "":
            yield _result_error_html(text["no_code_error"]), None, "", None
            return

        try:
            start_str, end_str, symbol, (
                initial_cash_value,
                leverage_value,
                effective_leverage_value,
                position_size_percent_value,
                fee_rate_percent_value,
                slippage_percent_value,
            ) = parse_common_ui_params(
                output_language, symbol, start_time, end_time,
                initial_cash, leverage, position_size_percent,
                fee_rate_percent, slippage_percent,
            )
        except Exception as e:
            yield _result_error_html(str(e)), None, "", None
            return

        fee_rate_value = fee_rate_percent_value / 100
        slippage_value = slippage_percent_value / 100
        position_size_value = position_size_percent_value / 100

        # 单次回测编排（路由→加载策略→按版本加载数据→funding→选引擎 run）已抽到
        # backtest_runner.run_once（无 Gradio，与稳健性分析层共用同一内核、杜绝执行
        # 路径分叉）；出图仍在此（webUI 专属）。代码留档在此调一次（稳健性多次回测不审计）。
        save_strategy_code_audit(strategy_code)
        engine_params = dict(
            initial_cash=initial_cash_value,
            fee_rate=fee_rate_value,
            slippage=slippage_value,
            leverage=effective_leverage_value,
            position_size=position_size_value,
        )
        try:
            # 显式 load + run（替代 run_once）以保留 prepared，供稳健性分析复用——
            # 同一份已加载数据切窗复跑，数据不二次加载。
            prepared = load_for_backtest(strategy_code, symbol, timeframe, start_str, end_str)
            run = run_prepared(prepared, engine_params, min_klines=100)
        except InsufficientKlinesError as e:
            yield _result_error_html(text["too_few_klines_error"].format(count=e.kline_count)), None, "", None
            return

        result = run["result"]
        route_version = run["route_version"]
        route_symbols = run["route_symbols"]
        display_symbol = run["display_symbol"]
        kline_count = run["kline_count"]
        # 摘要展示实际参与回测的数据范围（本地数据未覆盖请求窗口时不贴长周期标签）
        actual_start = run["actual_start"]
        actual_end = run["actual_end"]

        # ---- 出图：分支体只保留真正不同的部分 ----

        if route_version == 2:
            base_names = "_".join(get_base_asset(s) for s in route_symbols[:4])
            html_path = plot_portfolio_result(
                result=result,
                output_dir="Past_data",
                file_prefix=f"{base_names}_{timeframe}_webui_portfolio",
                timeframe=timeframe,
                language=output_language,
                auto_open=False,
            )
        else:
            symbol = route_symbols[0]
            html_path = plot_generic_equity_curves(
                result=result,
                output_dir="Past_data",
                file_prefix=f"{symbol}_{timeframe}_webui_code_strategy",
                auto_open=False,
                language=output_language,
            )

        metrics = result["metrics"]
        chart_path = html_path if html_path else summary_text["chart_path_missing"]

        summary = build_backtest_summary(
            metrics=metrics,
            lang_code=output_language,
            market=market,
            symbol=display_symbol,
            timeframe=timeframe,
            start_str=actual_start,
            end_str=actual_end,
            kline_count=kline_count,
            leverage_value=leverage_value,
            effective_leverage_value=effective_leverage_value,
            position_size_percent_value=position_size_percent_value,
            fee_rate_percent_value=fee_rate_percent_value,
            slippage_percent_value=slippage_percent_value,
            chart_path=chart_path,
        )

        trades = result.get("trades", [])
        equity = [p.get("equity_close") for p in result.get("equity_curve", [])]

        # 输出台以可视化仪表盘展示结果（大字总盈亏 + 权益曲线 + 指标卡 + 成交/订单列表）；
        # 人类可读文字摘要仍随历史留档（下方 summary）。
        dashboard = build_dashboard_html(
            metrics,
            trades,
            {
                "symbol": display_symbol, "timeframe": timeframe,
                "start": actual_start, "end": actual_end,
                "kline_count": kline_count, "initial_cash": initial_cash_value,
                "equity": equity,
            },
            summary_text,
            output_language,
        )

        # 异步分两步出（生成器）：① 先把回测仪表盘 + 回测图路径推给前端、稳健性区显示
        # 「计算中」占位——用户立即看到回测结果；② 再后台跑稳健性分析、出图、补进稳健性区。
        yield dashboard, html_path, _robustness_pending_html(output_language), None

    except Exception as e:
        # 仅捕获【第一次 yield 之前】的回测异常 → 错误展示在主仪表盘位
        yield _result_error_html(f"{text['backtest_fail_error']}：{str(e)}"), None, "", None
        return

    # —— 第一次 yield 之后（稳健性 + 留档 + 第二次 yield）独立兜底：此段任何意外都【绝不】
    # 冲掉已展示的回测结果——主仪表盘一律 gr.update() 空操作，失败只在稳健性区显示。——
    try:
        # 稳健性分析：每次回测默认（70% IS 切分）自动跑一遍，复用已加载的 prepared
        # （数据不二次加载）；图表不自动弹出，路径交「查看稳健性图表」按钮按需打开。
        # 最佳努力：失败/数据不足不影响回测主结果。
        robustness_html, robustness_chart, robustness_text = _compute_robustness(
            strategy_code, prepared, engine_params, display_symbol, timeframe,
            actual_start, actual_end, output_language)

        # 历史留档：把提示词 + 代码 + 参数 + 关键指标 + 成交/权益 + 图表 + 稳健性报告落成
        # 一份自包含 JSON（Past_data/runs/），供事后追溯/复现与「查看历史」重渲。
        # 最佳努力——留档失败绝不影响回测结果展示。
        try:
            save_run_record(build_run_record(
                prompt=prompt,
                strategy_code=strategy_code,
                market=market,
                params={
                    "symbol": display_symbol,
                    "route_symbols": route_symbols,
                    "contract_version": route_version,
                    "timeframe": timeframe,
                    "requested_start": start_str,
                    "requested_end": end_str,
                    "actual_start": actual_start,
                    "actual_end": actual_end,
                    "kline_count": kline_count,
                    "initial_cash": initial_cash_value,
                    "leverage": leverage_value,
                    "effective_leverage": effective_leverage_value,
                    "position_size_percent": position_size_percent_value,
                    "fee_rate_percent": fee_rate_percent_value,
                    "slippage_percent": slippage_percent_value,
                },
                metrics=metrics,
                summary=summary,
                chart_file=html_path,
                trades=trades,
                equity=equity,
                robustness=robustness_text,
                robustness_chart=robustness_chart,
                timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            ))
        except Exception as record_err:
            print(f"[run_history] 历史留档失败（不影响回测）：{record_err}")

        # ② 补进稳健性区 + 稳健性图路径。主仪表盘用 gr.update() 空操作（保持①已展示的结果、
        # 不重发 48KB HTML）；回测图路径 html_path 原样回传（同值，不对 gr.State 写 gr.update()）。
        yield gr.update(), html_path, robustness_html, robustness_chart

    except Exception as post_err:
        # 第一次 yield 后的意外：主仪表盘 gr.update() 保持不变，错误只落到稳健性区
        rt_fail = get_robustness_text(output_language)["fail"]
        yield gr.update(), html_path, _robustness_html(f"{rt_fail}：{post_err}", output_language), None


# =========================================================
# 详细图表（按钮触发）+ 查看历史
# =========================================================

def on_view_chart(chart_path, output_language):
    """「查看详细图表」：本地浏览器打开已落盘的图表 HTML（回测不再自动弹出，改按需）。"""
    text = get_ui_text(output_language)
    # isinstance 守卫防御纵深：chart_path 仅应为 str/None；万一被写成非字符串，
    # os.path.exists 在 try 之外、对非路径对象会抛 TypeError，这里先挡掉
    if not isinstance(chart_path, str) or not os.path.exists(chart_path):
        gr.Warning(text["view_chart_missing"])
        return
    try:
        webbrowser.open(chart_path)   # 与回测原 auto_open 同口径（返回值即此路径）
    except Exception as e:
        gr.Warning(f"{text['view_chart_missing']}（{e}）")


def _history_label(record, fallback_name):
    """历史下拉项标签：时间 · 标的 · 周期 · 总收益%（信息缺失时降级到文件名）。"""
    ts = record.get("timestamp_utc") or ""
    params = record.get("params") or {}
    sym = params.get("symbol") or ""
    tf = params.get("timeframe") or ""
    ret = (record.get("metrics") or {}).get("total_return_pct")
    ret_str = f"{ret:+.2f}%" if isinstance(ret, (int, float)) else ""
    parts = [p for p in [ts, sym, tf, ret_str] if p]
    return " · ".join(parts) or fallback_name


# 历史下拉只列最近 N 条：每条 label 需读整份 JSON（含 trades/equity，可达数百 KB），
# 不设上限则在启动 + 每次回测后刷新 + 手动刷新时全量解析，历史一多即明显卡顿。
_HISTORY_LIST_LIMIT = 200


def _history_choices():
    """枚举最近 _HISTORY_LIST_LIMIT 条历史 → [(label, filepath), ...]，最新在前。
    读不动的记录跳过。"""
    choices = []
    for path in reversed(list_run_records()):   # list 升序≈时间序，reversed=最新在前
        name = os.path.basename(path)
        try:
            rec = load_run_record(path)
        except Exception:
            continue
        choices.append((_history_label(rec, name), path))
        if len(choices) >= _HISTORY_LIST_LIMIT:
            break
    return choices


def refresh_history_choices(output_language):
    """刷新历史下拉选项（保留无选中态）。手动刷新按钮用。"""
    return gr.update(choices=_history_choices())


def after_backtest_history(output_language):
    """回测完成后：刷新历史下拉并【取消选中】（避免下拉仍指向上次浏览的历史项、与主仪表盘
    展示的新结果不符）+ 清空历史详情。不动主仪表盘——新结果已由 run_backtest_from_ui 渲入；
    取消选中触发的 on_select_history(None) 对主仪表盘是 gr.update() 空操作，不会覆盖新结果。"""
    return gr.update(choices=_history_choices(), value=None), ""


def on_select_history(file_path, output_language, current_chart, current_robustness_chart):
    """选中一条历史记录 → 在【主仪表盘】重渲该次回测 + 下方独立展示当时的提示词/参数/代码/
    摘要 + 重渲当时的稳健性报告 + 把该次的回测图/稳健性图路径写入对应 State（两个「查看图表」
    按钮即开这次的图）。

    返回 (主仪表盘HTML, 详情HTML, 回测图路径, 稳健性报告HTML, 稳健性图路径)。无选中（下拉被
    清空/刷新重置）时：主仪表盘与稳健性报告用 gr.update() 空操作（不覆盖当前回测结果），只清空
    详情；两个图表路径【原样回传 current_*】——不对 gr.State 用 gr.update() 占位（明确无歧义）。"""
    if not file_path:
        return gr.update(), "", current_chart, gr.update(), current_robustness_chart
    try:
        rec = load_run_record(file_path)
    except Exception as e:
        return _result_error_html(str(e)), "", current_chart, gr.update(), current_robustness_chart

    summary_text = get_summary_text(output_language)
    metrics = rec.get("metrics") or {}
    trades = rec.get("trades") or []
    params = rec.get("params") or {}
    meta = {
        "symbol": params.get("symbol", ""),
        "timeframe": params.get("timeframe", ""),
        "start": params.get("actual_start"),
        "end": params.get("actual_end"),
        "kline_count": params.get("kline_count", ""),
        "initial_cash": params.get("initial_cash", 0.0),
        "equity": rec.get("equity") or [],
    }
    dashboard = build_dashboard_html(metrics, trades, meta, summary_text, output_language)
    detail = build_history_detail_html(rec, output_language)
    robustness_html = _robustness_html(rec.get("robustness") or "", output_language)
    return (dashboard, detail, rec.get("chart_file"),
            robustness_html, rec.get("robustness_chart"))


# IS/OOS 对比表展示的指标 → SUMMARY_TEXTS 标签键（指标名复用回测摘要文案，不重复翻译）
_ROBUSTNESS_METRIC_LABELS = (
    ("total_return_pct", "total_return"),
    ("annual_return_pct", "annual_return"),
    ("max_drawdown_pct", "max_drawdown"),
    ("sharpe_ratio", "sharpe_ratio"),
    ("profit_factor", "profit_factor"),
    ("net_win_rate", "net_win_rate"),
)


def build_robustness_summary(report: dict, lang_code: str, chart_path) -> str:
    """把 run_full_analysis 报告渲染成纯文本（原始 IS/OOS 对比 + 红线 + WFO 聚合 +
    最优参数格）。指标名复用 get_summary_text，结构标签用 get_robustness_text。"""
    rt = get_robustness_text(lang_code)
    st = get_summary_text(lang_code)
    na = st["na"]

    if not report.get("available"):
        return f"{rt['unavailable']}：{report.get('reason', '')}"

    meta = report["meta"]
    lines = [
        f"【{rt['title']}】{meta['display_symbol']} · {meta['timeframe']} · "
        f"{meta['actual_start']} ~ {meta['actual_end']} ({meta['kline_count']})",
    ]

    # ---- 样本内 / 样本外原始对比 + 红线 ----
    io = report["in_out"]
    deg = io.get("degradation", {})
    lines.append("")
    lines.append(f"— {rt['io_section']} (IS {io['split_ratio']:.0%}) · {rt['boundary']} {io['boundary_time']} —")
    if deg.get("available"):
        header = f"{'':<14}{rt['col_in']:>12}{rt['col_out']:>12}{rt['col_ratio']:>10}"
        lines.append(header)
        per = deg["metrics"]
        for key, label_key in _ROBUSTNESS_METRIC_LABELS:
            cell = per.get(key, {})
            label = st.get(label_key, key)
            iv = format_number(cell.get("in"), 2, na)
            ov = format_number(cell.get("out"), 2, na)
            rv = format_number(cell.get("out_over_in"), 2, na)
            lines.append(f"{label:<14}{iv:>12}{ov:>12}{rv:>10}")
        flags = deg.get("overfit_flags") or []
        lines.append("")
        lines.append(f"{rt['flags_title']}：")
        if flags:
            lines.extend(f"  · {f}" for f in flags)
        else:
            lines.append(f"  · {rt['no_flags']}")
    else:
        lines.append(deg.get("reason", na))

    # ---- Walk-Forward ----
    wfo = report["walk_forward"]
    agg = wfo.get("aggregate", {})
    lines.append("")
    mode_desc = rt["wfo_real"] if wfo.get("optimized") else rt["wfo_stability"]
    lines.append(f"— {rt['wfo_section']} · {mode_desc} —")
    pos_ratio = agg.get("positive_window_ratio")
    pos_txt = f"{pos_ratio:.0%}" if isinstance(pos_ratio, (int, float)) else na
    lines.append(f"{rt['wfo_windows']}：{agg.get('total_windows', 0)}   "
                 f"{rt['wfo_valid']}：{agg.get('valid_windows', 0)}   "
                 f"{rt['wfo_pos_ratio']}：{pos_txt}")
    lines.append(f"{rt['wfo_mean_ret']}：{format_number(agg.get('mean_out_return'), 2, na)}   "
                 f"{rt['wfo_std_ret']}：{format_number(agg.get('std_out_return'), 2, na)}")
    lines.append(f"{rt['wfo_mean_sharpe']}：{format_number(agg.get('mean_out_sharpe'), 2, na)}   "
                 f"{rt['wfo_worst_dd']}：{format_number(agg.get('worst_window_drawdown'), 2, na)}")
    if wfo.get("optimized"):
        chosen = [f"{w['index']}→{w['train'].get('chosen_params')}" for w in wfo.get("windows", [])]
        if chosen:
            lines.append(f"{rt['wfo_chosen']}：" + ", ".join(chosen))

    # ---- 参数热力图最优格 ----
    scan = report.get("param_scan")
    if scan:
        lines.append("")
        lines.append(f"— {rt['scan_section']} ({scan['metric']}) —")
        best = _best_scan_cell(scan)
        if best:
            lines.append(f"{rt['scan_best']}：{best['params']} → {format_number(best['value'], 4, na)}")
        lines.append(rt["chart_note"])

    lines.append("")
    lines.append(f"{st['chart_file']}：")
    lines.append(str(chart_path) if chart_path else na)
    return "\n".join(lines)


def _best_scan_cell(scan: dict):
    """扫描矩阵里 metric 最大的格（None 不参选）。无有效格 ⇒ None。"""
    best = None
    for cell in scan.get("cells", []):
        m = (cell.get("metrics") or {}).get(scan["metric"])
        if not isinstance(m, (int, float)):
            continue
        if best is None or m > best["value"]:
            best = {"params": cell.get("params"), "value": m}
    return best


# =========================================================
# 工作台 UI 静态构件
# =========================================================

STUDIO_HEADERS = {
    "zh": (
        "QTBS AI 量化策略前端",
        "用自然语言生成策略代码并运行历史回测",
    ),
    "en": (
        "QTBS AI Quant Strategy Frontend",
        "Generate executable strategy code from natural language and run historical backtests.",
    ),
    "ko": (
        "QTBS AI 퀀트 전략 프론트엔드",
        "자연어로 전략 코드를 생성하고 과거 데이터 백테스트를 실행합니다.",
    ),
    "ja": (
        "QTBS AI 量的戦略フロントエンド",
        "自然言語から戦略コードを生成し、履歴データでバックテストします。",
    ),
    "ar": (
        "واجهة QTBS AI للاستراتيجيات الكمية",
        "أنشئ كود الاستراتيجية من اللغة الطبيعية وشغّل الاختبار الخلفي التاريخي.",
    ),
    "ru": (
        "QTBS AI фронтенд количественных стратегий",
        "Создавайте код стратегии на естественном языке и запускайте исторический бэктест.",
    ),
}


def build_studio_header_markdown(lang_code: str) -> str:
    title, subtitle = STUDIO_HEADERS.get(lang_code, STUDIO_HEADERS["zh"])
    return f"""
<div class="studio-kicker">QTBS AI Studio</div>
<h1>{html.escape(title)}</h1>
<p>{html.escape(subtitle)}</p>
"""


def build_sidebar_html() -> str:
    return """
    <aside class="sidebar-shell">
        <div>
            <div class="sidebar-brand">QTBS AI Studio</div>
            <nav class="sidebar-nav" aria-label="QTBS workspace navigation">
                <div class="sidebar-nav-item active"><span></span>策略工作台</div>
                <div class="sidebar-nav-item"><span></span>数据任务</div>
                <div class="sidebar-nav-item"><span></span>回测报告</div>
                <div class="sidebar-nav-item"><span></span>代码审查</div>
            </nav>
        </div>
        <div class="sidebar-foot">自然语言策略生成与回测平台</div>
    </aside>
    """


def format_status_cash(value) -> str:
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
    except Exception:
        number = 1000.0

    if number.is_integer():
        formatted = f"{number:,.0f}"
    else:
        formatted = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{formatted} USDT"


def build_status_cards_html(
    lang_code: str = "zh",
    market: str = "crypto",
    timeframe: str = "4h",
    initial_cash=1000,
) -> str:
    text = get_ui_text(lang_code)
    market_display = text["market_choice"] if market == "crypto" else str(market or "-")
    timeframe_display = str(timeframe or "4h")
    cash_display = format_status_cash(initial_cash)

    return f"""
    <div class="top-status-grid">
        <div class="top-status-item">
            <span>{html.escape(text["market_label"])}</span>
            <strong>{html.escape(market_display)}</strong>
        </div>
        <div class="top-status-item">
            <span>{html.escape(text["timeframe_label"])}</span>
            <strong>{html.escape(timeframe_display)}</strong>
        </div>
        <div class="top-status-item">
            <span>{html.escape(text["initial_cash_label"])}</span>
            <strong>{html.escape(cash_display)}</strong>
        </div>
    </div>
    """


def update_status_cards(
    lang_code: str,
    market: str,
    timeframe: str,
    initial_cash,
) -> str:
    return build_status_cards_html(lang_code, market, timeframe, initial_cash)


def build_section_header_html(title: str, subtitle: str | None = None) -> str:
    subtitle_html = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    return f"""
    <div class="section-heading">
        <h2>{html.escape(title)}</h2>
        {subtitle_html}
    </div>
    """


# =========================================================
# 页面 CSS
# =========================================================

custom_css = """
:root {
    --qtbs-bg: #f4f6f8;
    --qtbs-panel: #ffffff;
    --qtbs-line: #d9dee7;
    --qtbs-muted: #667085;
    --qtbs-text: #172033;
    --qtbs-blue: #2563eb;
    --qtbs-orange: #f97316;
    --qtbs-dark: #171a22;
    --qtbs-ui-scale: 1;
    --qtbs-screen-px: 1px;
    --qtbs-screen-px: clamp(0.96px, 0.052vw, 1.16px);
    --qtbs-radius: clamp(7px, calc(var(--qtbs-screen-px) * 8), 10px);
    --qtbs-font-xxs: clamp(10px, calc(var(--qtbs-screen-px) * 11), 13px);
    --qtbs-font-xs: clamp(11px, calc(var(--qtbs-screen-px) * 12), 14px);
    --qtbs-font-sm: clamp(12px, calc(var(--qtbs-screen-px) * 13), 15px);
    --qtbs-font-md: clamp(13px, calc(var(--qtbs-screen-px) * 14), 16px);
    --qtbs-font-lg: clamp(15px, calc(var(--qtbs-screen-px) * 18), 21px);
    --qtbs-font-xl: clamp(22px, calc(var(--qtbs-screen-px) * 30), 36px);
    --qtbs-page-pad: clamp(8px, calc(var(--qtbs-screen-px) * 18), 26px);
    --qtbs-gap: clamp(8px, calc(var(--qtbs-screen-px) * 14), 20px);
    --qtbs-panel-pad: clamp(10px, calc(var(--qtbs-screen-px) * 16), 24px);
    --qtbs-sidebar-width: clamp(180px, calc(var(--qtbs-screen-px) * 208), 250px);
    --qtbs-content-max: min(2440px, calc(100vw - var(--qtbs-page-pad) * 2));
}

body,
.gradio-container {
    background: var(--qtbs-bg) !important;
    color: var(--qtbs-text) !important;
    font-size: var(--qtbs-font-md) !important;
}

body {
    margin: 0 !important;
    overflow-x: hidden;
}

.gradio-container {
    max-width: none !important;
    width: 100% !important;
}

body > gradio-app,
gradio-app,
.gradio-container,
.gradio-container > .main,
.gradio-container .main,
.gradio-container .contain,
.gradio-container .wrap {
    max-width: none !important;
}

.gradio-container > .main,
.gradio-container .main,
.gradio-container .contain {
    width: 100% !important;
}

#app-shell {
    width: var(--qtbs-content-max) !important;
    max-width: var(--qtbs-content-max) !important;
    margin: 0 auto !important;
    padding: var(--qtbs-page-pad) 0 calc(var(--qtbs-page-pad) + 4px);
    gap: var(--qtbs-gap) !important;
    align-items: stretch;
    box-sizing: border-box;
    zoom: var(--qtbs-ui-scale);
}

#sidebar-col {
    flex: 0 0 var(--qtbs-sidebar-width) !important;
    max-width: var(--qtbs-sidebar-width) !important;
    min-width: 160px !important;
}

#studio-sidebar {
    height: 100%;
}

#studio-sidebar,
#studio-sidebar .html-container,
#studio-sidebar .prose {
    overflow: visible !important;
}

.sidebar-shell {
    min-height: calc(100vh - var(--qtbs-page-pad) * 2);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: var(--qtbs-panel-pad);
    border: 1px solid var(--qtbs-line);
    border-radius: 8px;
    background: #ffffff;
}

.sidebar-brand {
    padding: 8px 8px 18px;
    font-size: 17px;
    font-weight: 800;
    letter-spacing: 0;
    color: #111827;
}

.sidebar-nav {
    display: grid;
    gap: 6px;
}

.sidebar-nav-item {
    display: flex;
    align-items: center;
    gap: 9px;
    min-height: 38px;
    padding: 0 10px;
    border-radius: 8px;
    color: #4b5563;
    font-size: 14px;
    font-weight: 600;
}

.sidebar-nav-item span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #cbd5e1;
}

.sidebar-nav-item.active {
    background: #fff7ed;
    color: #c2410c;
}

.sidebar-nav-item.active span {
    background: var(--qtbs-orange);
}

.sidebar-foot {
    padding: 12px 8px 4px;
    border-top: 1px solid #e5e7eb;
    color: var(--qtbs-muted);
    font-size: 12px;
    line-height: 1.55;
}

#main-container {
    min-width: 0;
    max-width: none !important;
    width: 100% !important;
}

.studio-panel {
    background: var(--qtbs-panel);
    border: 1px solid var(--qtbs-line);
    border-radius: 8px;
    padding: var(--qtbs-panel-pad) !important;
}

#top-bar {
    align-items: stretch;
    gap: var(--qtbs-gap) !important;
    margin-bottom: var(--qtbs-gap);
}

#header-panel,
#top-right-panel {
    background: var(--qtbs-panel);
    border: 1px solid var(--qtbs-line);
    border-radius: 8px;
    padding: var(--qtbs-panel-pad) !important;
}

#studio-header-md,
#studio-header-md .prose {
    margin: 0 !important;
}

#studio-header-md .studio-kicker {
    margin-bottom: 7px;
    color: var(--qtbs-blue);
    font-size: 12px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0;
}

#studio-header-md h1 {
    margin: 0 !important;
    color: #111827;
    font-size: clamp(22px, 1.8vw, 32px) !important;
    line-height: 1.2 !important;
    letter-spacing: 0 !important;
}

#studio-header-md p {
    margin: 8px 0 0 !important;
    color: var(--qtbs-muted);
    font-size: 14px !important;
    line-height: 1.55 !important;
}

#top-right-panel {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: var(--qtbs-gap);
}

#language-select label {
    color: #344054 !important;
    font-size: 12px !important;
    font-weight: 700 !important;
}

#language-select .wrap,
#right-panel .wrap,
#strategy-panel .wrap,
#output-stage .wrap {
    border-radius: 8px !important;
}

.top-status-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));
    gap: 8px;
}

.top-status-item {
    min-width: 0;
    padding: 10px;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background: #f8fafc;
}

.top-status-item span {
    display: block;
    color: #667085;
    font-size: 11px;
    line-height: 1.3;
}

.top-status-item strong {
    display: block;
    margin-top: 4px;
    overflow: hidden;
    color: #111827;
    font-size: 14px;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
}

#data-status-bar {
    align-items: stretch;
    gap: var(--qtbs-gap) !important;
    margin: var(--qtbs-gap) 0 4px;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: clamp(8px, calc(var(--qtbs-screen-px) * 10), 14px) !important;
}

#fetch-progress-col,
#fetch-action-col {
    justify-content: center;
}

#fetch-progress-wrap,
#fetch-progress-wrap .html-container,
#fetch-progress-wrap .prose,
#param-priority-wrap,
#param-priority-wrap .html-container,
#param-priority-wrap .prose,
#review-output,
#review-output .html-container,
#review-output .prose {
    overflow: visible !important;
}

#fetch-progress {
    margin: 0;
    color: #4b5563;
    font-size: 12px;
}

.fetch-head {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 8px;
}

.fetch-status {
    color: #344054;
    font-weight: 700;
}

#fetch-progress.fetch-active .fetch-status {
    color: var(--qtbs-blue);
}

.fetch-total-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.fetch-total-track {
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background: #e8eef8;
    overflow: hidden;
}

.fetch-total-bar {
    height: 100%;
    border-radius: 4px;
    background: var(--qtbs-blue);
    transition: width 0.4s ease;
}

.fetch-total-num {
    min-width: 48px;
    color: #667085;
    font-variant-numeric: tabular-nums;
    text-align: right;
}

.fetch-current-label {
    margin-top: 7px;
    color: #667085;
}

.fetch-pulse-track {
    position: relative;
    height: 5px;
    margin-top: 4px;
    border-radius: 3px;
    background: #e8eef8;
    overflow: hidden;
}

.fetch-pulse-bar {
    position: absolute;
    height: 100%;
    width: 36%;
    border-radius: 3px;
    background: var(--qtbs-blue);
    animation: fetch-pulse 1.1s ease-in-out infinite;
}

@keyframes fetch-pulse {
    0% { left: -36%; }
    100% { left: 100%; }
}

#fetch-update-button {
    width: 100%;
    min-height: 38px !important;
    border-radius: 8px !important;
    border-color: #bfd3ff !important;
    background: #eef4ff !important;
    color: #1d4ed8 !important;
    font-size: 13px !important;
    font-weight: 700 !important;
}

.qtbs-tooltip {
    position: relative;
    display: inline-flex;
    flex-shrink: 0;
}

.qtbs-tooltip-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    border: 1px solid #aab3c2;
    border-radius: 50%;
    color: #667085;
    cursor: help;
    font-size: 10px;
    font-weight: 800;
    user-select: none;
}

.qtbs-tooltip .qtbs-tooltip-text {
    visibility: hidden;
    opacity: 0;
    position: absolute;
    top: 132%;
    right: -8px;
    z-index: 1000;
    width: 360px;
    max-width: 72vw;
    padding: 10px 12px;
    border-radius: 8px;
    background: rgba(23, 26, 34, 0.96);
    color: #f7f8fb;
    font-size: 12px;
    line-height: 1.6;
    white-space: normal;
    text-align: start;
    box-shadow: 0 10px 24px rgba(15, 23, 42, 0.24);
    transition: opacity 0.15s ease;
    pointer-events: none;
}

.qtbs-tooltip:hover .qtbs-tooltip-text {
    visibility: visible;
    opacity: 1;
}

#strategy-workspace {
    align-items: stretch;
    gap: var(--qtbs-gap) !important;
    margin-bottom: var(--qtbs-gap);
}

.section-heading {
    margin-bottom: 12px;
}

.section-heading h2 {
    margin: 0 !important;
    color: #111827;
    font-size: 18px !important;
    line-height: 1.25 !important;
    letter-spacing: 0 !important;
}

.section-heading p {
    margin: 4px 0 0 !important;
    color: #667085;
    font-size: 12px !important;
    line-height: 1.5 !important;
}

#strategy-box textarea {
    min-height: clamp(300px, 42vh, 520px) !important;
    border: 1px solid #cfd7e3 !important;
    border-radius: 8px !important;
    background: #fbfcfe !important;
    color: #111827 !important;
    font-size: 15px !important;
    line-height: 1.65 !important;
    box-shadow: none !important;
    outline: none !important;
}

#strategy-box textarea:focus {
    border-color: #93b4ff !important;
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.14) !important;
}

#review-block-title {
    margin-top: 14px;
}

#review-output {
    width: 100%;
}

.review-card {
    min-height: 150px;
    padding: 16px;
    border: 1px solid #2a3040;
    border-radius: 8px;
    background: var(--qtbs-dark);
    color: #f4f6fb;
}

.review-layout {
    display: grid;
    grid-template-columns: 6fr 4fr;
    gap: 16px;
    align-items: start;
}

.review-left {
    min-width: 0;
}

.review-right {
    min-width: 0;
    padding-left: 16px;
    border-left: 1px solid #303747;
}

.review-title {
    margin-bottom: 12px;
    color: #f8fafc;
    font-size: 14px;
    font-weight: 800;
}

.score-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.score-bar-bg {
    flex: 1;
    height: 10px;
    border-radius: 8px;
    background: #303747;
    overflow: hidden;
}

.score-bar-fill {
    height: 100%;
    border-radius: 8px;
    background: #22c55e;
}

.score-number {
    width: 70px;
    color: #f8fafc;
    font-weight: 800;
    text-align: right;
}

.review-behavior {
    display: flex;
    align-items: flex-start;
    justify-content: flex-end;
    gap: 6px;
    margin: 10px 0 0;
    font-size: 12.5px;
}

.review-behavior.behavior-pass,
.review-behavior.behavior-pass > span {
    color: #4ade80 !important;
}

.review-behavior.behavior-fail,
.review-behavior.behavior-fail > span {
    color: #fb7185 !important;
    font-weight: 700;
}

.review-summary-title {
    margin-bottom: 8px;
    color: #e5e7eb;
    font-size: 13px;
    font-weight: 800;
}

.review-summary {
    margin-bottom: 8px;
    color: #cbd5e1;
    font-size: 12px;
    line-height: 1.65;
}

.review-note {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #303747;
    color: #aab3c2;
    font-size: 12px;
    line-height: 1.6;
}

.review-empty {
    color: #aab3c2;
    font-size: 13px;
}

#right-panel {
    min-width: 0;
}

#param-priority-note {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0 0 12px;
    padding: 10px 12px;
    border: 1px solid #e0e7ff;
    border-radius: 8px;
    background: #f5f8ff;
    color: #475467;
    font-size: 12px;
    line-height: 1.45;
}

#param-priority-note > span:first-child {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.param-row {
    gap: 10px !important;
    margin-bottom: 2px !important;
}

#right-panel label,
#strategy-panel label,
#output-stage label {
    color: #344054 !important;
    font-size: 12px !important;
    font-weight: 700 !important;
}

#right-panel input,
#right-panel textarea {
    color: #111827 !important;
    font-size: 13px !important;
}

#right-panel .wrap {
    min-height: 38px !important;
}

#button-row {
    gap: 10px !important;
    margin-top: 12px !important;
}

#generate-button,
#backtest-button {
    min-height: 44px !important;
    border-radius: 8px !important;
    font-size: 15px !important;
    font-weight: 800 !important;
}

#generate-button {
    border-color: var(--qtbs-orange) !important;
    background: var(--qtbs-orange) !important;
    color: #ffffff !important;
}

#backtest-button {
    border-color: #d0d5dd !important;
    background: #f3f4f6 !important;
    color: #1f2937 !important;
}

#output-stage {
    margin-bottom: 4px;
}

#output-grid {
    gap: var(--qtbs-gap) !important;
    align-items: stretch;
}

#output-code textarea,
#result-box textarea {
    border-radius: 8px !important;
    font-size: 13px !important;
    line-height: 1.55 !important;
}

#result-box textarea {
    min-height: clamp(260px, 36vh, 460px) !important;
}

#chart-file-output {
    margin-top: 10px;
}

button,
input,
textarea,
.wrap {
    letter-spacing: 0 !important;
}

/* Width-led responsive sizing. Keep the workspace using the available
   monitor width, while spacing and typography scale gently with it. */
#header-panel {
    min-width: min(calc(var(--qtbs-screen-px) * 320), 100%) !important;
}

#top-right-panel {
    min-width: min(calc(var(--qtbs-screen-px) * 300), 100%) !important;
}

#strategy-panel {
    min-width: min(calc(var(--qtbs-screen-px) * 460), 100%) !important;
}

#right-panel {
    min-width: min(calc(var(--qtbs-screen-px) * 330), 100%) !important;
}

.sidebar-brand {
    padding: calc(var(--qtbs-screen-px) * 8) calc(var(--qtbs-screen-px) * 8) calc(var(--qtbs-screen-px) * 18);
    font-size: var(--qtbs-font-lg);
}

.sidebar-nav {
    gap: clamp(5px, calc(var(--qtbs-screen-px) * 6), 8px);
}

.sidebar-nav-item {
    gap: clamp(7px, calc(var(--qtbs-screen-px) * 9), 12px);
    min-height: clamp(34px, calc(var(--qtbs-screen-px) * 38), 46px);
    padding: 0 clamp(8px, calc(var(--qtbs-screen-px) * 10), 14px);
    border-radius: var(--qtbs-radius);
    font-size: var(--qtbs-font-md);
}

.sidebar-nav-item span {
    width: clamp(7px, calc(var(--qtbs-screen-px) * 8), 10px);
    height: clamp(7px, calc(var(--qtbs-screen-px) * 8), 10px);
}

.sidebar-foot,
#fetch-progress,
.section-heading p,
.review-summary,
.review-note,
#param-priority-note,
#right-panel label,
#strategy-panel label,
#output-stage label {
    font-size: var(--qtbs-font-xs) !important;
}

#studio-header-md .studio-kicker,
#language-select label,
.top-status-item span {
    font-size: var(--qtbs-font-xs) !important;
}

#studio-header-md h1 {
    font-size: var(--qtbs-font-xl) !important;
}

#studio-header-md p,
.top-status-item strong,
.review-title,
#right-panel input,
#right-panel textarea,
#output-code textarea,
#result-box textarea {
    font-size: var(--qtbs-font-md) !important;
}

.section-heading h2 {
    font-size: var(--qtbs-font-lg) !important;
}

.top-status-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: clamp(6px, calc(var(--qtbs-screen-px) * 8), 12px);
}

.top-status-item {
    padding: clamp(8px, calc(var(--qtbs-screen-px) * 10), 14px);
    border-radius: var(--qtbs-radius);
}

#strategy-box textarea {
    min-height: clamp(calc(var(--qtbs-screen-px) * 300), 42vh, calc(var(--qtbs-screen-px) * 560)) !important;
    font-size: var(--qtbs-font-md) !important;
}

.review-card {
    min-height: clamp(132px, calc(var(--qtbs-screen-px) * 150), 180px);
    padding: clamp(12px, calc(var(--qtbs-screen-px) * 16), 22px);
}

.review-layout {
    gap: clamp(12px, calc(var(--qtbs-screen-px) * 16), 22px);
}

.review-right {
    padding-left: clamp(12px, calc(var(--qtbs-screen-px) * 16), 22px);
}

.param-row,
#button-row {
    gap: clamp(8px, calc(var(--qtbs-screen-px) * 10), 14px) !important;
}

#fetch-update-button {
    min-height: clamp(34px, calc(var(--qtbs-screen-px) * 38), 46px) !important;
    font-size: var(--qtbs-font-sm) !important;
}

#generate-button,
#backtest-button {
    min-height: clamp(40px, calc(var(--qtbs-screen-px) * 44), 54px) !important;
    font-size: var(--qtbs-font-md) !important;
}

#result-box textarea {
    min-height: clamp(calc(var(--qtbs-screen-px) * 260), 36vh, calc(var(--qtbs-screen-px) * 500)) !important;
}

@media (max-height: 760px) and (min-width: 1101px) {
    #strategy-box textarea {
        min-height: clamp(calc(var(--qtbs-screen-px) * 230), 34vh, calc(var(--qtbs-screen-px) * 390)) !important;
    }

    #result-box textarea {
        min-height: clamp(calc(var(--qtbs-screen-px) * 210), 30vh, calc(var(--qtbs-screen-px) * 360)) !important;
    }
}

@media (max-width: 1280px) {
    :root {
        --qtbs-sidebar-width: clamp(160px, calc(var(--qtbs-screen-px) * 184), 220px);
    }

    #strategy-workspace {
        align-items: flex-start;
    }
}

@media (max-width: 1100px) {
    #app-shell {
        width: calc(100vw - 20px);
        max-width: calc(100vw - 20px);
        flex-direction: column !important;
    }

    #sidebar-col {
        flex: 1 1 auto !important;
        max-width: none !important;
        min-width: 0 !important;
    }

    .sidebar-shell {
        min-height: auto;
        flex-direction: row;
        gap: var(--qtbs-gap);
        align-items: center;
    }

    .sidebar-nav {
        grid-template-columns: repeat(4, max-content);
        overflow-x: auto;
    }

    .sidebar-foot {
        max-width: 220px;
        border-top: 0;
        border-left: 1px solid #e5e7eb;
        padding: 4px 8px 4px 14px;
    }
}

@media (max-width: 760px) {
    #app-shell {
        width: calc(100vw - 12px);
        max-width: calc(100vw - 12px);
        padding-top: 6px;
    }

    #top-bar,
    #strategy-workspace,
    #output-grid,
    #data-status-bar {
        flex-direction: column !important;
    }

    #header-panel,
    #top-right-panel,
    .studio-panel,
    #data-status-bar {
        padding: 12px !important;
    }

    #studio-header-md h1 {
        font-size: clamp(21px, 6vw, 25px) !important;
    }

    .top-status-grid {
        grid-template-columns: 1fr;
    }

    .sidebar-shell {
        display: block;
    }

    .sidebar-nav {
        grid-template-columns: 1fr 1fr;
    }

    .sidebar-foot {
        max-width: none;
        margin-top: 10px;
        border-left: 0;
        border-top: 1px solid #e5e7eb;
        padding: 10px 8px 2px;
    }

    .review-layout {
        grid-template-columns: 1fr;
    }

    .review-right {
        padding-left: 0;
        padding-top: 12px;
        border-left: 0;
        border-top: 1px solid #303747;
    }

    #strategy-box textarea {
        min-height: 300px !important;
    }

    .param-row {
        flex-direction: column !important;
    }
}
"""


# =========================================================
# Gradio 前端
# =========================================================

custom_head = """
<script>
(() => {
    const DESIGN_WIDTH = 2200;
    const DESIGN_HEIGHT = 1500;
    const VIEWPORT_PAD = 32;
    const MIN_SCALE = 0.70;
    const MAX_SCALE = 1.00;

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function applyQtbsViewportScale() {
        const root = document.documentElement;
        const viewportWidth = window.innerWidth || root.clientWidth || DESIGN_WIDTH;
        const viewportHeight = window.innerHeight || root.clientHeight || DESIGN_HEIGHT;
        const availableWidth = Math.max(360, viewportWidth - VIEWPORT_PAD);
        const availableHeight = Math.max(360, viewportHeight - VIEWPORT_PAD);
        const widthScale = availableWidth / DESIGN_WIDTH;
        const heightScale = availableHeight / DESIGN_HEIGHT;
        const scale = clamp(Math.min(widthScale, heightScale), MIN_SCALE, MAX_SCALE);
        const contentWidth = Math.min(DESIGN_WIDTH, availableWidth / scale);

        root.style.setProperty("--qtbs-ui-scale", scale.toFixed(4));
        root.style.setProperty("--qtbs-content-max", `${contentWidth.toFixed(1)}px`);
        root.dataset.qtbsUiScale = scale.toFixed(4);
    }

    let resizeFrame = 0;
    function scheduleScale() {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(applyQtbsViewportScale);
    }

    window.addEventListener("resize", scheduleScale, { passive: true });
    window.addEventListener("orientationchange", scheduleScale, { passive: true });
    document.addEventListener("DOMContentLoaded", applyQtbsViewportScale);
    applyQtbsViewportScale();
    window.setTimeout(applyQtbsViewportScale, 80);
    window.setTimeout(applyQtbsViewportScale, 400);
    window.setTimeout(applyQtbsViewportScale, 1200);
})();
</script>
"""

default_lang = "zh"
default_text = get_ui_text(default_lang)

with gr.Blocks(
    # Gradio 6 起 css/head 不再是 Blocks 构造参数（移到 launch()）：
    # 传给 Blocks 会被静默丢弃（demo.css=None），整套 studio 样式不生效。
    # 见文件底部 demo.launch(css=..., head=...)。
    title="QTBS AI Quant Strategy Frontend",
) as demo:

    with gr.Row(elem_id="app-shell"):

        with gr.Column(scale=1, min_width=160, elem_id="sidebar-col"):
            gr.HTML(build_sidebar_html(), elem_id="studio-sidebar")

        with gr.Column(scale=12, elem_id="main-container"):

            with gr.Row(elem_id="top-bar", equal_height=True):

                with gr.Column(scale=7, min_width=320, elem_id="header-panel"):
                    header_md = gr.HTML(
                        build_studio_header_markdown(default_lang),
                        elem_id="studio-header-md",
                    )

                with gr.Column(scale=5, min_width=300, elem_id="top-right-panel"):
                    language_select = gr.Dropdown(
                        label=default_text["language_label"],
                        choices=LANGUAGE_CHOICES,
                        value=default_lang,
                        interactive=True,
                        elem_id="language-select",
                    )
                    status_strip = gr.HTML(
                        build_status_cards_html(
                            default_lang,
                            "crypto",
                            "4h",
                            1000,
                        ),
                        elem_id="status-strip",
                    )

            with gr.Row(elem_id="strategy-workspace", equal_height=True):
                with gr.Column(
                    scale=7,
                    min_width=460,
                    elem_id="strategy-panel",
                    elem_classes=["studio-panel"],
                ):
                    gr.HTML(
                        build_section_header_html(
                            "策略输入",
                            "自然语言到可执行策略代码",
                        )
                    )

                    strategy_input = gr.Textbox(
                        label=default_text["strategy_label"],
                        placeholder=default_text["strategy_placeholder"],
                        lines=18,
                        elem_id="strategy-box",
                    )

                    gr.HTML(
                        build_section_header_html(
                            "代码审查",
                            "AI 匹配度和行为检查",
                        ),
                        elem_id="review-block-title",
                    )

                    review_output = gr.HTML(
                        value=build_review_html(None, default_lang),
                        elem_id="review-output",
                    )

                with gr.Column(
                    scale=4,
                    min_width=330,
                    elem_id="right-panel",
                    elem_classes=["studio-panel"],
                ):
                    gr.HTML(
                        build_section_header_html(
                            "回测控制台",
                            "市场、时间窗口、资金与交易成本",
                        )
                    )

                    param_priority_note = gr.HTML(
                        build_param_priority_html(default_text),
                        elem_id="param-priority-wrap",
                    )

                    market_select = gr.Dropdown(
                        label=default_text["market_label"],
                        choices=[(default_text["market_choice"], "crypto")],
                        value="crypto",
                        interactive=False,
                    )

                    with gr.Row(elem_classes=["param-row"]):
                        symbol_input = gr.Textbox(
                            label=default_text["symbol_label"],
                            value="BTC",
                            placeholder=default_text["symbol_placeholder"],
                            scale=1,
                        )

                        timeframe_select = gr.Dropdown(
                            label=default_text["timeframe_label"],
                            choices=["1m", "5m", "15m", "1h", "4h", "1d"],
                            value="4h",
                            interactive=True,
                            scale=1,
                        )

                    with gr.Row(elem_classes=["param-row"]):
                        start_date = gr.DateTime(
                            label=default_text["start_label"],
                            value="2017-01-01",
                            include_time=False,
                            type="string",
                            timezone="UTC",
                            scale=1,
                        )

                        end_date = gr.DateTime(
                            label=default_text["end_label"],
                            value=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            include_time=False,
                            type="string",
                            timezone="UTC",
                            scale=1,
                        )

                    with gr.Row(elem_classes=["param-row"]):
                        initial_cash_input = gr.Number(
                            label=default_text["initial_cash_label"],
                            value=1000,
                            precision=2,
                            minimum=0.01,
                            step=100,
                            scale=1,
                        )

                        leverage_input = gr.Number(
                            label=default_text["leverage_label"],
                            value=1,
                            precision=0,
                            minimum=0,
                            maximum=200,
                            step=1,
                            scale=1,
                        )

                    with gr.Row(elem_classes=["param-row"]):
                        position_size_input = gr.Number(
                            label=default_text["position_size_label"],
                            value=100,
                            precision=2,
                            minimum=0,
                            maximum=100,
                            step=1,
                            scale=1,
                        )

                        fee_rate_input = gr.Number(
                            label=default_text["fee_rate_label"],
                            value=0.05,
                            precision=4,
                            minimum=0,
                            step=0.01,
                            scale=1,
                        )

                    slippage_input = gr.Number(
                        label=default_text["slippage_label"],
                        value=0,
                        precision=4,
                        minimum=0,
                        step=0.01,
                    )

                    with gr.Row(elem_id="button-row"):
                        generate_button = gr.Button(
                            value=default_text["generate_button"],
                            variant="primary",
                            elem_id="generate-button",
                            scale=1,
                        )

                        backtest_button = gr.Button(
                            value=default_text["backtest_button"],
                            variant="secondary",
                            elem_id="backtest-button",
                            scale=1,
                        )

            with gr.Column(elem_id="output-stage", elem_classes=["studio-panel"]):
                gr.HTML(
                    build_section_header_html(
                        "输出台",
                        "策略代码、回测摘要与图表文件",
                    )
                )

                # 纵向堆叠：代码折叠面板【全宽】置顶（折叠后仅占标题条），把整片宽度
                # 留给回测结果可视化（用户反馈：代码即便折叠也占了输出台太多空间）
                with gr.Column(elem_id="output-grid"):
                    with gr.Accordion(
                        default_text["code_output_label"],
                        open=False,
                    ) as code_accordion:
                        strategy_code_output = gr.Code(
                            label="",
                            language="python",
                            lines=20,
                            elem_id="output-code",
                        )

                    # 查看历史：下拉切换历次回测，选中即在下方【同一个主仪表盘】重渲（与当前
                    # 输出整合，不再单开一块重复仪表盘）；当时的提示词/参数/代码/摘要在仪表盘
                    # 下方独立展示。数据源 Past_data/runs/*.json。
                    with gr.Row(elem_classes=["param-row"], elem_id="history-bar"):
                        history_select = gr.Dropdown(
                            label=default_text["history_select_label"],
                            choices=_history_choices(),
                            value=None,
                            elem_id="history-select",
                            scale=9,
                        )
                        history_refresh_button = gr.Button(
                            value=default_text["history_refresh"],
                            elem_id="history-refresh-button",
                            scale=2,
                        )

                    # 回测结果可视化仪表盘（大字总盈亏 + 权益曲线 + 指标卡 + 成交/订单列表），
                    # 全宽主角。当前回测结果与选中的历史记录【共用此仪表盘】重渲。图表 HTML
                    # 仍落盘 Past_data/ 并随历史 JSON 留档，改按需打开（配「查看详细图表」）。
                    backtest_result_output = gr.HTML(
                        build_dashboard_placeholder(default_lang),
                        elem_id="result-box",
                    )
                    # 当前/历史的回测图 + 稳健性图 HTML 路径（gr.State 持有，按钮按需打开）
                    chart_path_state = gr.State(None)
                    robustness_chart_path_state = gr.State(None)
                    with gr.Row(elem_classes=["param-row"], elem_id="chart-buttons"):
                        view_chart_button = gr.Button(
                            value=default_text["view_chart_button"],
                            elem_id="view-chart-button",
                            size="sm",
                        )
                        view_robustness_chart_button = gr.Button(
                            value=default_text["view_robustness_chart_button"],
                            elem_id="view-robustness-chart-button",
                            size="sm",
                        )
                    # 稳健性分析报告：每次回测默认自动跑（70% IS 切分）随结果一并展示；
                    # 浏览历史时重渲当时的报告。初始留空（不占版面）。
                    robustness_output = gr.HTML("", elem_id="robustness-output")
                    # 历史记录详情（提示词/参数/代码/摘要）：仅浏览历史时填充，回测时清空。
                    # 初始留空（不占版面）。
                    history_detail_output = gr.HTML("", elem_id="history-detail-box")

            with gr.Row(elem_id="data-status-bar", equal_height=True):
                with gr.Column(scale=9, min_width=260, elem_id="fetch-progress-col"):
                    fetch_progress = gr.HTML(
                        build_fetch_progress_html(fetch_queue.snapshot(), default_lang),
                        elem_id="fetch-progress-wrap",
                    )
                with gr.Column(scale=2, min_width=130, elem_id="fetch-action-col"):
                    fetch_button = gr.Button(
                        value=get_fetch_text(default_lang)["button"],
                        elem_id="fetch-update-button",
                        size="sm",
                    )
                    integrity_button = gr.Button(
                        value=get_fetch_text(default_lang)["button_integrity"],
                        elem_id="fetch-integrity-button",
                        size="sm",
                    )

                fetch_timer = gr.Timer(1.0)

            language_select.change(
                fn=update_ui_language,
                inputs=[
                    language_select,
                    market_select,
                    timeframe_select,
                    initial_cash_input,
                ],
                outputs=[
                    header_md,
                    status_strip,
                    strategy_input,
                    language_select,
                    market_select,
                    symbol_input,
                    timeframe_select,
                    param_priority_note,
                    start_date,
                    end_date,
                    initial_cash_input,
                    leverage_input,
                    position_size_input,
                    fee_rate_input,
                    slippage_input,
                    generate_button,
                    backtest_button,
                    code_accordion,
                    review_output,
                    backtest_result_output,
                    fetch_progress,
                    fetch_button,
                    integrity_button,
                    view_chart_button,
                    view_robustness_chart_button,
                    history_select,
                    history_refresh_button,
                ],
                api_name="update_language",
            )

            for status_source in [
                market_select,
                timeframe_select,
                initial_cash_input,
            ]:
                status_source.change(
                    fn=update_status_cards,
                    inputs=[
                        language_select,
                        market_select,
                        timeframe_select,
                        initial_cash_input,
                    ],
                    outputs=[status_strip],
                    show_progress=False,
                )

            generate_button.click(
                fn=generate_code_from_ui,
                inputs=[
                    strategy_input,
                    language_select,
                    market_select,
                    symbol_input,
                    timeframe_select,
                    start_date,
                    end_date,
                    initial_cash_input,
                    leverage_input,
                    position_size_input,
                    fee_rate_input,
                    slippage_input,
                ],
                outputs=[
                    strategy_code_output,
                    review_output,
                ],
                api_name="generate_strategy_code",
            )

            backtest_button.click(
                fn=run_backtest_from_ui,
                inputs=[
                    strategy_code_output,
                    language_select,
                    market_select,
                    symbol_input,
                    timeframe_select,
                    start_date,
                    end_date,
                    initial_cash_input,
                    leverage_input,
                    position_size_input,
                    fee_rate_input,
                    slippage_input,
                    strategy_input,   # 自然语言提示词：随回测一起落历史留档（run_history）
                ],
                outputs=[
                    backtest_result_output,
                    chart_path_state,   # 回测图路径 → State，供「查看详细图表」按需打开
                    robustness_output,  # 稳健性报告（每次回测自动跑，随结果一并展示）
                    robustness_chart_path_state,  # 稳健性图路径 → State，供按钮按需打开
                ],
                api_name="run_backtest",
            ).then(
                # 回测落档后刷新「查看历史」下拉（新记录即时可选）+ 清空历史详情
                fn=after_backtest_history,
                inputs=[language_select],
                outputs=[history_select, history_detail_output],
            )

            # 「查看详细图表」/「查看稳健性图表」：按需在本地浏览器打开已落盘图表（无 UI 输出）
            view_chart_button.click(
                fn=on_view_chart,
                inputs=[chart_path_state, language_select],
                outputs=[],
            )
            view_robustness_chart_button.click(
                fn=on_view_chart,
                inputs=[robustness_chart_path_state, language_select],
                outputs=[],
            )

            # 查看历史：手动刷新列表 + 选中即在【主仪表盘】重渲 + 填充详情 + 重渲稳健性 + 切换图表路径
            history_refresh_button.click(
                fn=refresh_history_choices,
                inputs=[language_select],
                outputs=[history_select],
            )
            history_select.change(
                fn=on_select_history,
                inputs=[history_select, language_select, chart_path_state, robustness_chart_path_state],
                outputs=[backtest_result_output, history_detail_output, chart_path_state,
                         robustness_output, robustness_chart_path_state],
            )

            # ---- 数据拉取进度区：Timer 轮询全局状态、手动更新、启动自动更新 ----

            fetch_timer.tick(
                fn=refresh_fetch_progress,
                inputs=[language_select],
                outputs=[fetch_progress, fetch_button],
            )

            fetch_button.click(
                fn=on_manual_update,
                inputs=[language_select],
                outputs=[fetch_progress, fetch_button],
                api_name="update_market_data",
            )

            integrity_button.click(
                fn=on_integrity_check,
                inputs=[language_select],
                outputs=[fetch_progress, fetch_button],
                api_name="check_repair_data",
            )

            demo.load(
                fn=on_app_start,
                inputs=[language_select],
                outputs=[fetch_progress, fetch_button],
            )


# =========================================================
# 启动
# =========================================================

if __name__ == "__main__":
    # css/head 必须在 launch() 传入（Gradio 6 起；传给 gr.Blocks 会被丢弃，
    # 导致 19KB studio 样式不生效、界面渲染为无样式）
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=True,
        css=custom_css,
        head=custom_head,
    )
