"""
K 线数据完整性检查与缺口修复。

币安拉取偶发失败会在 1m 数据文件中段留下【缺分钟】（缺口），扭曲重采样/指标/信号。
本模块负责发现并回补这些缺口：

- scan_gaps：扫 open_time 找【内部】缺口（相邻两根 > 1 分钟；首根前/末根后不算，
  那是数据边界、由正常增量更新负责）。
- 分段（自然月）白名单控开销：某月连续 N=3 次检查无缺口（已知空洞除外）⇒ 永久
  白名单、之后不再检查；最新月始终复查（新数据落在那里）。force=True 忽略白名单全查。
- 修复：对每个缺口区间从币安回补（fetch_and_merge_range 合并去重落盘）。回补后仍缺
  的分钟 = 币安确无 ⇒ 记入 known_holes，不再反复重试、也不阻止该月白名单。
- check_and_repair：先查 → 有缺口（在评估月内）先修 → 更新分段状态 → 返回报告。
  供 fetch_queue 后台「先检查/修复、再更新」与手动按钮调用。

状态 sidecar：{data_dir}/.integrity/{SYMBOL}.json（不污染数据文件本身）。
纯扫描/状态逻辑可单测；网络回补经可注入的 fetcher（默认走真实下载器）。
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from module.modules.Load_real_kline import kline_file_name, normalize_symbol

ONE_MINUTE_MS = 60_000
INTEGRITY_DIR_NAME = ".integrity"
STATE_VERSION = "integrity_v1"
CLEAN_STREAK_TO_WHITELIST = 3


# =========================================================
# 纯函数：缺口扫描 / 月份 / 已知空洞
# =========================================================

def scan_gaps(open_times_ms):
    """open_times_ms：升序去重的毫秒整数数组。返回内部缺口 [(first_missing_ms,
    last_missing_ms), ...]（分钟对齐、左右闭）——仅相邻两根间 > 1min 的缺口。"""
    arr = np.asarray(open_times_ms, dtype=np.int64)
    if arr.size < 2:
        return []
    diffs = np.diff(arr)
    idx = np.where(diffs > ONE_MINUTE_MS)[0]
    return [(int(arr[i] + ONE_MINUTE_MS), int(arr[i + 1] - ONE_MINUTE_MS)) for i in idx]


def _month_of(ms):
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _month_start_ms(month_key):
    y, m = month_key.split("-")
    return int(datetime(int(y), int(m), 1, tzinfo=timezone.utc).timestamp() * 1000)


def _month_end_ms(month_key):
    """该月最后一毫秒（闭区间右端）：下月初 - 1ms。"""
    y, m = int(month_key[:4]), int(month_key[5:7])
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return int(datetime(ny, nm, 1, tzinfo=timezone.utc).timestamp() * 1000) - 1


def _gap_months(gap):
    """缺口横跨的全部月份键（缺口通常在一个月内，但大缺口可能跨月）。"""
    gs, ge = gap
    out = []
    cur = _month_start_ms(_month_of(gs))
    while cur <= ge:
        key = _month_of(cur)
        out.append(key)
        cur = _month_end_ms(key) + 1
    return out


def _gap_in_months(gap, months_set):
    return any(m in months_set for m in _gap_months(gap))


def _month_has_gap(month_key, gaps):
    return any(month_key in _gap_months(g) for g in gaps)


def _covered_by_holes(gap, holes):
    """缺口是否被某个已知空洞完整覆盖（则不计为待修复缺口）。"""
    gs, ge = gap
    return any(hs <= gs and ge <= he for (hs, he) in holes)


def _overlaps_any(gap, ranges):
    gs, ge = gap
    return any(not (ge < rs or gs > re) for (rs, re) in ranges)


def _merge_holes(holes):
    """合并重叠/相邻的已知空洞区间，保持有序、不无界膨胀。"""
    if not holes:
        return []
    items = sorted((int(a), int(b)) for a, b in holes)
    merged = [list(items[0])]
    for s, e in items[1:]:
        if s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


# =========================================================
# 文件读取 / 状态持久化
# =========================================================

def _read_open_times(file_path):
    """只读 open_time 列（远比读全表快），归一为升序去重的毫秒 int64 数组。"""
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return np.array([], dtype=np.int64)
    df = pd.read_csv(file_path, usecols=["open_time"])
    s = df["open_time"]
    if pd.api.types.is_numeric_dtype(s):
        s = pd.to_numeric(s, errors="coerce").dropna()
        arr = s.astype("int64").to_numpy()
    else:
        dt = pd.to_datetime(s, utc=True, errors="coerce").dropna()
        arr = ((dt - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy().astype("int64")
    return np.unique(arr)


def _months_present(open_times):
    """有数据的月份集合（含 ≥1 根 K 线的月）。"""
    if open_times.size == 0:
        return set()
    # unit="ms" 的 epoch 本就是 UTC；用 tz-naive（UTC 墙钟）避免 to_period 丢时区告警，
    # 月份与 _month_of(fromtimestamp tz=utc) 同口径
    periods = pd.to_datetime(open_times, unit="ms").to_period("M")
    return {str(p) for p in periods.unique()}


def _state_dir(data_dir):
    return os.path.join(data_dir, INTEGRITY_DIR_NAME)


def _state_path(symbol, data_dir):
    return os.path.join(_state_dir(data_dir), f"{normalize_symbol(symbol)}.json")


def _load_state(symbol, data_dir):
    path = _state_path(symbol, data_dir)
    if not os.path.exists(path):
        return {"version": STATE_VERSION, "known_holes": [], "segments": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("known_holes", [])
        st.setdefault("segments", {})
        return st
    except Exception:
        # 状态损坏不致命：当作空状态重建（大不了重新检查几次）
        return {"version": STATE_VERSION, "known_holes": [], "segments": {}}


def _save_state(symbol, data_dir, state):
    os.makedirs(_state_dir(data_dir), exist_ok=True)
    path = _state_path(symbol, data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _default_fetcher(symbol, start_ms, end_ms, data_dir, market_type):
    from cryptocurrency_data.obtain_K_data import fetch_and_merge_range
    return fetch_and_merge_range(symbol, start_ms, end_ms, save_dir=data_dir, market_type=market_type)


# =========================================================
# 编排：检查 + 修复
# =========================================================

def check_and_repair(
    symbol, data_dir, *, force=False, repair=True, fetcher=None,
    market_type="spot", now_ms=None,
):
    """检查 symbol 的 1m 数据缺口并（可选）回补。

    force=True 忽略白名单全量检查（手动按钮用）；force=False 只查【未白名单月 + 最新月】
    （后台用，省开销）。repair=False 只查不补（仅报告）。fetcher 可注入便于测试。
    返回 JSON-able 报告 dict。
    """
    sym = normalize_symbol(symbol)
    file_path = os.path.join(data_dir, kline_file_name(sym))
    open_times = _read_open_times(file_path)
    if open_times.size < 2:
        return {"symbol": sym, "available": False, "reason": "数据为空或不足以检查缺口"}

    state = _load_state(sym, data_dir)
    holes = _merge_holes([tuple(h) for h in state.get("known_holes", [])])
    segments = dict(state.get("segments", {}))

    months_present = _months_present(open_times)
    latest_month = _month_of(int(open_times[-1]))

    if force:
        eval_months = set(months_present)
    else:
        eval_months = {m for m in months_present if not segments.get(m, {}).get("whitelisted")}
        eval_months.add(latest_month)
    eval_months &= months_present

    gaps = scan_gaps(open_times)
    effective = [g for g in gaps if not _covered_by_holes(g, holes)]
    to_repair = [g for g in effective if _gap_in_months(g, eval_months)]

    rows_repaired = 0
    new_holes_count = 0
    if repair and to_repair:
        fn = fetcher or _default_fetcher
        for (gs, ge) in to_repair:
            rows_repaired += int(fn(sym, gs, ge, data_dir, market_type) or 0)
        # 回补后重读重扫：本轮尝试修复但仍残留的缺口 = 币安确无 ⇒ 记为已知空洞
        open_times = _read_open_times(file_path)
        months_present = _months_present(open_times)
        eval_months &= months_present
        gaps = scan_gaps(open_times)
        residual = [g for g in gaps if not _covered_by_holes(g, holes) and _overlaps_any(g, to_repair)]
        if residual:
            holes = _merge_holes(holes + residual)
            new_holes_count = len(residual)
        effective = [g for g in gaps if not _covered_by_holes(g, holes)]

    # 更新评估月的 clean_streak / whitelist
    for m in eval_months:
        if _month_has_gap(m, effective):
            segments[m] = {"clean_streak": 0, "whitelisted": False}
        else:
            streak = segments.get(m, {}).get("clean_streak", 0) + 1
            segments[m] = {"clean_streak": streak, "whitelisted": streak >= CLEAN_STREAK_TO_WHITELIST}

    state = {
        "version": STATE_VERSION,
        "updated_at": _now_str(now_ms),
        "known_holes": [list(h) for h in holes],
        "segments": segments,
    }
    _save_state(sym, data_dir, state)

    remaining = [g for g in effective if _gap_in_months(g, eval_months)]
    return {
        "symbol": sym,
        "available": True,
        "gaps_to_repair": len(to_repair),
        "rows_repaired": rows_repaired,
        "confirmed_holes_new": new_holes_count,
        "remaining_gaps": len(remaining),
        "known_holes_total": len(holes),
        "evaluated_months": sorted(eval_months),
        "whitelisted_months": sorted(m for m, s in segments.items() if s.get("whitelisted")),
    }


def _now_str(now_ms=None):
    if now_ms is not None:
        return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
