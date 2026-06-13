[English](#english-version) | [中文](#中文版本) | [한국어](#한국어-버전)

---

========================================================================

# English Version

## QTBS AI Quant Strategy Frontend

AI-Powered Natural Language Quantitative Strategy Backtesting Platform

---

## Overview

QTBS is an AI-assisted quantitative strategy backtesting platform designed for ordinary users.

Users do not need to write Python code or build quantitative infrastructures.

By simply describing a trading strategy in natural language, QTBS can:

- Generate executable strategy code
- Run historical backtests
- Visualize trading behavior
- Analyze whether the strategy implementation matches the user’s intent
- Review potential risks and logic problems

The core goal of QTBS is:

> Reduce the barrier of quantitative strategy validation and improve transparency in AI-generated trading systems.

---

## Why QTBS

The internet is full of:

- “High win-rate strategies”
- “Profitable systems”
- “Indicator combinations”
- “Trading tutorials”

However, most ordinary users:

- Cannot verify whether strategies are truly effective
- Cannot write Python backtesting code
- Cannot build quantitative environments
- Cannot judge whether AI-generated code is logically correct

As a result:

> Strategy validation remains difficult, expensive, and opaque.

QTBS attempts to solve this problem through:

- AI-generated strategy code
- Explainable backtesting
- Visualization
- AI strategy auditing

---

## Core Features

### Natural Language → Strategy Code

Example input:

```text
Go long when EMA12 crosses above EMA26.
Close the position on a dead cross.
```

QTBS automatically generates executable Python strategy functions.

---

### Multi-Asset Portfolio Backtesting

QTBS also understands multi-asset strategies described in natural language, for example:

```text
Compare the recent momentum of BTC and ETH.
When BTC is clearly stronger, long BTC and short ETH with half capital each;
when ETH is stronger, do the reverse; stay flat otherwise.
```

- The AI automatically selects the right strategy contract: single-asset discrete positions, or multi-asset continuous target weights (hedge / portfolio / dynamic sizing)
- The portfolio engine handles weight reconciliation, rebalancing thresholds, average-cost accounting and conservative liquidation modeling
- Parameter precedence: when the strategy code declares its symbols (`SYMBOLS`), the code wins and the panel's symbol field is ignored; timeframe, date range, capital, leverage, position size, fees and slippage always come from the panel
- Engine semantics are specified in `STRATEGY_CONTRACT.md` and locked by golden-sample tests under `tests/`

---

### AI Strategy Review

QTBS automatically analyzes:

- Whether the generated strategy matches the user’s description
- Whether future data leakage exists
- Whether unnecessary logic has been added
- Potential trading risks

And generates:

- Match score
- Risk explanations
- Strategy implementation notes

Before the AI review, QTBS runs a **behavior check**: the generated code is
actually executed by the engine on synthetic K-lines (uptrend / downtrend /
range). Runtime errors are caught up front, and the observed facts (trade count,
direction, exposure) are fed to the reviewer — so the score reflects what the
code *does*, not just how it reads.

---

### Automatic Data Management

K-line data is kept up to date automatically:

- On startup QTBS scans locally available symbols and updates them in the
  background; first use downloads a default set (BTC/ETH).
- A manual **Update Data** button and a progress panel (total progress, current
  symbol, hover list) sit next to the language selector.
- Updates use an **atomic write** (staging file + atomic replace), so backtests
  always read a complete file and run **in parallel** with ongoing updates — no
  blocking, no half-written data.

---

### Visualized Backtesting

QTBS automatically generates:

- Candlestick charts
- Entry / exit markers
- Floating equity curves
- Realized equity curves
- Volume charts
- Position curves

For multi-asset strategies, QTBS renders a portfolio view instead:

- Portfolio equity (floating + realized)
- Per-symbol exposure curves
- Normalized price comparison with buy / sell / liquidation markers

All charts include a history switcher, and very long backtests are automatically downsampled for smooth rendering.

This allows users to visually verify trading behavior and strategy execution logic.

---

### Multi-language Support

Currently supported languages:

- English
- 中文
- 한국어
- 日本語
- Русский
- العربية

---

## Tech Stack

### Frontend

- Gradio
- HTML
- CSS
- JavaScript

### AI System

- DeepSeek API
- Natural language strategy parsing
- AI strategy auditing

### Backtesting Engine

- Python
- Pandas
- NumPy

### Visualization

- Pyecharts
- Apache ECharts

---

## Environment Setup

Before running the project, create a `.env` file in the project root directory.

Example:

```env
DEEPSEEK_API_KEY=your_api_key_here
```

Current AI Provider:

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
```

Currently, QTBS only supports the DeepSeek API.

Get your API key from:

https://platform.deepseek.com/

---

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the project:

```bash
python webUI.py
```

On Windows you can simply double-click `QTBS launcher.bat` — it creates the
virtual environment and installs dependencies automatically on first run.

Run the test suite (golden-sample tests locking the engine semantics):

```bash
python -m pytest tests -q
```

---

## Important Notes

- `.env` is not included in this repository for security reasons
- Never upload API keys to GitHub
- Make sure `.env` is added to `.gitignore`

Example:

```gitignore
.env
```

---

## Current Status

Current version already supports:

- Natural-language strategy generation
- Historical backtesting (single-asset and multi-asset portfolio engines)
- Hedge / portfolio strategies via continuous target weights
- Behavioral strategy review (runs generated code on synthetic data before the AI review)
- Automatic K-line data update (atomic write; backtests run in parallel with updates)
- Visualized chart systems (incl. portfolio view)
- AI strategy match analysis
- Multi-language UI support
- A written AI↔engine strategy contract (`STRATEGY_CONTRACT.md`) locked by 190+ tests

---

## Future Plans

Planned future features:

- Intrabar trigger prices (stop-loss / take-profit at intraday levels)
- Funding rates and borrowing costs
- Risk / robustness analysis modules (parameter sensitivity, in/out-of-sample)
- Multi-strategy portfolios
- AI-driven strategy optimization
- Local model deployment
- More OpenAI-compatible API providers

---

## Project Positioning

QTBS is not intended to compete with institutional-grade quantitative trading platforms.

It is currently positioned as:

- AI-assisted strategy verification tool
- Educational quant platform
- Lightweight quantitative analysis platform for ordinary users

QTBS focuses heavily on:

- Transparency
- Explainability
- Verification
- Visualization
- AI-assisted auditing

---

## Philosophy

QTBS does not simply output backtest results.

It also attempts to answer:

- Why was a position opened?
- Why was a position closed?
- Did the AI truly understand the user’s intent?
- Does the generated strategy actually match the description?

The project emphasizes:

> “Determinism and transparency in AI-generated trading systems.”

---

## License

MIT License

---

## Disclaimer

This project is for research and educational purposes only.

QTBS does not provide financial advice or investment guarantees.

All trading strategies involve risk.




# 中文版本

# QTBS AI Quant Strategy Frontend

AI 驱动的自然语言量化策略回测平台

---

# 项目简介

QTBS 是一个面向普通用户的 AI 辅助量化策略回测平台。

用户无需编写 Python 代码，也无需搭建复杂量化环境。

只需输入自然语言描述，QTBS 即可：

- 自动生成量化策略代码
- 自动运行历史回测
- 自动生成可视化图表
- 自动分析策略实现是否符合用户意图
- 自动审查潜在风险与逻辑问题

QTBS 的核心目标是：

> 降低量化策略验证门槛，提高 AI 生成策略的透明度与可解释性。

---

# 为什么做 QTBS

当前互联网充斥着：

- “高胜率策略”
- “稳赚系统”
- “指标组合”
- “量化教程”

但绝大多数普通用户：

- 无法验证策略是否真实有效
- 不会编写 Python 回测代码
- 不会搭建量化环境
- 无法判断 AI 是否正确实现策略逻辑

因此：

> 策略验证始终是一个高门槛、高成本、低透明度的问题。

QTBS 希望通过：

- AI 自动生成策略代码
- 可解释回测
- 图表可视化
- AI 策略审查

降低用户的验证成本。

---

# 核心功能

## 自然语言生成策略代码

示例输入：

```text
EMA12 上穿 EMA26 做多，
死叉平仓。
```

QTBS 会自动生成可执行的 Python 策略函数。

---

## 多标的组合回测

QTBS 也能理解自然语言描述的多标的策略，例如：

```text
比较 BTC 和 ETH 最近的动量强弱。
BTC 明显更强时各用一半资金做多 BTC、做空 ETH；
ETH 更强时反向操作；强弱接近时空仓等待。
```

- AI 自动选择合适的策略契约：单标的离散仓位，或多标的连续目标权重（对冲 / 组合 / 动态仓位）
- 组合引擎负责权重对账、再平衡阈值、移动平均成本记账与保守强平模型
- 参数优先级：策略代码声明了标的（`SYMBOLS`）时以代码为准、面板的交易标的不生效；周期、回测时间、资金、杠杆、仓位、费率、滑点始终以面板为准
- 引擎语义由根目录 `STRATEGY_CONTRACT.md` 规约，并由 `tests/` 金样例测试锁定

---

## AI 策略审查

QTBS 会自动分析：

- 是否符合用户描述
- 是否存在未来函数
- 是否存在多余逻辑
- 是否存在潜在风险

并生成：

- 策略匹配度
- 风险说明
- 策略实现解释

在交给 AI 审查之前，QTBS 会先做一次**行为检查**：把生成的代码用引擎在合成
K 线（上涨 / 下跌 / 震荡）上真正跑一遍。运行时错误在此被提前拦截，实际行为
（交易笔数、方向、敞口）也喂给审查 AI——所以评分反映的是代码**实际做了什么**，
而不只是代码字面。

---

## 自动数据管理

K 线数据自动保持最新：

- 启动时扫描本地已有交易对并在后台更新；首次使用拉取默认币种（BTC/ETH）。
- 语言选择旁边有**更新数据**手动按钮和进度区（总进度、当前币种、悬停清单）。
- 更新采用**原子写**（暂存文件 + 原子替换），回测永远读到完整文件，可与更新
  **并行**进行——不阻塞、不会读到写了一半的数据。

---

## 可视化回测系统

QTBS 自动生成：

- K线图
- 开仓 / 平仓点
- 实时权益曲线
- 已实现权益曲线
- 成交量图
- 仓位曲线

多标的策略则渲染组合视图：

- 组合权益（实时 + 已实现）
- 各标的敞口曲线
- 归一化价格对比与买入 / 卖出 / 强平标记

所有图表带历史切换器，超长回测自动降采样保证流畅渲染。

帮助用户直观验证策略行为与交易逻辑。

---

## 多语言支持

当前支持：

- English
- 中文
- 한국어
- 日本語
- Русский
- العربية

---

# 技术栈

## 前端

- Gradio
- HTML
- CSS
- JavaScript

## AI 系统

- DeepSeek API
- 自然语言策略解析
- AI 策略审查

## 回测核心

- Python
- Pandas
- NumPy

## 图表系统

- Pyecharts
- Apache ECharts

---

# 环境配置

运行项目之前，需要在项目根目录创建 `.env` 文件。

示例：

```env
DEEPSEEK_API_KEY=your_api_key_here
```

当前 AI 提供商：

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
```

当前 QTBS 仅支持 DeepSeek API。

获取 API Key：

https://platform.deepseek.com/

---

# 安装方式

安装依赖：

```bash
pip install -r requirements.txt
```

运行项目：

```bash
python webUI.py
```

Windows 下也可以直接双击 `QTBS launcher.bat`——首次运行会自动创建虚拟环境并安装依赖。

运行测试（锁定引擎语义的金样例测试）：

```bash
python -m pytest tests -q
```

---

# 注意事项

- `.env` 文件不会上传到 GitHub
- 请勿公开你的 API Key
- 请确保 `.env` 已加入 `.gitignore`

示例：

```gitignore
.env
```

---

# 当前开发状态

当前版本已支持：

- 自然语言生成策略
- 历史回测（单标的与多标的组合双引擎）
- 连续目标权重的对冲 / 组合策略
- 行为审查（生成代码先在合成数据上实际运行再交给 AI 审查）
- 自动 K 线数据更新（原子写，回测与更新并行）
- 图表可视化（含组合视图）
- AI 策略匹配分析
- 多语言 UI
- 成文的 AI↔引擎策略契约（`STRATEGY_CONTRACT.md`），190+ 测试锁定

---

# 后续计划

未来计划包括：

- 盘中触发价（止损 / 止盈按盘中价位成交）
- 资金费率与借币成本
- 风险 / 稳健性分析模块（参数敏感性、样本内外）
- 多策略组合
- AI 自动优化策略
- 本地模型部署
- 更多 OpenAI Compatible API 支持

---

# 项目定位

QTBS 并不是机构级量化平台。

当前更偏向：

- AI 辅助策略验证工具
- 教学与学习平台
- 面向普通用户的轻量量化平台

QTBS 更强调：

- 可验证性
- 可解释性
- 可视化
- AI 审查能力

---

# 项目理念

QTBS 不仅输出回测结果。

它还试图回答：

- 为什么在这里开仓？
- 为什么在这里平仓？
- AI 是否真正理解了用户意图？
- 生成的策略是否真的符合描述？

项目核心强调：

> “AI 生成交易系统中的确定性与透明度。”

---

# License

MIT License

---

# Disclaimer

本项目仅用于研究与学习目的。

QTBS 不提供任何投资建议或收益保证。

所有交易策略均存在风险。




# 한국어 버전

# QTBS AI Quant Strategy Frontend

AI 기반 자연어 퀀트 전략 백테스트 플랫폼

---

# 프로젝트 소개

QTBS는 일반 사용자를 위한 AI 기반 퀀트 전략 백테스트 플랫폼입니다.

사용자는 Python 코드를 작성하거나 복잡한 퀀트 환경을 구축할 필요가 없습니다.

자연어로 전략을 설명하기만 하면 QTBS는 다음 기능을 제공합니다.

- 자동 전략 코드 생성
- 자동 과거 데이터 백테스트
- 자동 시각화 차트 생성
- 전략 구현이 사용자 의도와 일치하는지 분석
- 잠재적 리스크 및 로직 문제 검토

QTBS의 핵심 목표는:

> 퀀트 전략 검증의 진입 장벽을 낮추고 AI 생성 전략의 투명성과 설명 가능성을 높이는 것입니다.

---

# 왜 QTBS인가

현재 인터넷에는 수많은:

- “고승률 전략”
- “수익 보장 시스템”
- “지표 조합”
- “퀀트 강의”

가 존재합니다.

하지만 대부분의 일반 사용자는:

- 전략의 실제 효과를 검증할 수 없고
- Python 백테스트 코드를 작성할 수 없으며
- 퀀트 환경을 구축할 수 없고
- AI가 전략을 올바르게 구현했는지 판단하기 어렵습니다.

결과적으로:

> 전략 검증은 여전히 높은 비용과 낮은 투명성을 가진 영역입니다.

QTBS는 다음을 통해 이 문제를 해결하고자 합니다.

- AI 전략 코드 생성
- 설명 가능한 백테스트
- 시각화
- AI 전략 감사

---

# 핵심 기능

## 자연어 → 전략 코드 생성

예시 입력:

```text
EMA12가 EMA26을 상향 돌파하면 매수,
데드크로스 발생 시 청산.
```

QTBS는 실행 가능한 Python 전략 함수를 자동 생성합니다.

---

## 멀티 종목 포트폴리오 백테스트

QTBS는 자연어로 설명한 멀티 종목 전략도 이해합니다. 예시:

```text
BTC와 ETH의 최근 모멘텀을 비교한다.
BTC가 확실히 강하면 자금의 절반씩 BTC 매수 / ETH 공매도,
ETH가 강하면 반대로, 비슷하면 전량 청산 후 대기.
```

- AI가 적합한 전략 계약을 자동 선택: 단일 종목 이산 포지션, 또는 멀티 종목 연속 목표 가중치(헤지 / 포트폴리오 / 동적 포지션)
- 포트폴리오 엔진이 가중치 정산, 리밸런싱 임계값, 평균 단가 회계, 보수적 청산 모델을 처리
- 파라미터 우선순위: 전략 코드에 종목(`SYMBOLS`)이 선언되면 코드가 우선하며 패널의 종목 입력은 무시됩니다. 주기·기간·자금·레버리지·포지션 비율·수수료·슬리피지는 항상 패널 기준입니다
- 엔진 의미론은 루트의 `STRATEGY_CONTRACT.md`에 명세되어 있고 `tests/`의 골든 샘플 테스트로 고정됩니다

---

## AI 전략 검토

QTBS는 자동으로 분석합니다.

- 사용자 설명과 일치하는지
- 미래 함수(Future Leakage)가 존재하는지
- 불필요한 로직이 추가되었는지
- 잠재적 리스크가 존재하는지

그리고 다음 결과를 제공합니다.

- 전략 일치도
- 리스크 설명
- 전략 구현 설명

AI 검토 전에 QTBS는 **행동 검사**를 수행합니다. 생성된 코드를 합성 K라인
(상승 / 하락 / 횡보)에서 엔진으로 실제 실행해 봅니다. 런타임 오류를 미리
잡아내고, 관측된 사실(거래 수, 방향, 노출)을 검토 AI에 전달합니다. 따라서
점수는 코드가 어떻게 보이는지가 아니라 **실제로 무엇을 하는지**를 반영합니다.

---

## 자동 데이터 관리

K라인 데이터는 자동으로 최신 상태로 유지됩니다.

- 시작 시 로컬 종목을 스캔하여 백그라운드에서 업데이트하며, 최초 사용 시
  기본 종목(BTC/ETH)을 다운로드합니다.
- 언어 선택 옆에 **데이터 업데이트** 수동 버튼과 진행 패널(전체 진행률,
  현재 종목, 호버 목록)이 있습니다.
- 업데이트는 **원자적 쓰기**(스테이징 파일 + 원자적 교체)를 사용하므로
  백테스트는 항상 완전한 파일을 읽고 업데이트와 **병렬로** 실행됩니다 —
  차단 없음, 절반만 쓰인 데이터 없음.

---

## 시각화 백테스트 시스템

QTBS는 자동으로 생성합니다.

- 캔들 차트
- 진입 / 청산 포인트
- 실시간 손익 곡선
- 실현 손익 곡선
- 거래량 차트
- 포지션 곡선

멀티 종목 전략은 포트폴리오 뷰로 렌더링됩니다.

- 포트폴리오 자산 곡선(실시간 + 실현)
- 종목별 노출 곡선
- 정규화 가격 비교와 매수 / 매도 / 청산 마커

모든 차트에 히스토리 전환기가 포함되며, 매우 긴 백테스트는 자동 다운샘플링으로 부드럽게 렌더링됩니다.

사용자는 이를 통해 전략 동작을 직관적으로 검증할 수 있습니다.

---

## 다국어 지원

현재 지원 언어:

- English
- 中文
- 한국어
- 日本語
- Русский
- العربية

---

# 기술 스택

## 프론트엔드

- Gradio
- HTML
- CSS
- JavaScript

## AI 시스템

- DeepSeek API
- 자연어 전략 분석
- AI 전략 감사

## 백테스트 엔진

- Python
- Pandas
- NumPy

## 시각화 시스템

- Pyecharts
- Apache ECharts

---

# 환경 설정

프로젝트 실행 전, 프로젝트 루트 디렉토리에 `.env` 파일을 생성해야 합니다.

예시:

```env
DEEPSEEK_API_KEY=your_api_key_here
```

현재 AI Provider:

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
```

현재 QTBS는 DeepSeek API만 지원합니다.

API Key 발급:

https://platform.deepseek.com/

---

# 설치 방법

패키지 설치:

```bash
pip install -r requirements.txt
```

프로젝트 실행:

```bash
python webUI.py
```

Windows에서는 `QTBS launcher.bat`를 더블클릭하면 됩니다. 첫 실행 시 가상환경 생성과 의존성 설치가 자동으로 진행됩니다.

테스트 실행(엔진 의미론을 고정하는 골든 샘플 테스트):

```bash
python -m pytest tests -q
```

---

# 주의 사항

- `.env` 파일은 GitHub에 업로드되지 않습니다.
- API Key를 공개하지 마십시오.
- `.env`를 `.gitignore`에 추가하십시오.

예시:

```gitignore
.env
```

---

# 현재 개발 상태

현재 지원 기능:

- 자연어 전략 생성
- 과거 데이터 백테스트(단일 종목 + 멀티 종목 포트폴리오 듀얼 엔진)
- 연속 목표 가중치 기반 헤지 / 포트폴리오 전략
- 행동 검증(생성 코드를 합성 데이터에서 실제 실행 후 AI 검토)
- 자동 K라인 데이터 업데이트(원자적 쓰기, 백테스트와 업데이트 병렬)
- 시각화 차트 시스템(포트폴리오 뷰 포함)
- AI 전략 일치도 분석
- 다국어 UI 지원
- 문서화된 AI↔엔진 전략 계약(`STRATEGY_CONTRACT.md`)과 190+ 테스트

---

# 향후 계획

향후 추가 예정 기능:

- 장중 트리거 가격(손절 / 익절의 장중 체결)
- 펀딩비 및 차입 비용
- 리스크 / 강건성 분석 모듈(파라미터 민감도, 표본 내외)
- 멀티 전략 포트폴리오
- AI 기반 전략 자동 최적화
- 로컬 모델 배포
- 추가 OpenAI Compatible API 지원

---

# 프로젝트 포지셔닝

QTBS는 기관급 퀀트 플랫폼을 목표로 하지 않습니다.

현재는 다음과 같은 방향에 가깝습니다.

- AI 기반 전략 검증 도구
- 교육 및 학습 플랫폼
- 일반 사용자를 위한 경량 퀀트 플랫폼

QTBS는 특히 다음 요소를 중요하게 생각합니다.

- 검증 가능성
- 설명 가능성
- 시각화
- AI 감사 기능

---

# 프로젝트 철학

QTBS는 단순히 결과만 출력하지 않습니다.

또한 다음 질문에 답하려고 시도합니다.

- 왜 여기서 진입했는가?
- 왜 여기서 청산했는가?
- AI가 사용자 의도를 제대로 이해했는가?
- 생성된 전략이 설명과 실제로 일치하는가?

QTBS가 강조하는 핵심은:

> “AI 생성 거래 시스템의 투명성과 결정성”

입니다.

---

# License

MIT License

---

# Disclaimer

본 프로젝트는 연구 및 학습 목적용입니다.

QTBS는 투자 조언이나 수익 보장을 제공하지 않습니다.

모든 거래 전략에는 리스크가 존재합니다.
