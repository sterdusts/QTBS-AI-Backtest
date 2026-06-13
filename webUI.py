import re
import math
import html
from datetime import datetime, timezone

import gradio as gr
from module.AI.api_config import LANGUAGE_DISPLAY_NAMES, clamp_score
from module.AI.deepseek_strategy_reviewer import review_strategy_code_with_deepseek
from module.AI.deepseek_code_generator import generate_strategy_code_with_deepseek
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
    filter_df_by_date,
    list_local_symbols,
    load_aligned_panel,
    load_symbol_kline,
)
from module.modules.generic_chart import plot_generic_equity_curves
from module.modules.portfolio_chart import plot_portfolio_result
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
        "code_output_label": "DeepSeek 生成的策略代码",
        "result_output_label": "回测结果",
        "chart_file_label": "回测图表文件",
        "empty_strategy_error": "策略输入不能为空。",
        "start_time_error": "起始时间不能早于 2017-01-01。",
        "invalid_date_error": "日期格式无效。推荐使用 YYYY-MM-DD，例如 2017-01-13；也支持 2017.1.13、2017/1/13、2017年1月13日、13/1/2017。",
        "date_order_error": "结束时间不能早于起始时间。",
        "api_fail_error": "DeepSeek API 调用失败",
        "backtest_fail_error": "回测运行失败",
        "no_code_error": "策略代码不能为空，请先生成策略代码。",
        "invalid_number_error": "请输入有效数字。",
        "invalid_initial_cash_error": "初始资金必须大于 0。",
        "invalid_leverage_error": "杠杆倍数必须是 0 到 200 之间的整数。0 表示不启用杠杆，实际按 1 倍计算。",
        "invalid_position_size_error": "仓位比例必须在 0 到 100 之间。",
        "invalid_fee_rate_error": "手续费率不能小于 0。请输入 0 或正数，例如 0.05 表示 0.05%，也就是万分之五。",
        "invalid_slippage_error": "滑点不能小于 0。请输入 0 或正数，例如 0.02 表示 0.02%。",
        "too_few_klines_error": "过滤后的 K线数量太少，当前只有 {count} 条。请扩大时间范围。",
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
        "code_output_label": "DeepSeek가 생성한 전략 코드",
        "result_output_label": "백테스트 결과",
        "chart_file_label": "백테스트 차트 파일",
        "empty_strategy_error": "전략 입력은 비워둘 수 없습니다.",
        "start_time_error": "시작 시간은 2017-01-01보다 이를 수 없습니다.",
        "invalid_date_error": "날짜 형식이 올바르지 않습니다. 권장 형식은 YYYY-MM-DD입니다. 예: 2017-01-13. 2017.1.13, 2017/1/13, 2017년1월13일, 13/1/2017 형식도 지원합니다.",
        "date_order_error": "종료 시간은 시작 시간보다 이를 수 없습니다.",
        "api_fail_error": "DeepSeek API 호출 실패",
        "backtest_fail_error": "백테스트 실행 실패",
        "no_code_error": "전략 코드가 비어 있습니다. 먼저 전략 코드를 생성하세요.",
        "invalid_number_error": "유효한 숫자를 입력하세요.",
        "invalid_initial_cash_error": "초기 자금은 0보다 커야 합니다.",
        "invalid_leverage_error": "레버리지는 0에서 200 사이의 정수여야 합니다. 0은 레버리지를 사용하지 않는다는 뜻이며 실제 계산은 1배로 처리됩니다.",
        "invalid_position_size_error": "포지션 비율은 0에서 100 사이여야 합니다.",
        "invalid_fee_rate_error": "수수료율은 0보다 작을 수 없습니다. 0 또는 양수를 입력하세요. 예: 0.05는 0.05%, 즉 0.0005를 의미합니다.",
        "invalid_slippage_error": "슬리피지는 0보다 작을 수 없습니다. 0 또는 양수를 입력하세요. 예: 0.02는 0.02%를 의미합니다.",
        "too_few_klines_error": "필터링 후 K라인 수가 너무 적습니다. 현재 {count}개입니다. 기간을 넓혀 주세요.",
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
        "code_output_label": "Strategy Code Generated by DeepSeek",
        "result_output_label": "Backtest Result",
        "chart_file_label": "Backtest Chart File",
        "empty_strategy_error": "Strategy input cannot be empty.",
        "start_time_error": "Start time cannot be earlier than 2017-01-01.",
        "invalid_date_error": "Invalid date format. Recommended format: YYYY-MM-DD, for example 2017-01-13. Also supports 2017.1.13, 2017/1/13, 2017年1月13日, 13/1/2017, and 1/13/2017.",
        "date_order_error": "End time cannot be earlier than start time.",
        "api_fail_error": "DeepSeek API call failed",
        "backtest_fail_error": "Backtest failed",
        "no_code_error": "Strategy code is empty. Generate strategy code first.",
        "invalid_number_error": "Please enter a valid number.",
        "invalid_initial_cash_error": "Initial cash must be greater than 0.",
        "invalid_leverage_error": "Leverage must be an integer between 0 and 200. 0 means no leverage and will be calculated as 1x.",
        "invalid_position_size_error": "Position size must be between 0 and 100.",
        "invalid_fee_rate_error": "Fee rate cannot be less than 0. Enter 0 or a positive number. Example: 0.05 means 0.05%, equal to 0.0005.",
        "invalid_slippage_error": "Slippage cannot be less than 0. Enter 0 or a positive number. Example: 0.02 means 0.02%.",
        "too_few_klines_error": "Too few K-lines after filtering. Current count: {count}. Please expand the time range.",
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
        "code_output_label": "DeepSeek が生成した戦略コード",
        "result_output_label": "バックテスト結果",
        "chart_file_label": "バックテストチャートファイル",
        "empty_strategy_error": "戦略入力は空にできません。",
        "start_time_error": "開始時間は 2017-01-01 より前にできません。",
        "invalid_date_error": "日付形式が無効です。推奨形式は YYYY-MM-DD です。例：2017-01-13。2017.1.13、2017/1/13、2017年1月13日、13/1/2017 も対応しています。",
        "date_order_error": "終了時間は開始時間より前にできません。",
        "api_fail_error": "DeepSeek API の呼び出しに失敗しました",
        "backtest_fail_error": "バックテスト実行に失敗しました",
        "no_code_error": "戦略コードが空です。先に戦略コードを生成してください。",
        "invalid_number_error": "有効な数値を入力してください。",
        "invalid_initial_cash_error": "初期資金は 0 より大きい必要があります。",
        "invalid_leverage_error": "レバレッジは 0 から 200 までの整数である必要があります。0 はレバレッジなしを意味し、実際の計算では 1倍として扱います。",
        "invalid_position_size_error": "ポジション比率は 0 から 100 の間である必要があります。",
        "invalid_fee_rate_error": "手数料率は 0 未満にできません。0 または正の数を入力してください。例：0.05 は 0.05%、つまり 0.0005 を意味します。",
        "invalid_slippage_error": "スリッページは 0 未満にできません。0 または正の数を入力してください。例：0.02 は 0.02% を意味します。",
        "too_few_klines_error": "フィルタ後のK線数が少なすぎます。現在 {count} 本です。期間を広げてください。",
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
        "code_output_label": "كود الاستراتيجية الذي أنشأه DeepSeek",
        "result_output_label": "نتيجة الاختبار الخلفي",
        "chart_file_label": "ملف رسم الاختبار الخلفي",
        "empty_strategy_error": "لا يمكن أن يكون إدخال الاستراتيجية فارغًا.",
        "start_time_error": "لا يمكن أن يكون وقت البداية أقدم من 2017-01-01.",
        "invalid_date_error": "تنسيق التاريخ غير صالح. التنسيق الموصى به هو YYYY-MM-DD، مثال: 2017-01-13. يتم أيضًا دعم 2017.1.13 و 2017/1/13 و 2017年1月13日 و 13/1/2017.",
        "date_order_error": "لا يمكن أن يكون وقت النهاية أقدم من وقت البداية.",
        "api_fail_error": "فشل استدعاء واجهة DeepSeek API",
        "backtest_fail_error": "فشل تشغيل الاختبار الخلفي",
        "no_code_error": "كود الاستراتيجية فارغ. يرجى إنشاء الكود أولاً.",
        "invalid_number_error": "يرجى إدخال رقم صالح.",
        "invalid_initial_cash_error": "يجب أن يكون رأس المال الأولي أكبر من 0.",
        "invalid_leverage_error": "يجب أن تكون الرافعة المالية عددًا صحيحًا بين 0 و 200. القيمة 0 تعني عدم استخدام الرافعة ويتم الحساب فعليًا على أساس 1x.",
        "invalid_position_size_error": "يجب أن يكون حجم الصفقة بين 0 و 100.",
        "invalid_fee_rate_error": "لا يمكن أن تكون نسبة الرسوم أقل من 0. أدخل 0 أو رقمًا موجبًا. مثال: 0.05 يعني 0.05%، أي 0.0005.",
        "invalid_slippage_error": "لا يمكن أن يكون الانزلاق السعري أقل من 0. أدخل 0 أو رقمًا موجبًا. مثال: 0.02 يعني 0.02%.",
        "too_few_klines_error": "عدد شموع K-line بعد التصفية قليل جدًا. العدد الحالي: {count}. يرجى توسيع الفترة الزمنية.",
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
        "code_output_label": "Код стратегии, сгенерированный DeepSeek",
        "result_output_label": "Результат бэктеста",
        "chart_file_label": "Файл графика бэктеста",
        "empty_strategy_error": "Поле стратегии не может быть пустым.",
        "start_time_error": "Начальная дата не может быть раньше 2017-01-01.",
        "invalid_date_error": "Неверный формат даты. Рекомендуемый формат: YYYY-MM-DD, например 2017-01-13. Также поддерживаются 2017.1.13, 2017/1/13, 2017年1月13日 и 13/1/2017.",
        "date_order_error": "Конечное время не может быть раньше начального времени.",
        "api_fail_error": "Ошибка вызова DeepSeek API",
        "backtest_fail_error": "Ошибка запуска бэктеста",
        "no_code_error": "Код стратегии пуст. Сначала сгенерируйте код стратегии.",
        "invalid_number_error": "Введите корректное число.",
        "invalid_initial_cash_error": "Начальный капитал должен быть больше 0.",
        "invalid_leverage_error": "Кредитное плечо должно быть целым числом от 0 до 200. 0 означает отсутствие плеча и фактически рассчитывается как 1x.",
        "invalid_position_size_error": "Размер позиции должен быть от 0 до 100.",
        "invalid_fee_rate_error": "Комиссия не может быть меньше 0. Введите 0 или положительное число. Например: 0.05 означает 0.05%, то есть 0.0005.",
        "invalid_slippage_error": "Проскальзывание не может быть меньше 0. Введите 0 или положительное число. Например: 0.02 означает 0.02%.",
        "too_few_klines_error": "После фильтрации осталось слишком мало K-line данных. Текущее количество: {count}. Расширьте временной диапазон.",
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
        "tip_initial": "Первая загрузка этих инструментов (1-мин данные с 2017):",
        "tip_update": "Обновление данных по этим инструментам:",
        "tip_idle": "При запуске локальные инструменты сканируются и обновляются автоматически; при первом запуске загружаются инструменты по умолчанию. Нажмите «Обновить данные» для ручного обновления.",
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


