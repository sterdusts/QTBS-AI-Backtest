"""
资金费率数据管线测试（契约 §10.8 的 Stage B：下载器 + 数据层对齐摊销）。

覆盖：
- 下载器：手动分页、断点续传、去重排序、原子落盘、损坏行清洗（mock client，无网络）
- 数据层 load_funding_series：merge_asof(backward) 防未来函数 + 首结算点前为 0
  + 按 bar/结算周期连续摊销
- build_funding_rates：只纳入有数据的标的，全无数据返回 None
"""

import os

import pandas as pd
import pytest

from cryptocurrency_data import funding_rate_data as frd
from module.modules import data_panel
from module.modules.Load_real_kline import get_funding_file_path, has_funding_data


EIGHT_H_MS = 8 * 3600 * 1000


def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").value // 1_000_000)


class FakeBinanceClient:
    """只实现 futures_funding_rate，按 startTime/endTime 过滤并按 limit 截断分页。"""

    def __init__(self, records):
        self._records = sorted(records, key=lambda r: r["fundingTime"])
        self.calls = []

    def futures_funding_rate(self, symbol, startTime, endTime=None, limit=1000):
        self.calls.append({"startTime": startTime, "endTime": endTime, "limit": limit})
        hits = [
            r for r in self._records
            if r["fundingTime"] >= startTime
            and (endTime is None or r["fundingTime"] <= endTime)
        ]
        return hits[:limit]


def _make_records(n, base_ts="2024-01-01 00:00", rate="0.0001"):
    base = _ms(base_ts)
    return [
        {"symbol": "BTCUSDT", "fundingTime": base + i * EIGHT_H_MS, "fundingRate": rate}
        for i in range(n)
    ]


# =========================================================
# 下载器
# =========================================================

def test_funding_acquisition_paginates_and_persists(tmp_path):
    # 2500 条、单页 1000 ⇒ 3 页（1000/1000/500），全部落盘
    client = FakeBinanceClient(_make_records(2500))
    out = frd.funding_acquisition(
        "BTC", end_date="now UTC", save_dir=str(tmp_path), client=client
    )
    assert len(out) == 2500
    assert len(client.calls) == 3                      # 手动分页 3 次
    assert client.calls[1]["startTime"] > client.calls[0]["startTime"]  # 起点递增

    # 落盘文件可被读回，funding_time 为整数且有序
    path = get_funding_file_path("BTC", funding_dir=str(tmp_path))
    assert os.path.exists(path)
    reloaded = frd.load_existing_funding_df(path)
    assert len(reloaded) == 2500
    assert reloaded["funding_time"].is_monotonic_increasing
    assert pd.api.types.is_integer_dtype(reloaded["funding_time"])


def test_funding_acquisition_resumes_from_last(tmp_path):
    # 先写入前 100 条
    client1 = FakeBinanceClient(_make_records(100))
    frd.funding_acquisition("BTC", save_dir=str(tmp_path), client=client1)

    path = get_funding_file_path("BTC", funding_dir=str(tmp_path))
    last_t = frd.load_existing_funding_df(path)["funding_time"].iloc[-1]

    # 第二次：断点续传应从 last+1 起请求
    client2 = FakeBinanceClient(_make_records(150))   # 后 50 条为新
    frd.funding_acquisition("BTC", save_dir=str(tmp_path), client=client2)

    assert client2.calls[0]["startTime"] == int(last_t) + 1
    # 去重后总数为 150（无重复 funding_time）
    final = frd.load_existing_funding_df(path)
    assert len(final) == 150
    assert final["funding_time"].is_unique


def test_funding_records_to_df_coerces_types():
    df = frd.funding_records_to_df([
        {"fundingTime": _ms("2024-01-01"), "fundingRate": "0.0001"},
        {"fundingTime": _ms("2024-01-01 08:00"), "fundingRate": "-0.0002"},
    ])
    assert list(df.columns) == ["funding_time", "funding_rate"]
    assert df["funding_rate"].iloc[1] == pytest.approx(-0.0002)


def test_load_existing_funding_df_drops_corrupt_rows(tmp_path):
    path = tmp_path / "BTCUSDT_FUNDING.csv"
    pd.DataFrame({
        "funding_time": [_ms("2024-01-01"), None, _ms("2024-01-01 08:00")],
        "funding_rate": [0.0001, 0.0002, None],
    }).to_csv(path, index=False)
    # 缺 funding_time 或缺 funding_rate 的行都丢弃 ⇒ 只剩第 1 条
    df = frd.load_existing_funding_df(str(path))
    assert len(df) == 1
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0001)


