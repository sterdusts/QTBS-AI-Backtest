# QTBS 策略契约（STRATEGY CONTRACT）

**当前版本：v1**

本文档是 AI 生成层与回测引擎之间契约的**单一事实源（Single Source of Truth）**。

它回答两个问题：

1. AI 必须生成什么样的策略代码（策略 → 引擎的承诺）
2. 引擎承诺如何执行这些信号（引擎 → 策略的承诺）

## 同步规则（强制）

以下三处必须保持一致，**任何一处的语义修改必须在同一次提交中同步另外两处**：

| 位置 | 角色 |
|------|------|
| 本文档 | 契约定义 |
| `module/AI/deepseek_code_generator.py` 中的 prompt | 契约的 AI 表述 |
| `tests/` 金样例测试 | 契约的可执行验证 |

---

## 1. 接口签名

```python
def generate_signals(df: pd.DataFrame) -> pd.DataFrame
```

策略代码是一个**纯函数**：输入 K 线数据，输出带目标仓位列的数据。
除此之外不做任何事情（不下单、不算钱、不读写文件、不联网）。

## 2. 输入保证（引擎 → 策略）

引擎传入的 `df` 保证满足：

- index 为 `DatetimeIndex`（UTC、升序、无重复）
- 至少包含列：`open`、`high`、`low`、`close`、`volume`（均为 float）
- 可能包含 `close_time` 列（策略不应依赖它）
- **周期任意**：同一份策略代码可能收到 1m / 5m / 15m / 1h / 4h / 1d 的数据
- 数据已清洗：无 NaN 价格、无非正价格

## 3. 输出要求（策略 → 引擎）

策略必须返回包含 `target_position` 列的 DataFrame：

| 值 | 含义 |
|----|------|
| `1` | 目标状态：持有多仓 |
| `0` | 目标状态：空仓 |
| `-1` | 目标状态：持有空仓 |

**语义为「目标状态」，不是「交易事件」**：

- `target_position` 描述的是"这根 K 线收盘后，仓位应该是什么状态"
- 引擎按「实际持仓 vs 目标状态」对账：不一致才交易，一致则什么都不做
- 首根 K 线即可输出非 0 目标（引擎会在第二根 K 线开盘执行）
- 信号无变化时应延续上一根的状态（推荐 `ffill().fillna(0)`）
- 收尾必须保证：列中只含 `-1 / 0 / 1`，无 NaN（引擎会做 `fillna(0)` 兜底 + 非法值前置拦截）

**回测窗口锚定输入索引（引擎 → 策略，引擎层承诺）**：

回测窗口的唯一事实源是**引擎传入的输入索引**，与策略返回帧的行数/索引无关。
策略对返回帧做 `dropna` 或其他缩短行操作时，引擎按输入索引重对齐
（`reindex(input_index)`），与 v2 §10.2 的 `weights.reindex(index).ffill().fillna(0)`
同口径，**不会**在更短窗口静默跑完：

- **`target_position` 缺行（策略未返回的行）= 延续上一行（`ffill`）**，首段无前值兜底为 0；
  策略明确返回的行中 NaN 仍按 `fillna(0)` 视为空仓
- **行情列（OHLC）始终取自引擎输入帧**（真实市场数据）：策略缩短行情行不影响
  回测窗口，equity_curve 始终覆盖完整输入区间，且行情值绝不被 `ffill` 造假
- 策略返回帧与输入**等长同序**时（常规情形），重对齐为无操作，逐根行为完全不变
- 策略返回帧索引与输入索引**完全不重叠**（`reset_index` / 整体平移时间轴）→ **报错**：
  静默全表回退 0 会产出一份「0 笔交易」的假正常报告

## 4. 时序规则（防未来函数）

1. 所有信号视为在**当前 K 线收盘后**确认
2. 引擎在**下一根 K 线开盘价**执行
3. 上穿/下穿判断必须用 `shift(1)` 比较前一根
4. 禁止 `shift(-1)` 及任何形式的未来数据
5. `rolling` / `ewm` 等指标只能基于当前及历史数据
6. 禁止在代码中写死周期字符串（`"1m"` ~ `"1d"`）、定义 timeframe 变量、`resample`、读取其他周期数据

## 5. 执行模型承诺（引擎 → 策略）

以下全部由引擎负责，**策略代码不得自行实现**：

