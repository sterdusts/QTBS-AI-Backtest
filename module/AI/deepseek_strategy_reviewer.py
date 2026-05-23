import os
import json
from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
REVIEW_MODEL = "deepseek-v4-pro"


def _extract_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(f"AI审查结果不是合法JSON：{text}")

    return json.loads(text[start:end + 1])


def _clamp_score(value) -> float:
    try:
        value = float(value)
    except Exception:
        value = 0.0

    return max(0.0, min(99.99, value))


def review_strategy_code_with_deepseek(
    user_strategy_text: str,
    generated_code: str,
    language: str = "zh",
) -> dict:
    """
    用 DeepSeek V4 Pro 审查策略匹配度：
    只判断生成代码是否符合用户的自然语言策略描述。
    """

    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    system_prompt = """
你是一个量化策略代码审查员。

你的任务不是生成策略代码，而是审查：
生成代码是否符合用户的自然语言策略描述。

你只评估“策略匹配度”，不要评估最终回测结果是否可信。
不要输出可置信度。
不要输出 confidence_score。
不要输出 confidence_summary。

你必须只输出 JSON，不要输出 markdown，不要输出解释性正文。
"""

    user_prompt = f"""
请审查下面的量化策略代码。

【用户原始策略描述】
{user_strategy_text}

【AI生成的策略代码】
{generated_code}

请只返回 JSON，格式必须完全如下：

{{
  "match_score": 0-99.99,
  "match_summary": "一句话说明代码和用户描述的匹配情况"
}}

评分标准：

match_score：
分数范围是 0 到 99.99。
不要输出 100。
即使完全正确，最高也只能给 99.99。

- 90-99.99：几乎完全符合用户描述
- 80-89：初步可用，建议进入回测验证
- 70-79：大体符合，但有明显自动补充或局部偏差
- 50-69：只符合大方向，细节偏差明显
- 0-49：明显不符合用户描述

重点检查：
- 入场条件是否符合用户描述
- 出场条件是否符合用户描述
- 多空方向是否符合用户描述
- 指标是否符合用户描述
- 是否擅自加入用户没有要求的重要逻辑
- 是否漏掉用户明确要求的条件
- 是否把“平仓”误写成“反手”
- 是否把“只做多/只做空/多空都做”理解错
- 是否把用户的风控、过滤、止盈止损、仓位描述理解错

语言要求：
- 如果 language="{language}"，match_summary 使用对应语言。
- JSON 字段名必须保持英文。
"""

    response = client.chat.completions.create(
        model=REVIEW_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    data = _extract_json(content)

    return {
        "match_score": _clamp_score(data.get("match_score", 0)),
        "match_summary": str(data.get("match_summary", "")),
    }