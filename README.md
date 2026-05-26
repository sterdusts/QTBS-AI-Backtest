[English](#english-version) | [中文](#中文版本) | [한국어](#한국어-버전)

---

========================================================================

# English Version


\# QTBS AI Quant Strategy Frontend



AI-Powered Natural Language Quantitative Strategy Backtesting Platform



\---



\# Overview



QTBS is an AI-assisted quantitative strategy backtesting platform designed for ordinary users.



Users do not need to write Python code or build quantitative infrastructures.



By simply describing a trading strategy in natural language, QTBS can:



\- Generate executable strategy code

\- Run historical backtests

\- Visualize trading behavior

\- Analyze whether the strategy implementation matches the user’s intent

\- Review potential risks and logic problems



The core goal of QTBS is:



> Reduce the barrier of quantitative strategy validation and improve transparency in AI-generated trading systems.



\---



\# Why QTBS



The internet is full of:



\- “High win-rate strategies”

\- “Profitable systems”

\- “Indicator combinations”

\- “Trading tutorials”



However, most ordinary users:



\- Cannot verify whether strategies are truly effective

\- Cannot write Python backtesting code

\- Cannot build quantitative environments

\- Cannot judge whether AI-generated code is logically correct



As a result:



> Strategy validation remains difficult, expensive, and opaque.



QTBS attempts to solve this problem through:



\- AI-generated strategy code

\- Explainable backtesting

\- Visualization

\- AI strategy auditing



\---



\# Core Features



\## Natural Language → Strategy Code



Example input:



```text

Go long when EMA12 crosses above EMA26.

Close the position on a dead cross.

```



QTBS automatically generates executable Python strategy functions.



\---



\## AI Strategy Review



QTBS automatically analyzes:



\- Whether the generated strategy matches the user’s description

\- Whether future data leakage exists

\- Whether unnecessary logic has been added

\- Potential trading risks



And generates:



\- Match score

\- Risk explanations

\- Strategy implementation notes



\---



\## Visualized Backtesting



QTBS automatically generates:



\- Candlestick charts

\- Entry / exit markers

\- Floating equity curves

\- Realized equity curves

\- Volume charts

\- Position curves



This allows users to visually verify trading behavior and strategy execution logic.



\---



\## Multi-language Support



Currently supported languages:



\- English

\- 中文

\- 한국어

\- 日本語

\- Русский

\- العربية



\---



\# Tech Stack



\## Frontend



\- Gradio

\- HTML

\- CSS

\- JavaScript



\## AI System



\- DeepSeek API

\- Natural language strategy parsing

\- AI strategy auditing



\## Backtesting Engine



\- Python

\- Pandas

\- NumPy



\## Visualization



\- Pyecharts

\- Apache ECharts



\---



\# Environment Setup



Before running the project, create a `.env` file in the project root directory.



Example:



```env

DEEPSEEK\_API\_KEY=your\_api\_key\_here

```



Current AI Provider:



```python

DEEPSEEK\_BASE\_URL = "https://api.deepseek.com"

MODEL\_NAME = "deepseek-chat"

```



Currently, QTBS only supports the DeepSeek API.



Get your API key from:



https://platform.deepseek.com/



\---



\# Installation



Install dependencies:



```bash

pip install -r requirements.txt

```



Run the project:



```bash

python webUI.py

```



\---



\# Important Notes



\- `.env` is not included in this repository for security reasons

\- Never upload API keys to GitHub

\- Make sure `.env` is added to `.gitignore`



Example:



```gitignore

.env

```



\---



\# Current Status



Current version already supports:



\- Natural-language strategy generation

\- Historical backtesting

\- Visualized chart systems

\- AI strategy match analysis

\- Multi-language UI support



\---



\# Future Plans



Planned future features:



\- Advanced position management systems

\- Multi-strategy portfolios

\- AI-driven strategy optimization

\- Risk analysis modules

\- Local model deployment

\- More OpenAI-compatible API providers



\---



\# Project Positioning



QTBS is not intended to compete with institutional-grade quantitative trading platforms.



It is currently positioned as:



\- AI-assisted strategy verification tool

\- Educational quant platform

\- Lightweight quantitative analysis platform for ordinary users



QTBS focuses heavily on:



\- Transparency

\- Explainability

\- Verification

\- Visualization

\- AI-assisted auditing



\---



\# Philosophy



QTBS does not simply output backtest results.



It also attempts to answer:



\- Why was a position opened?

\- Why was a position closed?

\- Did the AI truly understand the user’s intent?

\- Does the generated strategy actually match the description?



The project emphasizes:



> “Determinism and transparency in AI-generated trading systems.”



\---



\# License



MIT License



\---



\# Disclaimer



This project is for research and educational purposes only.



QTBS does not provide financial advice or investment guarantees.



All trading strategies involve risk.

=========================================================================================

=========================================================================================

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

---

## 可视化回测系统

QTBS 自动生成：

- K线图
- 开仓 / 平仓点
- 实时权益曲线
- 已实现权益曲线
- 成交量图
- 仓位曲线

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
- 历史回测
- 图表可视化
- AI 策略匹配分析
- 多语言 UI

---

# 后续计划

未来计划包括：

- 更复杂的仓位系统
- 多策略组合
- AI 自动优化策略
- 风险分析模块
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

=========================================================================================

=========================================================================================

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

---

## 시각화 백테스트 시스템

QTBS는 자동으로 생성합니다.

- 캔들 차트
- 진입 / 청산 포인트
- 실시간 손익 곡선
- 실현 손익 곡선
- 거래량 차트
- 포지션 곡선

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
- 과거 데이터 백테스트
- 시각화 차트 시스템
- AI 전략 일치도 분석
- 다국어 UI 지원

---

# 향후 계획

향후 추가 예정 기능:

- 고급 포지션 관리 시스템
- 멀티 전략 포트폴리오
- AI 기반 전략 자동 최적화
- 리스크 분석 모듈
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