def resolve_strategy_route(strategy_code: str, ui_symbol: str):
    """
    根据策略代码元数据决定回测路由。

    返回 (contract_version, symbols)：
    - v2：使用代码中声明的 SYMBOLS（必须存在）
    - v1：代码点名了标的就用代码的，否则用 UI 选择的标的
    """

    metadata = parse_strategy_metadata(strategy_code)

    # 版本与 SYMBOLS 组合规则单源在 strategy_loader.validate_strategy_metadata
    # （与生成侧共用）：未知版本、v2 缺 SYMBOLS/格式不规范、v1 多标的都在此拒绝
    validate_strategy_metadata(metadata)

    if metadata["contract_version"] == 2:
        # 共享校验已保证 SYMBOLS 非空、格式规范且无重复，原样使用
        return 2, list(metadata["symbols"])

    # v1：校验保证 SYMBOLS 至多一个，不存在静默截断
    if metadata["symbols"]:
        return 1, [normalize_symbol(metadata["symbols"][0])]

    return 1, [normalize_symbol(ui_symbol)]


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


def update_ui_language(lang_code: str):
    text = get_ui_text(lang_code)
    _snap = fetch_queue.snapshot()  # 进度区与按钮共用一次快照

    return [
        text["header"],
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
        gr.update(
            label=text["result_output_label"],
        ),
        gr.update(
            label=text["chart_file_label"],
        ),
        # 进度区按新语言重渲染（读一次快照，不重置进度，也不再额外 is_running）
        gr.update(value=build_fetch_progress_html(_snap, lang_code)),
        gr.update(
            value=get_fetch_text(lang_code)[
                "button_running" if _snap["running"] else "button"
            ]
        ),
    ]