### 5.1 成交

- 成交方式：下一根 K 线开盘价市价成交
- 滑点：做多开仓/做空平仓按 `price × (1 + slippage)`，反向按 `price × (1 - slippage)`
- 手续费：开仓和平仓均按 `名义价值 × fee_rate` 收取
- 盈亏口径（两引擎一致）：`gross_pnl` = 实际持仓 × raw 价差
  （**不含滑点与手续费**的纯价格盈亏）；`pnl`/`net_pnl` = 含滑点与
  手续费的真实权益变化

### 5.2 仓位

- 开仓保证金 = 当时全部权益 × `position_size`
- 名义价值 = 保证金 × `leverage`
- 持仓数量 = 名义价值 ÷ 成交价（含滑点）
- v1 不支持部分加减仓：每次只有 全开 / 全平 / 反手

### 5.3 强平（简化模型）

- 逐 K 线用 `high` / `low` 检查盘中最坏情况
- 维持保证金 = `maintenance_margin_rate × 当前名义价值`
  （交易所惯例口径，与 v2 引擎同一公式；默认 0，即权益归零才强平）
- 多仓强平价：`(position × entry_price − cash) / (position × (1 − rate))`
- 空仓强平价：`(position × entry_price + cash) / (position × (1 + rate))`
- `rate = 0` 时退化为 `entry_price ∓ cash / position`
- 默认 `stop_on_liquidation=True`：强平后回测终止
- `stop_on_liquidation=False` 时：强平清仓后若目标仍为非零，
  下一根按对账语义**重新开仓**（与开仓失败重试同一机制，v1/v2 一致，
  金样例钉死）；权益 ≤ 0 后仍允许目标 0 的强制平仓，只禁止开仓/调仓

### 5.4 权益记录

- 权益曲线覆盖**每一根** K 线（含首根与末根）
- 每根记录 close / high / low 三个标记价下的权益及盘中最大偏离权益（插针可见）
- 最后一根 K 线只做权益结算与强平检测，不执行交易（无下一根开盘价）

### 5.5 末尾持仓虚拟结算

回测结束时仍持仓的仓位，按最后收盘价**虚拟结算**计入交易统计
（`exit_reason="end_of_data"`）：不产生真实成交、不改变现金与权益曲线，
只保证 trade_count / 胜率不漏记这笔仓位（避免「0 笔交易却有收益」的矛盾报告）。
v1 / v2 两引擎行为一致（金样例交叉验证）。

## 6. 安全边界（加载器强制执行）

`module/Strategy/strategy_loader.py` 在加载前强制检查：

- 只允许 `import pandas` / `import numpy`（含其子模块，如 `pandas.api.types`）；运行期受限 `__import__` 仅放行这两个根模块
- `ast.walk` 全树遍历，函数体内隐藏 import 同样拦截
- **最小化 `__builtins__`**：exec 前显式注入纯计算内置白名单（len/range/min/max/abs/sum/sorted/float/int/str/list/dict/isinstance/print/异常体系等），**不含** `open`/`getattr`/`setattr`/`eval`/`exec`/`compile`/`globals`/`locals`/`vars`/`dir`/`input`/`__build_class__`，阻断 CPython 自动注入完整内置
- **AST 拒绝危险 dunder 属性链**（`__class__`/`__subclasses__`/`__globals__`/`__builtins__`/`__getattribute__`/`__code__` 等）：防 `(1).__class__...__subclasses__()` 不 import 回取宿主对象
- **AST 拒绝 pandas/numpy 文件/网络 I/O 方法**（`read_csv`/`read_pickle`/`read_parquet`/`to_csv`/`to_pickle`/`np.save`/`savetxt`/`load`/`fromfile`/`tofile` 等）：策略被允许 import pandas/numpy，但其自带 I/O 方法既非 dunder 也不含黑名单子串，否则可任意读写文件、`read_pickle` 反序列化 RCE、`read_csv(url)` SSRF
- 字符串黑名单（仅 AST 白名单覆盖不到的危险调用）：`open(`、`eval(`、`exec(`、`__import__`、`compile(`、`globals(`、`locals(`
- 必须存在顶层 `generate_signals` 函数
- 策略代码从**内存**编译加载，不经过共享文件路径（并发安全）；
  实际参与回测的代码会以时间戳文件留档到 `Past_data/strategy_code/`（仅审计，不加载）

