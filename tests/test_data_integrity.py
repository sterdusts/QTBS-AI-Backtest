"""
数据完整性 data_integrity 金样例：缺口扫描、修复（mock fetcher 无网络）、
币安确无→已知空洞、连续 3 次干净→白名单、force 忽略白名单。
"""
import os

import numpy as np
import pandas as pd

from module.modules import data_integrity as di
from module.modules.Load_real_kline import kline_file_name

MIN = di.ONE_MINUTE_MS


def _csv_path(tmp_path, sym="BTCUSDT"):
    return os.path.join(str(tmp_path), kline_file_name(sym))


def _write(path, minutes_ms):
    df = pd.DataFrame({"open_time": list(minutes_ms)})
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = 1.0
    df.to_csv(path, index=False)


def _filler(path, available_minutes):
    """mock fetcher：把 [start,end] 内币安"有"的分钟补进文件，返回净增。"""
    avail = set(int(m) for m in available_minutes)

    def fetcher(symbol, start_ms, end_ms, data_dir, market_type):
        have = set(int(x) for x in di._read_open_times(path).tolist())
        add = sorted(m for m in avail if start_ms <= m <= end_ms and m not in have)
        if not add:
            return 0
        df = pd.read_csv(path)
        extra = pd.DataFrame({"open_time": add})
        for c in ("open", "high", "low", "close", "volume"):
            extra[c] = 1.0
        out = pd.concat([df, extra], ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
        out.to_csv(path, index=False)
        return len(add)
    return fetcher


# =========================================================
# scan_gaps
# =========================================================

def test_scan_gaps_detects_internal_gap():
    base = di._month_start_ms("2024-01")
    # 0..5 连续, 跳过 6,7, 再 8..12 → 缺口 = (base+6min, base+7min)
    mins = [base + k * MIN for k in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]
    gaps = di.scan_gaps(np.array(mins, dtype=np.int64))
    assert gaps == [(base + 6 * MIN, base + 7 * MIN)]


def test_scan_gaps_contiguous_none():
    base = di._month_start_ms("2024-01")
    mins = [base + k * MIN for k in range(20)]
    assert di.scan_gaps(np.array(mins, dtype=np.int64)) == []


# =========================================================
# 修复：缺口被回补
# =========================================================

def test_repair_fills_gap(tmp_path):
    path = _csv_path(tmp_path)
    base = di._month_start_ms("2024-01")
    present = [base + k * MIN for k in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]
    missing = [base + 6 * MIN, base + 7 * MIN]
    _write(path, present)

    rep = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=_filler(path, present + missing))
    assert rep["available"] is True
    assert rep["gaps_to_repair"] == 1
    assert rep["rows_repaired"] == 2
    assert rep["remaining_gaps"] == 0
    # 文件确实补全了
    assert di.scan_gaps(di._read_open_times(path)) == []


def test_unfillable_gap_becomes_known_hole(tmp_path):
    path = _csv_path(tmp_path)
    base = di._month_start_ms("2024-01")
    present = [base + k * MIN for k in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]
    _write(path, present)

    # 币安确无缺口分钟（available 只含已存在的）⇒ fetcher 返回 0
    rep1 = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=_filler(path, present))
    assert rep1["rows_repaired"] == 0
    assert rep1["confirmed_holes_new"] == 1     # 该缺口被确认为空洞
    assert rep1["known_holes_total"] == 1

    # 再查：缺口被已知空洞覆盖 ⇒ 不再计为待修复，月份可视为干净
    rep2 = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=_filler(path, present))
    assert rep2["gaps_to_repair"] == 0
    assert rep2["remaining_gaps"] == 0


# =========================================================
# 白名单：连续 3 次干净
# =========================================================

def test_whitelist_after_three_clean_checks(tmp_path):
    path = _csv_path(tmp_path)
    base = di._month_start_ms("2024-01")
    _write(path, [base + k * MIN for k in range(30)])   # 全干净，单月

    f = _filler(path, [])
    r1 = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f)
    r2 = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f)
    assert "2024-01" not in r1["whitelisted_months"]
    assert "2024-01" not in r2["whitelisted_months"]
    r3 = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f)
    assert "2024-01" in r3["whitelisted_months"]   # 第 3 次后白名单


def test_force_rechecks_whitelisted_segment(tmp_path):
    path = _csv_path(tmp_path)
    base = di._month_start_ms("2024-02")
    # 跨 1 月/2 月边界连续 11 根，无内部缺口；present 月 = {2024-01, 2024-02}
    _write(path, [base + k * MIN for k in range(-5, 6)])

    f = _filler(path, [])
    for _ in range(3):
        di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f)

    # force=False：旧月 2024-01 已白名单 ⇒ 跳过；最新月 2024-02 始终复查
    normal = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f)
    assert "2024-01" not in normal["evaluated_months"]
    assert "2024-02" in normal["evaluated_months"]

    # force=True：忽略白名单，全查
    forced = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=f, force=True)
    assert "2024-01" in forced["evaluated_months"]
    assert "2024-02" in forced["evaluated_months"]


def test_empty_file_reports_unavailable(tmp_path):
    path = _csv_path(tmp_path)
    _write(path, [di._month_start_ms("2024-01")])   # 单根，不足以查缺口
    rep = di.check_and_repair("BTCUSDT", str(tmp_path), fetcher=_filler(path, []))
    assert rep["available"] is False
