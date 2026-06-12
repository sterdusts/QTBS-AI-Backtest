"""
统一的时间戳输出文件名构造（策略审计留档、图表 HTML 共用）。

带 8 位随机后缀：同一秒内的并发生成（自动化批跑/双开 webUI）
也不会互相覆盖。
"""

import uuid
from datetime import datetime, timezone


def build_timestamped_filename(prefix: str, extension: str) -> str:
    utc_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_UTC")
    return f"{prefix}_{utc_timestamp}_{uuid.uuid4().hex[:8]}{extension}"