> **边界说明**：以上是**单用户本地驾驶舱**（`demo.launch` 绑 127.0.0.1、策略来自用户自己 prompt 的 DeepSeek 生成）下的务实加固，足以挡住 AI 生成代码意外/被提示注入诱导的越权。**生产级隔离**（策略跑在独立进程 + 无网络 namespace + 只读文件系统）是平台化阶段（多用户）的目标，静态黑名单不是绝对沙箱。

## 7. AI 生成约束（prompt 同步清单)

- 只输出 Python 代码，不输出 markdown / JSON / 解释
- 代码开头：`import pandas as pd` 与 `import numpy as np`
- **必须声明模块常量 `CONTRACT_VERSION`（1 或 2）**；契约 v2 必须同时声明 `SYMBOLS = [...]`
- 契约选择：默认 v1；只有策略明确涉及多标的（对冲/配对/轮动/组合/多因子）
  或连续仓位调整（动态仓位/分批建减仓）时才用 v2
- v1 策略中用户明确点名标的时，额外声明 `SYMBOLS = ["ETHUSDT"]`（路由优先于 UI 选择）
- 不计算手续费、滑点、收益率、净值、成交价、再平衡
- 用户没要求的逻辑不要加（止损止盈、做空、负权重、加仓等）
- 仍不支持的形态（做T、精确网格）生成最接近的版本，并在注释中说明
- 注释语言跟随用户输入语言

**元数据解析（严格模式）**：`strategy_loader.parse_strategy_metadata(code)` 用 AST
提取上述常量（不执行代码）。严格性覆盖**该名字的任何顶层绑定形式**，
写错形式会**报错而不是静默回退**：
- 普通赋值与带类型注解的赋值（`CONTRACT_VERSION: int = 2`）同样支持
- 增强赋值（`+=`）、解包赋值（含星号）、只有注解没有值 → 报错
- 常量包在 if/try 等语句块内（非模块顶层）→ 报错；
  函数体内部的同名局部变量不受此限
- `CONTRACT_VERSION` 非整数常量（如 `"2"`、`2.0`）→ 报错
- `SYMBOLS` 非字符串列表/为空 → 报错

**版本与 SYMBOLS 组合规则单源**：`strategy_loader.validate_strategy_metadata`
（生成校验与回测路由共用同一实现，保证「生成放行 ⇔ 回测接受」）：
- 1/2 之外的版本值显式拒绝（不会把 v3 代码当 v1 执行）
- v2 必须声明非空 `SYMBOLS`，且为大写 USDT 交易对格式（`validate_symbols_format`）
- **v1 至多声明一个标的**：多标的拒绝而非静默截断（截断会无声丢掉对冲腿）

## 8. 已知简化与限制（v1）

记录在案，避免误读回测结果：

1. 止损/止盈通过信号表达，存在**一根 K 线的执行延迟**，且无法以盘中触发价成交
2. 资金费率（funding）：引擎已支持（见 §10.8，v2.1，v1/v2 同口径，默认关闭）；借币成本（做空/杠杆借入利息）暂未建模
3. 无部分成交、无流动性模型：任意规模均按开盘价全额成交
4. 单标的、单仓位：无法表达对冲、组合、动态仓位
5. 图表的「盘中最大偏离权益」在上下影**完全对称**的 K 线上，v1 与 v2 的
   平局取向不同（v1 取 high 侧，v2 取最坏侧）：仅影响图表插针方向，
   不影响任何结算数字

## 9. 演进路线

| 版本 | 内容 | 状态 |
|------|------|------|
| v1 | 单标的、离散目标仓位 {-1, 0, 1}，引擎 `CodeBacktestCore` | **当前默认** |
| v2 | 多标的连续目标权重，引擎 `PortfolioBacktestCore`（见第 10 节），解锁对冲/多资产/动态仓位/分批/Alpha/多因子/轮动；v1 策略不受影响，继续由现有引擎执行 | **已实现**（引擎 + AI 生成 + webUI 路由 + 组合图表） |
| v2.1 | 资金费率/借币成本（持有成本现金流），引擎按持仓名义价值逐根计提，见 §10.8 | **引擎 + 真实数据读取管线均已实现**（v1/v2 同口径 + 金样例，默认关闭；funding 数据需手动跑 `funding_rate_data.py` 下载，无则回测不计 funding）；借币成本仍未做 |
| v2.2 | 可选盘中触发价列（stop / take-profit），引擎用 high/low 判断盘中成交 | 规划 |
| v3 | 订单级事件引擎（限价/条件单），服务做T与精确网格 | 按需 |