# =========================================================
# 数据层：对齐 + 连续摊销 + 防未来函数
# =========================================================

def _write_funding_csv(tmp_path, symbol, settle_times, rates):
    path = get_funding_file_path(symbol, funding_dir=str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame({
        "funding_time": [_ms(t) for t in settle_times],
        "funding_rate": list(rates),
    }).to_csv(path, index=False)
    return path


def test_load_funding_series_backward_and_amortized(tmp_path):
    # 结算点 04:00 / 12:00 / 20:00（间隔 8h），费率 0.001 / 0.002 / 0.003
    _write_funding_csv(
        tmp_path, "BTC",
        ["2024-01-01 04:00", "2024-01-01 12:00", "2024-01-01 20:00"],
        [0.001, 0.002, 0.003],
    )
    # 4h K 线：00,04,08,12,16,20
    index = pd.date_range("2024-01-01 00:00", periods=6, freq="4h")

    series = data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path))
    assert series is not None
    assert list(series.index) == list(index)

    # 防未来函数：08:00 取 04:00 的费率（0.001），不取未来的 12:00；
    # 首结算点（04:00）之前的 00:00 为 0。
    # 连续摊销系数 = bar(4h)/结算(8h) = 0.5 ⇒ 已结算费率 × 0.5
    expected = [0.0, 0.0005, 0.0005, 0.001, 0.001, 0.0015]
    assert series.tolist() == pytest.approx(expected)


def test_load_funding_series_none_when_no_data(tmp_path):
    index = pd.date_range("2024-01-01", periods=4, freq="4h")
    assert not has_funding_data("ETH", funding_dir=str(tmp_path))
    assert data_panel.load_funding_series("ETH", index, funding_dir=str(tmp_path)) is None


def test_build_funding_rates_includes_only_available(tmp_path):
    _write_funding_csv(
        tmp_path, "BTC",
        ["2024-01-01 00:00", "2024-01-01 08:00"],
        [0.001, 0.001],
    )
    index = pd.date_range("2024-01-01 00:00", periods=6, freq="4h")

    # BTC 有数据、ETH 无 ⇒ 只含 BTCUSDT
    rates = data_panel.build_funding_rates(["BTC", "ETH"], index, funding_dir=str(tmp_path))
    assert rates is not None
    assert set(rates.keys()) == {"BTCUSDT"}
    assert len(rates["BTCUSDT"]) == len(index)

    # 全无数据 ⇒ None（引擎不计 funding）
    assert data_panel.build_funding_rates(["ETH", "SOL"], index, funding_dir=str(tmp_path)) is None


def test_funding_series_feeds_engine_end_to_end(tmp_path):
    # 数据层产出的 per-bar 费率喂给引擎，funding 真实生效（与 test_funding 的合成
    # 费率路径一致，这里验证「真实 CSV → 数据层 → 引擎」整链路打通）
    from module.modules.portfolio_backtest_core import PortfolioBacktestCore
    from tests.helpers import make_df, weights_strategy

    _write_funding_csv(
        tmp_path, "BTC",
        ["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00"],
        [0.001, 0.001, 0.001],
    )
    index = pd.date_range("2024-01-01 00:00", periods=5, freq="4h")
    data = {"BTCUSDT": make_df([100] * 5).set_axis(index)}
    rates = data_panel.build_funding_rates(["BTCUSDT"], index, funding_dir=str(tmp_path))

    res = PortfolioBacktestCore(
        weights_strategy([(1,), (1,), (1,), (0,), (0,)], ["BTCUSDT"]),
        initial_cash=1000.0,
    ).run(data, funding_rates=rates)

    # 多头持仓且费率为正 ⇒ 净付出（total_funding_cost > 0）
    assert res["metrics"]["total_funding_cost"] > 0
    assert res["trades"][0]["funding_pnl"] < 0


# =========================================================
# round-9 加固：尾部覆盖 / 单根 / 损坏 CSV / tz-aware / 重复索引
# =========================================================