# =========================================================
# DeepSeek 生成策略代码
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
):
    text = get_ui_text(output_language)
    summary_text = get_summary_text(output_language)

    try:
        if strategy_code is None or strategy_code.strip() == "":
            return text["no_code_error"], None

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
            return str(e), None

        fee_rate_value = fee_rate_percent_value / 100
        slippage_value = slippage_percent_value / 100
        position_size_value = position_size_percent_value / 100

        # 1. 解析契约元数据决定路由（v1 单标的 / v2 组合），
        #    校验并从内存加载策略函数（不经过共享文件，并发安全），
        #    同时把实际参与回测的代码留档到 Past_data/strategy_code/ 便于追溯
        route_version, route_symbols = resolve_strategy_route(strategy_code, symbol)
        strategy_func = load_strategy_func_from_code(strategy_code)
        save_strategy_code_audit(strategy_code)

        # 两个引擎共享同一组参数：只在这里定义一次，
        # 新增参数时不会出现 v1/v2 分支漏改一边的静默分叉
        engine_kwargs = dict(
            strategy_func=strategy_func,
            initial_cash=initial_cash_value,
            fee_rate=fee_rate_value,
            slippage=slippage_value,
            leverage=effective_leverage_value,
            position_size=position_size_value,
        )

        # ---- 按版本只加载数据；数据充分性标准共用一份，v1/v2 不会各自漂移 ----

        if route_version == 2:
            # 日期过滤下沉到 load_aligned_panel 内部、对齐之前执行：
            # 避免先对全历史做 union 对齐、再把窗口外的行全部丢弃
            # required_end 缺省派生自 end_date，无需重复传
            data = load_aligned_panel(
                route_symbols, timeframe,
                start_date=start_str, end_date=end_str,
            )
            data_index = next(iter(data.values())).index
            display_symbol = " + ".join(route_symbols)
        else:
            symbol = route_symbols[0]
            df = load_symbol_kline(symbol, timeframe, required_end=end_str)
            df = filter_df_by_date(df, start_str, end_str)
            data_index = df.index
            display_symbol = symbol

        kline_count = len(data_index)

        if kline_count < 100:
            return text["too_few_klines_error"].format(count=kline_count), None

        # 摘要展示实际参与回测的数据范围：本地数据没覆盖到请求窗口时，
        # 按请求日期展示会给短窗口结果贴上长周期标签（年化/夏普失真）。
        # 索引已排序，取首尾是 O(1)
        actual_start = str(data_index[0])
        actual_end = str(data_index[-1])

        # ---- 跑引擎、出图：分支体只保留真正不同的部分 ----

        if route_version == 2:
            result = PortfolioBacktestCore(**engine_kwargs).run(data)

            base_names = "_".join(get_base_asset(s) for s in route_symbols[:4])
            html_path = plot_portfolio_result(
                result=result,
                output_dir="Past_data",
                file_prefix=f"{base_names}_{timeframe}_webui_portfolio",
                timeframe=timeframe,
                language=output_language,
                auto_open=True,
            )
        else:
            result = CodeBacktestCore(**engine_kwargs).run(df)

            html_path = plot_generic_equity_curves(
                result=result,
                output_dir="Past_data",
                file_prefix=f"{symbol}_{timeframe}_webui_code_strategy",
                auto_open=True,
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

        return summary, html_path

    except Exception as e:
        return f"{text['backtest_fail_error']}：{str(e)}", None


# =========================================================
# 页面 CSS
# =========================================================

custom_css = """
#main-container {
    max-width: 1500px;
    margin: 0 auto;
}

.review-behavior {
    font-size: 12.5px;
    margin: 8px 0 2px 0;
    display: flex;
    align-items: flex-start;
    justify-content: flex-end;
    gap: 6px;
}

/* 颜色直接钉到内部 span 并 !important：否则被 Gradio prose 的 span 颜色覆盖成灰 */
.review-behavior.behavior-pass,
.review-behavior.behavior-pass > span {
    color: #2da44e !important;
}

.review-behavior.behavior-fail,
.review-behavior.behavior-fail > span {
    color: #d64545 !important;
    font-weight: 600;
}

/* 行为检查的 "!" 气泡是绝对定位后代：整条祖先链不能裁切 */
#review-output,
#review-output .html-container,
#review-output .prose {
    overflow: visible !important;
}

/* 气泡是绝对定位的后代元素：从组件到气泡的整条祖先链都不能裁切，
   否则悬停时只剩 help 光标、看不到内容 */
#param-priority-wrap,
#param-priority-wrap .html-container,
#param-priority-wrap .prose {
    overflow: visible !important;
}

#param-priority-note {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    line-height: 1.4;
    color: #8a8f99;
    margin: 0 0 4px 2px;
}

/* 防溢出的裁切只作用于短句文本本身，不碰气泡所在的兄弟节点 */
#param-priority-note > span:first-child {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* ---- 数据拉取进度区 ---- */
#fetch-progress-wrap,
#fetch-progress-wrap .html-container,
#fetch-progress-wrap .prose {
    overflow: visible !important;
}

#fetch-progress {
    font-size: 12px;
    color: #6b7280;
    margin: 6px 2px 2px 2px;
}

.fetch-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
}

.fetch-status {
    font-weight: 600;
    color: #4b5563;
}

#fetch-progress.fetch-active .fetch-status {
    color: #2563eb;
}

.fetch-total-row {
    display: flex;
    align-items: center;
    gap: 8px;
}

.fetch-total-track {
    flex: 1;
    height: 7px;
    border-radius: 4px;
    background: #e5e7eb;
    overflow: hidden;
}

.fetch-total-bar {
    height: 100%;
    border-radius: 4px;
    background: linear-gradient(90deg, #34d399, #2563eb);
    transition: width 0.4s ease;
}

.fetch-total-num {
    font-variant-numeric: tabular-nums;
    color: #6b7280;
    min-width: 42px;
    text-align: right;
}

.fetch-current-label {
    margin-top: 4px;
    color: #6b7280;
}

.fetch-pulse-track {
    margin-top: 3px;
    height: 5px;
    border-radius: 3px;
    background: #e5e7eb;
    overflow: hidden;
    position: relative;
}

.fetch-pulse-bar {
    position: absolute;
    height: 100%;
    width: 35%;
    border-radius: 3px;
    background: linear-gradient(90deg, rgba(37,99,235,0.2), #2563eb, rgba(37,99,235,0.2));
    animation: fetch-pulse 1.1s ease-in-out infinite;
}

@keyframes fetch-pulse {
    0%   { left: -35%; }
    100% { left: 100%; }
}

#fetch-update-button {
    margin-top: 6px;
    font-size: 12px !important;
    min-height: 30px !important;
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
    width: 15px;
    height: 15px;
    border: 1px solid #a8abb2;
    border-radius: 50%;
    font-size: 10px;
    font-weight: 700;
    color: #8a8f99;
    cursor: help;
    user-select: none;
}

.qtbs-tooltip .qtbs-tooltip-text {
    visibility: hidden;
    opacity: 0;
    position: absolute;
    top: 130%;
    right: -8px;
    z-index: 1000;
    width: 360px;
    max-width: 70vw;
    padding: 10px 12px;
    border-radius: 8px;
    background: rgba(40, 44, 52, 0.96);
    color: #f0f2f5;
    font-size: 12px;
    line-height: 1.6;
    white-space: normal;
    text-align: start;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
    transition: opacity 0.15s ease;
    pointer-events: none;
}

.qtbs-tooltip:hover .qtbs-tooltip-text {
    visibility: visible;
    opacity: 1;
}

#top-bar {
    align-items: flex-start;
    margin-bottom: 8px;
}

#header-panel {
    padding-right: 20px;
}

/* 右上区：进度模块（左）+ 语言选择（右）并排等高 */
#top-right-panel {
    margin-left: auto;
    padding-top: 6px;
}

#top-right-row {
    align-items: stretch;
}

/* 语言列垂直居中，与左侧进度模块顶底对齐看起来等高 */
#lang-col {
    justify-content: center;
}

#language-select {
    max-width: 200px;
}

#language-select label {
    font-size: 13px !important;
}

#language-select input {
    font-size: 13px !important;
}

#language-select .wrap {
    min-height: 36px !important;
}

#strategy-box textarea {
    min-height: 430px !important;
    font-size: 18px !important;
    border-radius: 18px !important;
    background: #f4fbfc !important;
    border: 1px solid #dceff2 !important;
    box-shadow: none !important;
    outline: none !important;
}

#strategy-box textarea:focus {
    border: 1px solid #cfe7ec !important;
    box-shadow: 0 0 0 2px rgba(207, 231, 236, 0.35) !important;
    outline: none !important;
}

#right-panel {
    padding-top: 20px;
}

.param-row {
    gap: 12px !important;
    margin-bottom: 2px !important;
}

#right-panel input,
#right-panel textarea {
    font-size: 14px !important;
}

#right-panel .wrap {
    min-height: 38px !important;
}

#button-row {
    gap: 12px !important;
    margin-top: 12px !important;
}

#generate-button, #backtest-button {
    height: 48px;
    font-size: 18px;
    border-radius: 14px;
}

#output-code textarea {
    font-size: 14px !important;
}

#result-box textarea {
    font-size: 15px !important;
}

/* AI 策略审查评分卡 */
#review-output {
    margin-top: 14px;
    width: 100%;
}

.review-card {
    background: #1f1f22;
    border: 1px solid #3a3a40;
    border-radius: 12px;
    padding: 16px;
    color: #f2f2f2;
    min-height: 160px;
}

.review-layout {
    display: grid;
    grid-template-columns: 6fr 4fr;
    gap: 14px;
    align-items: start;
}

.review-left {
    min-width: 0;
}

.review-right {
    border-left: 1px solid #3a3a40;
    padding-left: 14px;
}

.review-title {
    font-size: 15px;
    font-weight: 700;
    margin-bottom: 12px;
}

.score-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.score-bar-bg {
    flex: 1;
    height: 12px;
    background: #3a3a40;
    border-radius: 999px;
    overflow: hidden;
}

.score-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #f97316, #22c55e);
    border-radius: 999px;
}

.score-number {
    width: 68px;
    text-align: right;
    font-weight: 700;
    color: #f3f4f6;
}

.review-summary-title {
    font-size: 13px;
    font-weight: 700;
    color: #e5e7eb;
    margin-bottom: 8px;
}

.review-summary {
    font-size: 12px;
    color: #b8b8b8;
    line-height: 1.6;
    margin-bottom: 8px;
}

.review-note {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #3a3a40;
    font-size: 12px;
    line-height: 1.6;
    color: #9ca3af;
}

.review-empty {
    font-size: 13px;
    color: #9ca3af;
}
"""


# =========================================================
# Gradio 前端
# =========================================================

default_lang = "zh"
default_text = get_ui_text(default_lang)

with gr.Blocks(
    title="QTBS AI Quant Strategy Frontend",
) as demo:

    with gr.Column(elem_id="main-container"):

        with gr.Row(elem_id="top-bar"):

            with gr.Column(scale=6, elem_id="header-panel"):
                header_md = gr.Markdown(default_text["header"])

            # 右上：数据进度模块（左）+ 语言选择（右）并排等高
            with gr.Column(scale=6, min_width=420, elem_id="top-right-panel"):
                with gr.Row(equal_height=True, elem_id="top-right-row"):
                    with gr.Column(scale=3, min_width=220, elem_id="fetch-col"):
                        fetch_progress = gr.HTML(
                            build_fetch_progress_html(fetch_queue.snapshot(), default_lang),
                            elem_id="fetch-progress-wrap",
                        )
                        fetch_button = gr.Button(
                            value=get_fetch_text(default_lang)["button"],
                            elem_id="fetch-update-button",
                            size="sm",
                        )

                    with gr.Column(scale=2, min_width=150, elem_id="lang-col"):
                        language_select = gr.Dropdown(
                            label=default_text["language_label"],
                            choices=LANGUAGE_CHOICES,
                            value=default_lang,
                            interactive=True,
                            elem_id="language-select",
                        )

                fetch_timer = gr.Timer(1.0)

        with gr.Row():
            with gr.Column(scale=7, min_width=650):
                strategy_input = gr.Textbox(
                    label=default_text["strategy_label"],
                    placeholder=default_text["strategy_placeholder"],
                    lines=18,
                    elem_id="strategy-box",
                )

                review_output = gr.HTML(
                    value=build_review_html(None, default_lang),
                    elem_id="review-output",
                )

            with gr.Column(scale=3, min_width=420, elem_id="right-panel"):

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

                with gr.Row(elem_classes=["param-row"]):
                    slippage_input = gr.Number(
                        label=default_text["slippage_label"],
                        value=0,
                        precision=4,
                        minimum=0,
                        step=0.01,
                        scale=1,
                    )

                    with gr.Column(scale=1):
                        gr.Markdown("")

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

        with gr.Accordion(default_text["code_output_label"], open=False) as code_accordion:
            strategy_code_output = gr.Code(
                label="",
                language="python",
                lines=26,
                elem_id="output-code",
            )

        backtest_result_output = gr.Textbox(
            label=default_text["result_output_label"],
            lines=20,
            elem_id="result-box",
        )

        chart_file_output = gr.File(
            label=default_text["chart_file_label"],
        )

        language_select.change(
            fn=update_ui_language,
            inputs=[language_select],
            outputs=[
                header_md,
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
                chart_file_output,
                fetch_progress,
                fetch_button,
            ],
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
            ],
            outputs=[
                backtest_result_output,
                chart_file_output,
            ],
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
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=True,
        css=custom_css,
    )