> v1.5（单标的连续仓位）已并入 v2：单标的连续权重就是 v2 的单列特例。

## 9.1 数据更新行为（K 线拉取）

引擎之外、但影响回测数据完整性的运行时行为，记录在案：

- **原子写**（`Load_real_kline.atomic_write_csv`）：下载器在数据目录的 `.staging`
  暂存子目录写完整临时文件，再 `os.replace` 原子覆盖原文件。回测端 `read_csv`
  要么读到旧的完整文件、要么读到新的完整文件，**绝不会读到半截或写入中的
  文件**——这让「后台更新写文件」与「回测读文件」可安全并行，无需暂停任何
  一方（Windows 上目标被读句柄占用时 `os.replace` 重试兜底）。
- **统一拉取队列** `module/modules/fetch_queue.py`：单一持久后台 worker 串行
  消费队列，三条路径（启动自动更新、手动更新按钮、回测按需拉取）共用同一
  队列/去重/进度计数，保证同币种不重复拉、同时刻只打一个 Binance 请求。
- **启动自动更新**：页面加载时若队列空闲，扫描本地交易对依次增量更新；
  本地为空（首次使用）则初次拉取默认币种（`DEFAULT_INITIAL_SYMBOLS`）。
- **回测与更新并行**：得益于原子写，回测随时读到完整文件，后台更新与回测
  **真正并行，无暂停机制**。回测对**已有数据**的币种立即开跑。
- **增量更新非阻塞**：回测所需币种已有数据但未覆盖到请求窗口时，提交**后台**
  增量更新（不阻塞），回测立即用现有数据继续；下载器原子写后，下次回测即可
  拿到更新后数据。仅当本地数据未覆盖「请求 end 当天整天」才触发（与过滤切片
  同一 end-of-day 口径）；下载器始终拉到 `now UTC`、丢弃未收盘 bar，每进程每
  标的触发一次（重启可重试）。
- **首次拉取阻塞**：本地**完全无该币种数据**时，回测必须阻塞等首次拉取完成
  （`fetch_blocking`，插队首优先，900 秒超时放行）。
- **摘要显示实际范围**：回测摘要显示的是**实际参与回测的数据范围**（非请求
  窗口），即使数据未覆盖到请求 end，年化/夏普等指标也是对真实区间计算、
  不会被贴上长周期标签。
- **失败处理**：下载失败/超时不抛断回测（用现有数据）；启动批量失败后，后续
  页面加载会重试（不是一次性 latch）。

---

## 10. 契约 v2（多标的目标权重）

引擎：`module/modules/portfolio_backtest_core.py`
数据：`module/modules/data_panel.py`
测试：`tests/test_portfolio_core.py`、`tests/test_data_panel.py`

### 10.1 接口签名

```python
def generate_signals(data: dict[str, pd.DataFrame]) -> pd.DataFrame
```

- 输入 `data`：`{标的: K线DataFrame}`，由 `data_panel.load_aligned_panel` 构造，
  **所有 DataFrame 索引完全相同**（索引并集对齐）
- 输出：目标权重 DataFrame，index = 相同时间索引，columns = 标的

### 10.2 权重语义

- 值域 [-1, 1]：正 = 做多，负 = 做空
- **权重 × 杠杆 = 目标敞口相对当前权益的比例**，引擎持续维持
- 总敞口（|权重|按行求和）> 1 时，引擎整行按比例缩放（确定性容错）
- UI 的 `position_size` 作为全局敞口缩放系数（权重 × position_size）
- **缺失语义（两条规则不同）**：
  - 策略返回的行中权重为 NaN → 视为 0（目标空仓）
  - 策略**未返回的行**（索引缺失，如 dropna 副作用）→ 视为「无意见」，
    延续上一行的目标权重；否则缺行会被解释成强制全平，造成平开循环磨损
  - 整列缺失（SYMBOLS 声明了但权重没给该列）→ **报错**：静默补 0
    会把对冲组合无声变成单边裸仓；索引与面板完全不重叠同样报错
