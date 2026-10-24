"""시장 데이터 테스트: 종목표 형식·조회, 종가 → 변동성·상관계수 계산, 붙여넣기 파싱, (live) yfinance 조회."""

import json

import numpy as np
import pytest

from elsa.market import (
    DATA_PATH,
    MarketDataUnavailable,
    canonical_name,
    estimate_from_closes,
    estimate_from_tickers,
    lookup,
    parse_pasted_closes,
)


def test_underlyings_json_format():
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    owner: dict[str, str] = {}
    for name, v in raw.items():
        assert set(v) >= {"ticker", "vol", "div"}, name
        assert isinstance(v["ticker"], str) and v["ticker"], name
        assert 0 < v["vol"] < 2, name
        assert 0 <= v["div"] < 0.2, name
        for alias in [name, *v.get("aliases", [])]:
            key = alias.replace(" ", "").upper()
            assert owner.setdefault(key, name) == name, f"서로 다른 종목이 같은 별칭을 씀: {alias}"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("삼성전자", "삼성전자"),
        ("코스피200", "KOSPI200"),
        ("KOSPI200지수", "KOSPI200"),
        ("S&P 500 지수", "S&P500"),
        ("Eurostoxx50", "EUROSTOXX50"),
        ("Euro Stoxx 50", "EUROSTOXX50"),
        ("sk하이닉스", "SK하이닉스"),
        ("없는종목", None),
    ],
)
def test_canonical_name(text, expected):
    assert canonical_name(text) == expected


def test_lookup_returns_ticker_and_defaults():
    info = lookup("삼성전자")
    assert info.ticker == "005930.KS" and info.vol == 0.32 and info.div == 0.006
    assert lookup("코스피200").ticker == "069500.KS"  # ^KS200은 Yahoo 시세가 비어 있어 ETF로 대체


def test_dividend_yield_from_history():
    import pandas as pd

    idx = pd.to_datetime(["2025-05-15", "2025-11-14", "2026-02-13", "2026-05-15", "2026-08-14"]).tz_localize("Asia/Seoul")
    divs = pd.Series([361, 361, 365, 370, 370], index=idx, dtype=float)
    now = pd.Timestamp("2026-10-24", tz="Asia/Seoul")
    # 최근 365일(2025-10-24 이후) 배당만: 361 + 365 + 370 + 370 = 1466
    assert estimate_div(divs, 100_000, now) == pytest.approx(1466 / 100_000)
    assert estimate_div(pd.Series(dtype=float), 100_000, now) == 0.0
    assert estimate_div(divs, 0, now) == 0.0


def estimate_div(divs, price, now):
    from elsa.market import dividend_yield_from_history

    return dividend_yield_from_history(divs, price, now)


def test_fetch_closes_rejects_bad_months():
    from elsa.market import fetch_closes

    for months in (1, 121):
        with pytest.raises(MarketDataUnavailable, match="개월"):
            fetch_closes(["005930.KS"], months)


def test_index_dividend_proxy_etfs_are_configured():
    assert lookup("S&P500").div_ticker == "SPY"
    assert lookup("EUROSTOXX50").div_ticker == "FEZ"
    assert lookup("삼성전자").div_ticker is None
    assert lookup("닛케이225").div_ticker is None  # 믿을 만한 대체 ETF가 없어 기본값 유지


def test_estimate_uses_proxy_etf_for_dividends(monkeypatch):
    import pandas as pd

    from elsa import market

    idx = pd.bdate_range("2026-01-01", periods=80)
    closes = pd.DataFrame({"^GSPC": 5000.0 + pd.Series(range(80), index=idx).astype(float) * 3}, index=idx)
    monkeypatch.setattr(market, "fetch_closes", lambda tickers, months: closes[tickers])
    seen = []
    monkeypatch.setattr(market, "fetch_dividend_yield", lambda t, p=None: seen.append((t, p)) or 0.01)
    est = market.estimate_from_tickers(["^GSPC"], 6, ["SPY"])
    assert seen == [("SPY", None)]  # 지수 종가가 아니라 ETF 종가를 따로 조회하도록 가격은 None
    assert est.divs == [0.01] and est.div_sources == ["SPY"]
    seen.clear()
    est = market.estimate_from_tickers(["^GSPC"], 6)
    assert seen == [("^GSPC", closes["^GSPC"].iloc[-1])] and est.div_sources == ["^GSPC"]


def test_indices_have_no_dividend_lookup():
    from elsa.market import fetch_dividend_yield

    assert fetch_dividend_yield("^GSPC", 5000.0) is None


def test_estimate_from_closes_recovers_parameters():
    rng = np.random.default_rng(0)
    n, vol1, vol2, rho = 5000, 0.2, 0.4, 0.6
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + np.sqrt(1 - rho**2) * rng.standard_normal(n)
    rets = np.column_stack([vol1 * z1, vol2 * z2]) / np.sqrt(252)
    closes = 100 * np.exp(np.vstack([np.zeros(2), np.cumsum(rets, axis=0)]))
    vols, corr = estimate_from_closes(closes)
    assert vols[0] == pytest.approx(vol1, rel=0.05)
    assert vols[1] == pytest.approx(vol2, rel=0.05)
    assert corr[0][1] == pytest.approx(rho, abs=0.03)
    assert corr[0][0] == 1.0


def test_estimate_single_asset_and_too_short():
    vols, corr = estimate_from_closes(np.linspace(100, 110, 60))
    assert len(vols) == 1 and corr == [[1.0]]
    with pytest.raises(MarketDataUnavailable):
        estimate_from_closes(np.linspace(100, 110, 10))


def test_parse_pasted_closes_formats():
    tab = "날짜\t삼성전자\tSK하이닉스\n2024-01-02\t78,500\t140,000\n2024-01-03\t77,000\t138,500.5"
    assert parse_pasted_closes(tab, 2).tolist() == [[78500, 140000], [77000, 138500.5]]
    csv = "2024-01-02,78500,140000\n2024-01-03,77000,138500"
    assert parse_pasted_closes(csv, 2).tolist() == [[78500, 140000], [77000, 138500]]
    single = "100\n101\n102.5"
    assert parse_pasted_closes(single, 1).ravel().tolist() == [100, 101, 102.5]
    with pytest.raises(MarketDataUnavailable):
        parse_pasted_closes("가나다", 1)


@pytest.mark.live
def test_live_yfinance_estimate():
    est = estimate_from_tickers(["005930.KS", "^GSPC"], months=12)
    assert est.n_days > 150
    assert all(0.05 < v < 1.5 for v in est.vols)
    assert -1 <= est.corr[0][1] <= 1
    assert est.divs[0] is not None and 0 < est.divs[0] < 0.1  # 개별 종목은 실제 배당
    assert est.divs[1] is None  # 지수는 배당 정보 없음


@pytest.mark.live
def test_live_every_table_ticker_has_data():
    from elsa.market import load_underlyings

    for info in load_underlyings().values():
        est = estimate_from_tickers([info.ticker], months=3)
        assert est.n_days > 40, info.name
