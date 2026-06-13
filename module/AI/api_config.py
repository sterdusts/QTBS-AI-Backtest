"""
DeepSeek 接入配置单源（生成器与审查器共用）。

此前 .env 加载、API Key 读取、语言显示名、评分截断在两个模块各写一份
且策略相反（一个 import 时缓存、一个调用时才读），出现过「生成成功、
审查失败」的不对称故障面——所有接入配置只在这里定义。
"""

import os

from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV_PATH = os.path.join(BASE_DIR, ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 语言代码 → AI 输出语言显示名（webUI 生成与审查共用同一份）
LANGUAGE_DISPLAY_NAMES = {
    "zh": "简体中文",
    "en": "English",
    "ko": "한국어",
    "ja": "日本語",
    "ar": "العربية",
    "ru": "Русский",
}


def get_api_key() -> str:
    """
    调用时加载 .env 并读取 key：不依赖任何模块的 import 副作用
    （否则只 import 生成器的脚本会在 .env 配置正确时仍然报缺 key）。
    """

    load_dotenv(ENV_PATH)
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "没有读取到 DEEPSEEK_API_KEY：请在项目根目录的 .env 文件中配置"
        )

    return api_key


def make_client() -> OpenAI:
    return OpenAI(api_key=get_api_key(), base_url=DEEPSEEK_BASE_URL)


def clamp_score(value) -> float:
    """评分截断到 [0, 99.99]（上限策略单源：满分也不给 100）。"""

    try:
        value = float(value)
    except Exception:
        value = 0.0

    return max(0.0, min(99.99, value))