- `SYMBOLS` 必须是**大写 USDT 交易对格式**（如 `BTCUSDT`）：策略代码内部
  按 SYMBOLS 原样引用面板键与权重列，生成校验与路由都会强制此格式并去重

**重要推论（恒定杠杆）**：权重恒定 + 杠杆 > 1 时，浮盈会触发加仓、浮亏会触发减仓
（维持目标敞口比例的必然结果）；杠杆 = 1 的满仓权重在期货式账本下自我维持，不产生漂移交易。

**重要推论（position_size 与 v1 的语义差异）**：v1 的 `position_size` 是
入场时一次性保证金比例，此后永不调仓；v2 的 `position_size` 是持续维持的
权重缩放系数——**部分权重（有效敞口 < 满仓）在任何杠杆下都会随价格漂移触发
再平衡**（常数混合）。同一描述被生成为 v1 或 v2，半仓场景结果会不同，
属有意设计（`test_position_size_semantics_differ_by_design` 钉死）。

### 10.3 执行模型

- 权重在当前 K 线收盘确认，下一根 K 线开盘执行（与 v1 相同）
- 对账规则：
  1. 空仓 → 非零权重：必定开仓
  2. 目标权重 = 0：必定全平（不受阈值限制）
  3. 其余调整：|目标权重 − 当前权重| > `rebalance_threshold`（默认 0.01）才交易，防手续费磨损
- 当前权重按决策时点收盘价估值（与决策权益同口径）
- 目标数量按**预期成交价（含滑点）**换算：`目标名义 ÷ (open × (1 ± slippage))`，
  与 v1 的「持仓数量 = 名义 ÷ 成交价」同口径——否则成交后名义敞口会系统性
  超出目标 `(1 ± slippage)` 倍（slippage > 0 的单资产不变量由金样例锁定）
- 部分加仓采用移动平均成本价；反手拆分为「平仓成交 + 开仓成交」两笔
- 手续费/滑点：按每笔成交的名义价值与方向计，费率与 v1 相同

### 10.4 上市时间与缺数据

- 面板对齐取索引并集；标的未上市/缺数据的 K 线为 NaN（**不伪造、不填充**）
- 下一根开盘价为 NaN 的标的本根无法交易，之后自动重试（对账语义）
- 持仓估值在 close 缺失时使用最近有效收盘价

### 10.5 强平（多资产推广，保守模型）

- 盘中最坏情况 = **所有腿同时处于各自最不利极值**（保守假设，写入结果须知）
- 维持保证金 = `maintenance_margin_rate × 名义价值`，与最坏权益**同用最不利价估值**
  （默认 0）；最坏权益 ≤ 维持保证金时全仓强平
- 强平价格按 α 插值求解「权益恰好打到维持线」的价格向量：
  `liq_price = avg_entry + (adverse − avg_entry) × α`，α 解自
  `cash + α × 最坏未实现盈亏 = rate × (入场名义 + α × (最不利名义 − 入场名义))`
- **任意 `maintenance_margin_rate` 下，单资产严格等价于 v1 强平价公式**（金样例交叉验证）

### 10.6 输出结构

| 字段 | 内容 |
|------|------|
| `trades` | 持仓片段（episode）：从建仓到完全平仓为一笔，字段与 v1 trade 兼容（pnl/pnl_pct/holding_hours/...），附 `symbol` |
| `fills` | 逐笔成交：time/symbol/side/action(open/close/liquidation)/qty/price/fee/realized_pnl |
| `equity_curve` | 组合权益（close/worst/best/盘中极值），逐 K 线 |
| `realized_equity_curve` | 已实现权益（cash 账本） |
| `exposure_curve` | 逐 K 线各标的实际敞口权重 |
| `metrics` | 与 v1 同名指标（共享 `backtest_metrics.py`）+ fill_count/symbols 等 |

### 10.7 不变量（金样例锁定）

1. 单资产、全仓、离散权重场景：v2 与 v1 引擎逐根权益曲线、交易盈亏完全一致
   （含 `slippage > 0` 与持仓到结束 end_of_data 的场景）