def test_funding_tail_beyond_coverage_zeroed(tmp_path):
    # 费率仅覆盖到 16:00（每 8h），index 延伸到次日：超末结算点一个结算周期
    # 以上的尾部 bar 退化为 0，不把最后费率无限前向外推（②）
    _write_funding_csv(
        tmp_path, "BTC",
        ["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00"],
        [0.001, 0.001, 0.001],
    )
    index = pd.date_range("2024-01-01 00:00", periods=12, freq="4h")  # 到次日 20:00
    s = data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path))
    by_time = {str(t): v for t, v in s.items()}
    # 覆盖边界 = 末结算点 16:00 + 8h = 次日 00:00：边界内保留摊销值（一周期内允许外推）
    assert by_time["2024-01-01 20:00:00"] == pytest.approx(0.0005)
    assert by_time["2024-01-02 00:00:00"] == pytest.approx(0.0005)
    # 超出一个结算周期 → 0
    assert by_time["2024-01-02 04:00:00"] == pytest.approx(0.0)
    assert by_time["2024-01-02 20:00:00"] == pytest.approx(0.0)


def test_funding_single_bar_charges_full_period(tmp_path):
    # 单根 index：摊销因子兜底为 1（结算周期/结算周期），计提一次完整 funding 而非 0（⑥）
    _write_funding_csv(tmp_path, "BTC", ["2024-01-01 00:00", "2024-01-01 08:00"], [0.002, 0.003])
    index = pd.DatetimeIndex(["2024-01-01 00:00"])
    s = data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path))
    assert s.iloc[0] == pytest.approx(0.002)


def test_funding_corrupt_csv_degrades_to_none(tmp_path):
    # 损坏/无法解析的 funding 文件不得中断核心回测，退化为 None（⑤）
    path = get_funding_file_path("BTC", funding_dir=str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    index = pd.date_range("2024-01-01", periods=6, freq="4h")

    open(path, "w").close()  # 空文件 → EmptyDataError
    assert data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path)) is None

    with open(path, "w", encoding="utf-8") as f:  # 列数不一致 → ParserError
        f.write("funding_time,funding_rate\n1,0.5,EXTRA\n2,0.6,EXTRA2\n")
    assert data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path)) is None


def test_funding_tz_aware_index_handled(tmp_path):
    # tz-aware index 内部剥成 tz-naive UTC 再对齐，不抛错（防御）
    _write_funding_csv(
        tmp_path, "BTC",
        ["2024-01-01 04:00", "2024-01-01 12:00", "2024-01-01 20:00"],
        [0.001, 0.002, 0.003],
    )
    index = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")
    s = data_panel.load_funding_series("BTC", index, funding_dir=str(tmp_path))
    assert s is not None
    assert getattr(s.index, "tz", None) is None
    assert s.tolist() == pytest.approx([0.0, 0.0005, 0.0005, 0.001, 0.001, 0.0015])


def test_funding_duplicate_index_degrades_to_none(tmp_path):
    # 重复时间戳 index 无法 reindex，退化为 None 而非晦涩 pandas 错误（防御）
    _write_funding_csv(tmp_path, "BTC", ["2024-01-01 00:00"], [0.001])
    dup = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 04:00", "2024-01-01 04:00"])
    assert data_panel.load_funding_series("BTC", dup, funding_dir=str(tmp_path)) is None


def test_funding_records_to_df_schema_drift_returns_empty():
    # API 返回缺键记录 → 空表而非 TypeError（下载器健壮性）
    assert frd.funding_records_to_df([{"foo": 1}, {"bar": 2}]).empty
    assert frd.funding_records_to_df([{"fundingTime": 1}]).empty


def test_engine_funding_tz_aware_index_not_silently_zeroed():
    # round-10：直连 API 传 tz-aware K 线索引 + tz-naive funding 序列时，引擎
    # _prepare_funding_rates 应统一 tz 后对齐，而非 reindex 全 NaN → funding 静默清零
    from module.modules.portfolio_backtest_core import PortfolioBacktestCore
    from tests.helpers import make_df, weights_strategy

    idx_naive = pd.date_range("2024-01-01 00:00", periods=5, freq="4h")
    idx_aware = idx_naive.tz_localize("UTC")
    data = {"BTCUSDT": make_df([100] * 5).set_axis(idx_aware)}
    funding = {"BTCUSDT": pd.Series([0.001] * 5, index=idx_naive)}  # data_panel 剥 tz 后口径

    res = PortfolioBacktestCore(
        weights_strategy([(1,), (1,), (1,), (0,), (0,)], ["BTCUSDT"]),
        initial_cash=1000.0,
    ).run(data, funding_rates=funding)

    # funding 真实生效（多头付 → 净支出 > 0），而非被 tz 不匹配静默清零
    assert res["metrics"]["total_funding_cost"] > 0
