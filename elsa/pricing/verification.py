"""엔진 검증: 해석해가 있는 유럽형 콜옵션으로 몬테카를로 엔진의 정확도를 확인한다.

새 시뮬레이터를 만들지 않고 engine.py의 경로 생성(_iter_batches), 할인(discount_factor),
쌍 평균 통계(_PairStats)를 그대로 쓰고 페이오프만 max(S_T − K, 0)로 바꾼다. 경로 생성·할인을
확인하는 검증이며, 낙인·조기상환 판정, 월중 최저·최고, 상관계수·워스트 계산은 확인하지 못한다.

표준오차는 대조변수 쌍 평균 기준이다. 쌍을 이룬 경로는 서로 독립이 아니라서 경로별 표준편차를
쓰면 표준오차를 크게 잡아 신뢰구간이 넓어진다. 그 값은 대조변수 효과를 보여 주는 비교용으로만 쓴다.

- CONVERGENCE_NS, GRID_MONEYNESS, GRID_VOLS: 수렴 그래프의 N, 일괄 검증의 행사가 비율·변동성
- CallParams: 콜옵션 조건 (단일 기초자산, 금리·배당은 연속복리, 만기는 개월)
- CallCheck: MC와 BS 비교 한 건 (가격, 오차, 표준오차, 신뢰구간, 판정)
- VerificationReport: 기본 조건, 수렴, 일괄 검증 결과 묶음
- months_from_years(years): 년 → 가장 가까운 개월 (엔진 격자가 월 단위)
- moneyness(s0, strike): 내가격 / 등가격 / 외가격
- bs_call(params): 배당 포함 Black-Scholes 콜 가격
- mc_call(params, n_paths, seed): 기존 엔진으로 계산한 콜 가격과 BS 비교
- mc_call_strikes(params, strikes, n_paths, seed): 같은 경로로 여러 행사가를 한 번에 비교
- run_verification(params, n_paths, seed): 기본 조건 비교 + 수렴 + 일괄 검증
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from elsa.pricing.engine import _iter_batches, _PairStats, discount_factor
from elsa.schemas import MarketData, SimSettings

Z_95 = 1.96
CONVERGENCE_NS = (1_000, 5_000, 10_000, 50_000, 100_000, 500_000)
GRID_MONEYNESS = (0.8, 0.9, 1.0, 1.1, 1.2)  # 행사가 / 현재가. S0 = 100이면 K = 80~120
GRID_VOLS = (0.1, 0.2, 0.3)


@dataclass(frozen=True)
class CallParams:
    """유럽형 콜옵션 조건. 금리·배당은 연속복리, 변동성은 연율, 비율은 소수."""

    s0: float
    strike: float
    vol: float
    rate: float
    div: float
    months: int

    @property
    def years(self) -> float:
        return self.months / 12.0


@dataclass
class CallCheck:
    """MC와 BS 비교 한 건. passed는 모든 경로의 페이오프가 같아(표준오차 0) 판정할 수 없으면 None."""

    strike: float
    vol: float
    n_paths: int
    mc: float
    bs: float
    diff: float
    rel_error: float | None  # BS 가격이 사실상 0이면 None
    stderr: float  # 대조변수 쌍 평균 기준
    stderr_independent: float  # 경로가 서로 독립이라고 볼 때의 표준편차 / √N
    ci_low: float
    ci_high: float
    passed: bool | None


@dataclass
class VerificationReport:
    params: CallParams
    seed: int
    main: CallCheck
    convergence: list[CallCheck]
    grid: list[CallCheck]


def months_from_years(years: float) -> int:
    """엔진 격자가 월 단위라 가장 가까운 개월로 맞춘다 (최소 1개월)."""
    return max(1, round(years * 12))


def moneyness(s0: float, strike: float) -> str:
    """콜옵션 기준 표기. 행사가가 현재가보다 낮으면 내가격, 높으면 외가격."""
    if math.isclose(s0, strike):
        return "등가격"
    return "내가격" if strike < s0 else "외가격"


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_call(p: CallParams) -> float:
    """배당수익률 q를 포함한 Black-Scholes 콜 가격."""
    t = p.years
    vol_t = p.vol * math.sqrt(t)
    d1 = (math.log(p.s0 / p.strike) + (p.rate - p.div + 0.5 * p.vol**2) * t) / vol_t
    d2 = d1 - vol_t
    return p.s0 * math.exp(-p.div * t) * _norm_cdf(d1) - p.strike * math.exp(-p.rate * t) * _norm_cdf(d2)


def mc_call_strikes(p: CallParams, strikes: Sequence[float], n_paths: int, seed: int) -> list[CallCheck]:
    """기존 경로 생성·할인·쌍 평균 통계로 행사가별 콜 가격을 계산해 BS와 비교한다.

    같은 시드·변동성이면 행사가와 무관하게 경로가 같으므로 시뮬레이션은 한 번만 한다.
    """
    market = MarketData(vols=[p.vol], divs=[p.div], corr=[[1.0]], rate=p.rate)
    sim = SimSettings(n_paths=n_paths, seed=seed)
    df = discount_factor(p.rate, p.months)
    pair_stats = [_PairStats() for _ in strikes]
    sums = np.zeros((len(strikes), 2))  # 행사가별 ΣPV, ΣPV² (경로별)
    total = 0

    for n, months_iter in _iter_batches(market, p.months, sim):
        w_T = None
        for _, w_end, _, _ in months_iter:
            w_T = w_end
        s_T = p.s0 * w_T  # 엔진 경로는 시작 가격 1로 정규화돼 있다
        total += n
        for i, strike in enumerate(strikes):
            pv = df * np.maximum(s_T - strike, 0.0)
            pair_stats[i].add(pv, np.zeros_like(pv))
            sums[i] += pv.sum(), (pv * pv).sum()

    out = []
    for i, strike in enumerate(strikes):
        mean = pair_stats[i].mean()[0]
        var = max(sums[i, 1] / total - (sums[i, 0] / total) ** 2, 0.0) * total / (total - 1)
        out.append(_compare(replace(p, strike=strike), total, mean, pair_stats[i].stderr(0.0),
                            math.sqrt(var / total)))
    return out


def mc_call(p: CallParams, n_paths: int, seed: int) -> CallCheck:
    return mc_call_strikes(p, [p.strike], n_paths, seed)[0]


def _compare(p: CallParams, n_paths: int, mc: float, stderr: float, stderr_independent: float) -> CallCheck:
    bs = bs_call(p)
    mc = float(mc)  # numpy 값이 결과에 섞이지 않게 한다 (화면 캐시는 기본 타입만 다룬다)
    ci_low, ci_high = mc - Z_95 * stderr, mc + Z_95 * stderr
    return CallCheck(
        strike=p.strike,
        vol=p.vol,
        n_paths=n_paths,
        mc=mc,
        bs=bs,
        diff=mc - bs,
        rel_error=(mc - bs) / bs if bs > 1e-12 * p.s0 else None,
        stderr=stderr,
        stderr_independent=stderr_independent,
        ci_low=ci_low,
        ci_high=ci_high,
        passed=None if stderr == 0.0 else ci_low <= bs <= ci_high,
    )


def run_verification(p: CallParams, n_paths: int, seed: int) -> VerificationReport:
    """기본 조건 비교, N별 수렴, 행사가 × 변동성 일괄 검증을 모두 계산한다."""
    grid = []
    for vol in GRID_VOLS:
        strikes = [p.s0 * m for m in GRID_MONEYNESS]
        grid += mc_call_strikes(replace(p, vol=vol), strikes, n_paths, seed)
    return VerificationReport(
        params=p,
        seed=seed,
        main=mc_call(p, n_paths, seed),
        convergence=[mc_call(p, n, seed) for n in CONVERGENCE_NS],
        grid=grid,
    )