2. 强平场景：v2 的 α 插值与 v1 强平价公式给出相同数字（含 `maintenance_margin_rate > 0`）
3. `gross_pnl` 两引擎同口径：实际持仓 × raw 价差（不含滑点与手续费）
4. `position_size < 1` 时两引擎语义**有意不同**（v1 一次性比例 / v2 持续再平衡），
   由说明性测试钉死，见 10.2 推论
5. 资金费率 funding（§10.8）：`funding_rates=None`（或全零）时引擎逐根行为与无
   funding 完全一致——上述 1–4 全部金样例 **bit-level 不变**（零影响硬门槛）

### 10.8 资金费率与借币成本（funding / borrow cost，v2.1）

永续合约的持有成本现金流。**默认全关闭**（`funding_rates=None`）；开启时不改
权重/对账/强平几何，仅在持有期内按持仓名义价值逐根扣/加 `cash`，与手续费记账
同构（fee 也只扣 cash），`equity = cash + 未实现盈亏` 自动跟随。

- **入口**：`run(data, funding_rates=...)`（两引擎同名参数）
  - v2：`{symbol: 每根 K 线的资金费率序列}`（与面板索引等长，pd.Series 或数组）
  - v1：单标的的每根费率序列（与输入索引等长）
  - 缺失的标的按 0 处理；NaN 视为该根无费率（0）；长度与 K 线根数不符报错
- **结算口径**：在每根 K 线起点、**MTM 与强平检测之前**结算，使 funding 拖低
  权益后能自然参与本根强平判定（funding 不进强平 α 公式，只改 α 求解的 cash
  起点）。单腿现金流 `funding_cf = -signed_qty × close × rate_i`（`signed_qty`
  正多负空）：**正费率 → 多头付（cash 减）、空头收（cash 增）**（照搬交易所约定）
- **每根费率的来源**：数据层把真实 8h 离散费率序列**连续摊销**到每根 K 线并防
  未来函数（每根取最近一次【已结算】费率，按 `rate × bar秒 / 结算周期秒` 摊销）；
  引擎只消费「每根费率」，对数据来源无感（固定年化近似亦可作同形态输入）
- **单笔 pnl 口径**：funding 作为持有期现金流**计入单笔 `net_pnl`**——v1 经
  `net_pnl = equity_after − entry_equity` 天然含已扣 cash 的 funding；v2 在
  `finish_episode` 显式 `pnl = realized_pnl − fees + funding_cf`，两引擎同口径。
  单笔另出 `funding_pnl` 字段（持有期累计 funding 现金流，负=净付出）
- **gross 神圣不变**：`gross_pnl` 仍为 raw 价差 × 持仓，**不含 funding**
- **输出**：`metrics.total_funding_cost`（累计资金费率净支出，正=净付出）；funding
  经 `equity_close` 自动进收益/回撤/夏普（长持有逐根负漂、夏普下降，即期望行为）
- **借币成本**：v2.1 暂只做 funding；借币（做空/杠杆借入利息）作为同结构的第二
  参数延后

> **金样例**（`tests/test_funding.py`）锁定：零/None 退化、多头付/空头收符号与
> 数值（v1≡v2 逐根一致）、gross 不受 funding 影响、funding 拖入强平（v1≡v2）、
> per-symbol 各腿独立结算、单笔 net_pnl 含 funding。

