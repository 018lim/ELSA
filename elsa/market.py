"""시장 데이터: 종목표 조회, 시세 조회, 종가로 변동성·상관계수·배당수익률 추정 (streamlit 의존 없음).

종목표는 elsa/data/underlyings.json에 있고, 코드를 고치지 않고 항목만 추가해 확장한다.
변동성은 과거(실현) 변동성만 쓴다: 일별 로그수익률 표준편차 × √252.
배당수익률은 최근 1년 실제 배당 내역으로 계산한다. 지수는 Yahoo에 배당 정보가 없으므로 종목표에
div_ticker(추종 ETF)가 있으면 그 ETF로 계산하고, 없으면 종목표 값을 쓴다.

- UnderlyingInfo: 종목표 한 항목 (정규화 이름, 티커, 기본 변동성·배당)
- load_underlyings(): 종목표 전체를 읽는다
- lookup(name): 이름·별칭으로 종목표 항목을 찾는다 (공백·대소문자·'지수' 무시)
- canonical_name(name): 종목표 기준 정규화 이름 (없으면 None)
- estimate_from_closes(closes): 종가 행렬 → 연 변동성 목록, 상관계수 행렬
- parse_pasted_closes(text, n_assets): 엑셀 등에서 붙여넣은 종가 텍스트 → 종가 행렬
- fetch_closes(tickers, months): yfinance로 최근 months개월 일별 종가 조회 (공통 거래일만)
- dividend_yield_from_history(dividends, last_price): 최근 1년 배당금 합계 ÷ 현재 주가
- fetch_dividend_yield(ticker, last_price): 종목·ETF 배당수익률 조회 (지수·실패 시 None)
- estimate_from_tickers(tickers, months, div_tickers): 시세 조회 + 변동성·상관·배당 추정 → MarketEstimate
- MarketDataUnavailable: 시세 조회 실패
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA_PATH = Path(__file__).parent / "data" / "underlyings.json"
TRADING_DAYS = 252
DEFAULT_CORR = 0.5
MIN_OBSERVATIONS = 30
MIN_EST_MONTHS = 2  # 일별 수익률이 최소 30개 이상 나오도록
MAX_EST_MONTHS = 120


class MarketDataUnavailable(Exception):
    """시세를 가져오지 못했거나 계산할 데이터가 부족함."""


@dataclass(frozen=True)
class UnderlyingInfo:
    name: str
    ticker: str
    vol: float
    div: float
    div_ticker: str | None = None  # 지수처럼 자체 배당 정보가 없을 때 대신 배당을 계산할 추종 ETF 티커


@dataclass(frozen=True)
class MarketEstimate:
    vols: list[float]
    corr: list[list[float]]
    divs: list[float | None]  # 값이 있으면 실제 배당으로 계산, 없으면 None (기본값 유지)
    div_sources: list[str | None]  # divs를 계산한 티커 (대체 ETF를 썼으면 그 티커)
    n_days: int
    start: str
    end: str


def _key(name: str) -> str:
    """비교용 키: 공백 제거, 대문자, 끝의 '지수' 제거."""
    k = re.sub(r"\s+", "", name).upper()
    return re.sub(r"지수$", "", k)


@lru_cache(maxsize=1)
def load_underlyings() -> dict[str, UnderlyingInfo]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {
        name: UnderlyingInfo(
            name=name, ticker=v["ticker"], vol=float(v["vol"]), div=float(v["div"]), div_ticker=v.get("div_ticker")
        )
        for name, v in raw.items()
    }


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    index: dict[str, str] = {}
    for name, v in raw.items():
        for alias in [name, *v.get("aliases", [])]:
            index[_key(alias)] = name
    return index


def lookup(name: str) -> UnderlyingInfo | None:
    canonical = _alias_index().get(_key(name))
    return load_underlyings()[canonical] if canonical else None


def canonical_name(name: str) -> str | None:
    info = lookup(name)
    return info.name if info else None


def estimate_from_closes(closes: np.ndarray) -> tuple[list[float], list[list[float]]]:
    """종가 행렬(행: 날짜, 열: 자산) → (연 변동성 목록, 상관계수 행렬)."""
    closes = np.asarray(closes, dtype=float)
    if closes.ndim == 1:
        closes = closes[:, None]
    if closes.shape[0] < MIN_OBSERVATIONS + 1:
        raise MarketDataUnavailable(f"종가가 최소 {MIN_OBSERVATIONS + 1}일 필요합니다. 지금은 {closes.shape[0]}일입니다.")
    if (closes <= 0).any():
        raise MarketDataUnavailable("종가에 0 이하 값이 있습니다.")
    rets = np.diff(np.log(closes), axis=0)
    vols = rets.std(axis=0, ddof=1) * np.sqrt(TRADING_DAYS)
    n = closes.shape[1]
    corr = np.corrcoef(rets, rowvar=False) if n > 1 else np.ones((1, 1))
    corr = np.atleast_2d(corr)
    np.fill_diagonal(corr, 1.0)
    return vols.tolist(), corr.tolist()


_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_pasted_closes(text: str, n_assets: int) -> np.ndarray:
    """붙여넣은 종가 텍스트를 (날짜 × 자산) 행렬로 바꾼다.

    한 줄에 한 날짜. 구분자는 탭·세미콜론·공백·쉼표 모두 허용하며, 숫자 안의 천 단위
    쉼표("1,234.5")는 탭·공백으로 구분된 줄에서만 지운다. 날짜·머리글처럼 숫자가 아닌
    칸은 건너뛰고, 각 줄의 숫자 중 마지막 n_assets개를 종가로 쓴다.
    """
    rows = []
    for line in text.strip().splitlines():
        if re.search(r"[\t;]|\s", line.strip()):
            tokens = re.split(r"[\t;\s]+", line.strip())
            tokens = [t.replace(",", "") for t in tokens]
        else:
            tokens = line.strip().split(",")
        nums = [float(t) for t in tokens if _NUMBER.match(t.strip())]
        if len(nums) >= n_assets:
            rows.append(nums[-n_assets:])
    if not rows:
        raise MarketDataUnavailable("붙여넣은 내용에서 종가를 찾지 못했습니다.")
    return np.asarray(rows, dtype=float)


def fetch_closes(tickers: list[str], months: int):
    """yfinance로 최근 months개월 일별 수정종가를 받아 모든 자산이 거래된 날짜만 남긴 DataFrame을 돌려준다."""
    if not MIN_EST_MONTHS <= months <= MAX_EST_MONTHS:
        raise MarketDataUnavailable(f"추정 기간은 {MIN_EST_MONTHS}~{MAX_EST_MONTHS}개월로 입력해 주세요.")
    try:
        import pandas as pd
        import yfinance as yf

        start = (pd.Timestamp.today().normalize() - pd.DateOffset(months=months)).strftime("%Y-%m-%d")
        data = yf.download(
            tickers, start=start, interval="1d", auto_adjust=True, progress=False, threads=False
        )
    except Exception as e:  # 네트워크·차단·라이브러리 오류
        raise MarketDataUnavailable(f"시세 조회 실패: {type(e).__name__}") from e
    if data is None or data.empty or "Close" not in data:
        raise MarketDataUnavailable("시세 데이터가 비어 있습니다. 티커를 확인해 주세요.")
    close = data["Close"]
    if hasattr(close, "to_frame") and close.ndim == 1:
        close = close.to_frame(tickers[0])
    missing = [t for t in tickers if t not in close.columns or close[t].dropna().empty]
    if missing:
        raise MarketDataUnavailable(f"다음 티커의 시세가 없습니다: {', '.join(missing)}")
    return close[tickers].dropna()


def dividend_yield_from_history(dividends, last_price: float, now=None) -> float:
    """최근 1년(now 기준 365일) 동안 지급된 주당 배당금 합계 ÷ 현재 주가.

    Yahoo 요약값(dividendYield 등)은 항목마다 단위(% 숫자 / 소수)가 달라 쓰지 않고,
    실제 지급 내역으로 직접 계산한다. 배당 내역이 없으면 0.
    """
    import pandas as pd

    if dividends is None or len(dividends) == 0 or not last_price or last_price <= 0:
        return 0.0
    index = dividends.index
    now = pd.Timestamp.now(tz=index.tz) if now is None else pd.Timestamp(now)
    if index.tz is not None and now.tzinfo is None:
        now = now.tz_localize(index.tz)
    recent = dividends[(index > now - pd.Timedelta(days=365)) & (index <= now)]
    return float(recent.sum()) / float(last_price)


def fetch_dividend_yield(ticker: str, last_price: float | None = None) -> float | None:
    """종목·ETF의 최근 1년 배당수익률. 지수(^로 시작)이거나 조회에 실패하면 None.
    last_price를 주지 않으면 최근 종가를 직접 조회한다(대체 ETF처럼 종가 표에 없는 티커용)."""
    if ticker.startswith("^"):
        return None
    try:
        import yfinance as yf

        tk = yf.Ticker(ticker)
        if last_price is None:
            last_price = float(tk.history(period="5d")["Close"].iloc[-1])
        y = dividend_yield_from_history(tk.dividends, last_price)
    except Exception:
        return None
    return y if 0 <= y < 0.3 else None


def estimate_from_tickers(
    tickers: list[str], months: int, div_tickers: list[str | None] | None = None
) -> MarketEstimate:
    """div_tickers[i]가 있으면 i번째 자산의 배당을 그 티커(추종 ETF)로 계산한다."""
    closes = fetch_closes(tickers, months)
    vols, corr = estimate_from_closes(closes.to_numpy())
    last = closes.iloc[-1]
    div_tickers = div_tickers or [None] * len(tickers)
    divs, sources = [], []
    for t, dt in zip(tickers, div_tickers):
        src = dt or t
        divs.append(fetch_dividend_yield(src, float(last[t]) if src == t else None))
        sources.append(src)
    return MarketEstimate(
        vols=vols,
        corr=corr,
        divs=divs,
        div_sources=sources,
        n_days=len(closes),
        start=str(closes.index[0].date()),
        end=str(closes.index[-1].date()),
    )
