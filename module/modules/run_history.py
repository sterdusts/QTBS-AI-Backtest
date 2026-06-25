"""
回测运行历史记录。

每次回测把【输入提示词 + 策略代码 + 参数 + 关键指标 + 图表文件路径】落成一份
自包含 JSON，便于事后追溯/复现，也为后续「历史查看」功能打基础（列目录即可枚举
全部历史，每条记录自带还原一次回测所需的全部信息）。

纯库、不依赖 Gradio。一次写一个带 UTC 时间戳 + 随机后缀的文件，不覆盖历史、并发安全。
JSON 严格合法（inf/NaN 归一为 null），方便将来任何前端/脚本直接解析。
"""

import json
import math
import os

from module.modules.file_naming import build_timestamped_filename

# 历史记录目录（与图表 Past_data、代码留档 Past_data/strategy_code 同根）
RUN_HISTORY_DIR = "Past_data/runs"

# 记录 schema 版本（供将来历史查看器稳定消费，类比 CONTRACT_VERSION）
RUN_RECORD_VERSION = "run_v1"


def _json_safe(obj):
    """递归把 inf/NaN 归一为 None，保证 json.dump(allow_nan=False) 严格合法。
    numpy 标量是 float/int 子类，isinstance 能命中；其余非常规类型交给 default=str。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def build_run_record(
    *, prompt, strategy_code, market, params, metrics, chart_file, timestamp_utc,
    summary="",
):
    """组装一条自包含历史记录（纯数据，不落盘）。params/metrics 为 JSON-able dict。

    metrics 是整套结构化指标（total_return_pct/annual_return_pct/sharpe_ratio/
    max_drawdown_pct/trade_count/胜率/盈亏比...，给程序消费）；summary 是 UI 里那段
    带语言标签的回测摘要原文（给人直接阅读），两者都存。"""
    return {
        "record_version": RUN_RECORD_VERSION,
        "timestamp_utc": timestamp_utc,
        "prompt": prompt or "",          # 直接粘贴代码回测时可能为空
        "strategy_code": strategy_code or "",
        "market": market,
        "params": params,
        "metrics": metrics,              # 结构化指标（收益率/年化/夏普/回撤/胜率...）
        "summary": summary or "",        # 人类可读回测摘要（与 UI 显示一致）
        "chart_file": chart_file,
    }


def save_run_record(record: dict, output_dir: str = RUN_HISTORY_DIR) -> str:
    """把一条记录写成 Past_data/runs/run_<UTC时间戳>_<随机后缀>.json，返回路径。"""
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, build_timestamped_filename("run", ".json"))
    with open(file_path, "w", encoding="utf-8") as f:
        # _json_safe 已把 inf/NaN 归一为 null，故 allow_nan=False 永不抛、且保证产出
        # 严格合法 JSON（默认 allow_nan=True 会写出非法的 Infinity/NaN 字面量）
        json.dump(_json_safe(record), f, ensure_ascii=False, indent=2, default=str, allow_nan=False)
    return file_path


def list_run_records(output_dir: str = RUN_HISTORY_DIR) -> list:
    """枚举历史记录路径（按文件名升序≈时间序）。供将来历史查看功能直接复用。"""
    if not os.path.isdir(output_dir):
        return []
    return [
        os.path.join(output_dir, name)
        for name in sorted(os.listdir(output_dir))
        if name.startswith("run_") and name.endswith(".json")
    ]


def load_run_record(file_path: str) -> dict:
    """读回一条历史记录。"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)