## 11. 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-11 | v1 | 首次成文：固化既有引擎语义；信号比较语义明确为「与实际持仓对账」；权益曲线覆盖首尾 K 线；策略加载改为内存加载 + 审计留档 |
| 2026-06-11 | v2 | 新增契约 v2：多标的连续目标权重 + 组合引擎 PortfolioBacktestCore + 对齐数据层 data_panel；指标计算抽取为共享模块 backtest_metrics |
| 2026-06-11 | v2 | v2 全链路接入：AI 生成双契约 prompt（CONTRACT_VERSION/SYMBOLS 自描述元数据）、webUI 双引擎路由、组合图表 portfolio_chart；v1 数据加载切换到 data_panel 缓存层 |
| 2026-06-11 | v2 | 审查修复：元数据解析严格化（错误形式报错而非回退 v1）；SYMBOLS 强制 USDT 格式+去重；权重缺行改为「无意见=维持」；两引擎末尾持仓虚拟结算（end_of_data）；拉取目录透传 data_dir；1m 不进缓存；日期过滤下沉 data_panel 共享；黑名单收紧（移除裸词与冗余 import 项） |
| 2026-06-11 | v2 | 第二轮审查修复：v2 目标数量改按含滑点成交价换算（修复隐性超杠杆，slippage>0 跨引擎金样例锁定）；维持保证金两引擎统一为「rate × 名义价值（最不利价估值）」并新增 rate>0 金样例；gross_pnl 统一为 raw 价格口径；元数据解析覆盖注解赋值等全部顶层绑定形式；版本/SYMBOLS 规则单源 validate_strategy_metadata（v1 多标的拒绝而非截断）；权重索引零交集报错；v1 引擎将策略 KeyError 翻译为可行动提示；图表历史切换器改 manifest 方案、序列降采样（>2 万点）、输出文件名加随机后缀防同秒覆盖；数据层先过滤后对齐、清洗后 1m 单槽缓存、多标的并行加载、K 线文件命名单源 Load_real_kline |
| 2026-06-12 | v2 | 第四轮审查修复：target_position 合法值校验移到整数转换之前（0.5 不再被截断成合法 0）；权重缺列报错（不再静默补 0）；权益 ≤ 0 仍允许目标 0 强制平仓；强平 α 兜底改 0（denom≥0 时首次触线在入场价位）；强平后重入语义写入 §5.3 并金样例钉死；1m 单槽缓存改整槽原子存取（修并行加载下的错配毒化）；pandas 2.x 运行时显式开启 CoW；自动拉取失败抛语义化报错且多标的下载改串行；元数据解析拒绝非顶层声明；生成校验复用 validate_strategy_code；图表迁移仅动带 QTBS 标记的文件、逐文件异常隔离且绝不阻断出图；HTML 注入定位单一插入点；成交点降采样封顶并与标记同步过滤；翻译查找/持仓时长口径/metrics 配置回填收敛单源 |
| 2026-06-13 | v2 | 第五轮审查修复（首次全仓扫描）：最大回撤峰值改按最有利权益累计（谷底仍按最不利，修复回撤被系统性低估）；强平 bar 的 equity_worst 落地为结算后权益（修复 < -100% 的不可能回撤）；跳空越过强平价时按本根开盘价结算（两引擎同规则，金样例锁定）；年化收益溢出防护（极短窗口给 inf 不崩溃）；权益穿零后跳过夏普；面板行内混合 NaN 入口报错；SYMBOLS 重复拒绝而非静默去重；行为审查升级（敞口/做空/信号事实改取策略原始输出、成交量变化序列、720 根、执行视角+信号未成交事实、局限性声明）；审查 prompt 随机定界符防注入、JSON 解析健壮化、评分不可解析走审查失败路径；生成检查输出截断、周期黑名单补齐 asfreq/Grouper/分钟别名；下载器时间戳归一化修复（NaT 幽灵行丢弃 + pandas 3 微秒分辨率下毫秒换算错 1000 倍）；引擎参数截断/AI 接入配置/图表标题模板/UI 参数解析收敛单源；回测摘要显示实际数据范围并支持陈旧数据增量补拉 |
| 2026-06-13 | v2 | 第六轮审查修复：data_panel 尾读时间戳剥时区（修带时区遗留 CSV 与 required_end 比较 TypeError 崩溃）；两引擎强平 bar 的 equity_best 也落地结算后权益（消除回撤新口径下的幽灵峰）；load_aligned_panel 的 required_end 缺省派生自 end_date（防新调用方漏传重现陈旧截断）；行为审查 v2 复用引擎透出的 raw_weights 而非二次调用策略（修模块级状态策略的事实漂移）；行为审查引擎参数全钉死；清理死代码（confidence 键/total_return_pct/.resample 冗余项/语言名别名）；指标层 worst/best 单遍提取、v1 open_price 移入强平分支 |
| 2026-06-13 | v2 | 数据更新功能 + 第七轮全仓审查修复：新增 fetch_queue 统一拉取队列（启动自动更新/手动按钮/回测按需，串行+进度区+回测优先暂停）；第七轮重写 fetch_queue 并发模型为「持久 worker + Condition + generation」修一整类竞态（reset 双 worker、lost-wakeup 币种滞留、批次边界 TOCTOU 计数堆叠）；_DIR_OF 取最新目录修同名跨目录串扰；增量补拉 gate 改 end-of-day（与过滤切片同口径，修少近一天数据）；_REFRESHED 标记移到补拉后；启动自动更新去一次性 latch 改 is_running 守卫（首批失败可重试）；代码折叠面板标题随语言切换；删冗余 download_lock；tooltip/默认币种入队收敛单源；契约新增 §9.1 数据更新行为 |
| 2026-06-13 | v2 | 更新与回测并行（原子写方案）：下载器改用 atomic_write_csv（.staging 暂存区写完整文件 + os.replace 原子覆盖 + Windows 占用重试），回测读文件永远完整、与后台更新真正并行；增量补拉改非阻塞后台 enqueue（回测对已有数据币种立即开跑，下次取更新后数据），仅本地完全无数据时才阻塞等首次拉取；移除回测优先暂停机制（_PRIORITY/_PAUSE_DEPTH，原子写后不再需要）；fetch_blocking 保留插队首优先；新增原子写并发安全测试（多读者+持续覆写零半截） |
| 2026-06-13 | v1 | v1 引擎回测窗口锚定输入索引：`CodeBacktestCore.run` 在 `strategy_func` 返回后按【输入】索引重对齐（`reindex(input_index)`），修复策略 `dropna`/缩短行**静默截断回测窗口**（equity_curve 只覆盖剩余根数，与 webUI 取自完整输入索引的 kline_count/起止时间静默错配）；target_position 缺行按 `ffill().fillna(0)` 延续（与 v2 §10.2 同口径），行情列始终取自输入帧不被 ffill 造假，索引零交集报错（与 v2 _prepare_weights 一致）；返回帧等长同序时重对齐为无操作，单资产 v1≡v2/强平 α 退化/end_of_data/slippage>0 等金样例逐根不变；契约 §3 补「回测窗口锚定输入索引」引擎层承诺 |
| 2026-06-14 | v2.1 | 资金费率（funding）引擎支持：两引擎 `run(funding_rates=...)` 接收 per-symbol 每根费率序列，在 MTM/强平之前按 `-signed_qty×close×rate` 逐根扣/加 cash（正费率多头付空头收），纳入强平判定（不进 α 公式、只改 cash 起点）；funding 计入单笔 net_pnl（v1 天然含、v2 finish_episode 显式加 funding_cf），gross 仍为 raw 价差不含 funding；metrics 新增 total_funding_cost、单笔新增 funding_pnl；`funding_rates=None`/全零时逐根 bit-level 退化为无 funding（现有金样例零改动通过）；契约 §10.8 + §8.2 + §9（v2.1）+ §10.7 不变量 5 + `tests/test_funding.py` 9 条金样例 |
| 2026-06-14 | v2.1 | funding 真实数据读取管线（Stage B）：`cryptocurrency_data/funding_rate_data.py` 下载器（`futures_funding_rate` 手动分页 + 续传 + 原子写 `funding_data/{SYMBOL}_FUNDING.csv`）；`data_panel.load_funding_series`（merge_asof backward 防未来函数 + 按 bar/结算周期连续摊销）+ `build_funding_rates`；webUI 自动接线（本地有费率即按 per-bar 计入，无则不计）。**funding 走被动读取，依赖手动运行下载器保持新鲜——与 K 线的自动增量补拉不对称（有意，自动拉取留后续）**。`tests/test_funding_data.py` 8 条金样例 |
| 2026-06-14 | v2.1 | 第九轮审查加固：exec 沙箱 AST 新增拒绝 pandas/numpy 文件/网络 I/O 方法（read_csv/read_pickle/to_csv/np.savetxt 等，封堵任意文件读写/反序列化 RCE/SSRF，§6 记录边界）；funding 回测窗口超本地覆盖时尾部退化为 0（不再无限前向外推末费率，与首段对称）；funding 单根 index 摊销因子兜底为一个结算周期（不再静默清零）；损坏 funding CSV 降级为不计 funding（不中断回测）；load_funding_series 对 tz-aware/重复时间戳 index 防御；funding_records_to_df schema 漂移退化空表；webUI 回测摘要展示 metrics.total_funding_cost（六语言）。studio 重构遗留的两处前端清理（死 header 文案、区块标题/侧栏未随语言切换）为纯 UI 维护项，留用户在其 studio 代码中处置；funding 自动补拉为产品决策，本轮以文档声明被动读取语义。267+ tests green |
