"""ELS 로직 점검: 극단적인 입력에서 평가 결과가 상식대로 나오는지 확인한다.

콜옵션 검증으로 확인할 수 없는 조기상환·낙인 판정, 관측일 처리, 다자산 상관계수·워스트 계산을
기존 평가 함수 price()만 호출해 점검한다. 기대값은 엔진 코드를 쓰지 않고 공식으로 따로 계산한다.
엔진이 틀렸을 때 기대값까지 함께 틀어지면 점검이 되지 않기 때문이다.

판정은 세 가지다.
- 손 계산값 비교: |차이| ≤ max(1.96 × 표준오차, TOLERANCE). 변동성이 0에 가깝거나 전 경로가 같은
  곳에서 상환되면 표준오차가 사실상 0이라, float32 경로 계산의 반올림 오차를 TOLERANCE로 흡수한다
- 차이 0 비교: |차이| ≤ 1.96 × √(se₁² + se₂²). 자산 수가 다르면 같은 시드여도 난수 배치가 달라
  공통 난수가 아니므로 두 표준오차를 합친다. 95% 기준이라 시드에 따라 가끔 어긋날 수 있다
- 방향 비교: 같은 시드(공통 난수)로 조건만 바꿔 대소 관계를 본다

- EXACT_PATHS, COMPARE_PATHS, DIRECTION_PATHS: 판정 종류별 경로 수
- TOLERANCE, NEAR_ZERO_VOL, UNREACHABLE, NEAR_ONE_CORR: 판정 허용오차와 극단 입력값
- LogicCheck: 점검 한 건 (입력 조건, 기대 결과, 실제 결과, 판정)
- within_tolerance, within_ci, is_increasing, is_decreasing, is_hump: 판정 함수
- check_ki_removed(seed): (1) 낙인을 닿을 수 없게 하면 가격이 오르거나 같다
- check_zero_vol(seed): (2) 변동성 ≈ 0에서 손 계산값과 일치 (상환 회차, 낙인, 더미, 워스트, 녹아웃)
- check_first_redemption(seed): (3) 1회차 무조건 상환이면 채권 현재가치와 일치
- check_perfect_correlation(seed): (4) 상관계수 ≈ 1이면 1자산 상품과 같은 가격
- check_vol_direction(seed): (5) 변동성에 따른 상품별 가격 방향
- check_corr_direction(seed): (6) 상관계수가 낮을수록 워스트 구조 가격 하락
- check_observation_frequency(seed): (7) 관측이 잦을수록 낙인·녹아웃이 많이 발생
- LOGIC_GROUPS: (이름, 점검 함수) 목록. 화면이 그룹별로 진행 표시·캐시를 한다
- run_logic_checks(seed): 모든 점검 실행
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from elsa.pricing.engine import price
from elsa.pricing.verification import Z_95, CallParams, bs_call
from elsa.schemas import (
    AutocallTerms,
    KnockoutTerms,
    MarketData,
    PricingRequest,
    PricingResult,
    SimSettings,
)

EXACT_PATHS = 10_000  # 손 계산값 비교. 경로가 사실상 하나로 정해져 적어도 된다
COMPARE_PATHS = 100_000  # 차이 0 비교. 표준오차만큼 판정 폭이 정해진다
DIRECTION_PATHS = 50_000  # 방향 비교. 공통 난수라 차이가 안정적이다

TOLERANCE = 1e-6  # 0.0001%p
NEAR_ZERO_VOL = 1e-4
UNREACHABLE = 1e-4  # 닿을 수 없는 낙인, 반드시 충족되는 배리어로 쓰는 0.01%
NEAR_ONE_CORR = 1.0 - 1e-9  # 1.0은 촐레스키 분해가 실패하고 0.99는 워스트 효과가 남는다
NO_KNOCKOUT = 1e6  # 닿을 수 없는 녹아웃 배리어

RATE = 0.028
KO_RATE = 0.03
DROP_BARRIERS = [1.0, 1.0, 0.9, 0.9, 0.85, 0.8]  # 연 6% 하락 경로가 3회차에 처음 넘는 배리어
HIGH_BARRIERS = [0.95, 0.95, 0.9, 0.9, 0.85, 0.8]  # 연 20~30% 하락 경로가 끝까지 넘지 못하는 배리어

BASE_TERMS = AutocallTerms(
    underlyings=["A", "B"],
    maturity_months=36,
    obs_period_months=6,
    barriers=[0.90, 0.90, 0.85, 0.85, 0.80, 0.75],
    coupon_annual=0.08,
    ki_mode="daily",
    ki_barrier=0.50,
    dummy_coupon=True,
)
BASE_MARKET = MarketData(vols=[0.32, 0.45], divs=[0.02, 0.008], corr=[[1, 0.6], [0.6, 1]], rate=RATE)
SINGLE_TERMS = BASE_TERMS.model_copy(update={"underlyings": ["A"]})
KO_TERMS = KnockoutTerms(
    underlyings=["A"], maturity_months=12, participation=1.0, ko_upper=1.37, rebate=0.03, max_loss=0.0,
)


@dataclass
class LogicCheck:
    """점검 한 건. uses_ci면 95% 신뢰구간 판정이라 시드에 따라 가끔 어긋날 수 있다."""

    name: str
    condition: str
    expected: str
    actual: str
    passed: bool
    uses_ci: bool = False


def within_tolerance(actual: float, expected: float, stderr: float) -> bool:
    return abs(actual - expected) <= max(Z_95 * stderr, TOLERANCE)


def within_ci(diff: float, stderr: float) -> bool:
    return abs(diff) <= Z_95 * stderr


def is_increasing(values: Sequence[float]) -> bool:
    return all(a < b for a, b in zip(values, values[1:]))


def is_decreasing(values: Sequence[float]) -> bool:
    return all(a > b for a, b in zip(values, values[1:]))


def is_hump(values: Sequence[float]) -> bool:
    """오르다가 내려가고, 정점이 양 끝이 아닌 중간에 있다."""
    peak = max(range(len(values)), key=values.__getitem__)
    return 0 < peak < len(values) - 1 and is_increasing(values[: peak + 1]) and is_decreasing(values[peak:])


def _pct(x: float) -> str:
    return f"{x * 100:.3f}%"


def _series(values: Sequence[float]) -> str:
    return " → ".join(f"{v * 100:.2f}%" for v in values)


def _pv(rate: float, years: float) -> float:
    """기대값 계산용 연속복리 현재가치 계수. 엔진 코드를 쓰지 않는다."""
    return math.exp(-rate * years)


def _ratio(rate: float, div: float, years: float) -> float:
    """변동성 ≈ 0일 때 최초기준가격 대비 만기 비율."""
    return math.exp((rate - div - 0.5 * NEAR_ZERO_VOL**2) * years)


def _market(n_assets: int, vol: float, div: float, rate: float, corr: float = NEAR_ONE_CORR) -> MarketData:
    """자산 n개가 변동성·배당·상관계수를 공유하는 시장."""
    matrix = [[1.0 if i == j else corr for j in range(n_assets)] for i in range(n_assets)]
    return MarketData(vols=[vol] * n_assets, divs=[div] * n_assets, corr=matrix, rate=rate)


def _price(ts: AutocallTerms | KnockoutTerms, market: MarketData, n_paths: int, seed: int) -> PricingResult:
    return price(PricingRequest(term_sheet=ts, market=market, sim=SimSettings(n_paths=n_paths, seed=seed)))


def _exact(
    name: str, condition: str, formula: str, expected: float, r: PricingResult, extra: bool = True, note: str = "",
) -> LogicCheck:
    """손 계산값 비교 한 건. extra는 가격 외에 함께 맞아야 하는 조건."""
    return LogicCheck(
        name=name,
        condition=condition,
        expected=f"{formula} = {_pct(expected)}",
        actual=_pct(r.price) + note,
        passed=bool(within_tolerance(r.price, expected, r.stderr) and extra),
    )


def _same_price(name: str, condition: str, single: PricingResult, many: PricingResult) -> LogicCheck:
    diff = many.price - single.price
    stderr = math.hypot(single.stderr, many.stderr)
    return LogicCheck(
        name=name,
        condition=condition,
        expected="1자산 상품과 차이 0",
        actual=f"{_pct(single.price)} / {_pct(many.price)}, 차이 {diff * 100:+.3f}%p (95% 범위 ±{Z_95 * stderr * 100:.3f}%p)",
        passed=bool(within_ci(diff, stderr)),
        uses_ci=True,
    )


def check_ki_removed(seed: int) -> list[LogicCheck]:
    """낙인 배리어만 낮춘다. ki_mode="none"은 보호 자체를 없애는 노낙인이라 쓰면 안 된다."""
    base = _price(BASE_TERMS, BASE_MARKET, DIRECTION_PATHS, seed)
    free = _price(BASE_TERMS.model_copy(update={"ki_barrier": UNREACHABLE}), BASE_MARKET, DIRECTION_PATHS, seed)
    return [LogicCheck(
        name="(1) 낙인 제거",
        condition="기준 상품의 낙인 배리어 50% → 0.01%",
        expected="가격 ≥ 기준 상품, 만기 손실 확률 0%",
        actual=f"{_pct(base.price)} → {_pct(free.price)}, 만기 손실 {free.maturity_loss_prob * 100:.2f}%",
        passed=bool(free.price >= base.price and free.maturity_loss_prob == 0),
    )]


def check_zero_vol(seed: int) -> list[LogicCheck]:
    """변동성 ≈ 0이면 경로가 금리·배당으로 정해지므로 판정 분기별로 손 계산값과 비교한다."""
    vol = NEAR_ZERO_VOL
    n = EXACT_PATHS
    drop = SINGLE_TERMS.model_copy(update={"barriers": DROP_BARRIERS})
    high = SINGLE_TERMS.model_copy(update={"barriers": HIGH_BARRIERS})
    ladder_value = _pv(RATE, 1.5) * 1.12
    rows = []

    r = _price(drop, _market(1, vol, 0.088, RATE), n, seed)
    rows.append(_exact(
        "(2a) 3회차 상환", "1자산, σ 0.01%, q 8.8%, 배리어 100-100-90-90-85-80",
        "3회차 100%, 18개월, e^(−1.5r) × 1.12", ladder_value, r,
        extra=math.isclose(r.redemption_probs[2], 1.0) and math.isclose(r.expected_life_months, 18.0),
        note=f", 3회차 {r.redemption_probs[2] * 100:.0f}%, {r.expected_life_months:g}개월",
    ))
    r = _price(high, _market(1, vol, 0.228, RATE), n, seed)
    rows.append(_exact(
        "(2b) 낙인 미발생·더미 쿠폰", "σ 0.01%, q 22.8%, 배리어 95-95-90-90-85-80, 만기 54.9%",
        "e^(−3r) × 1.24", _pv(RATE, 3) * 1.24, r,
    ))
    r = _price(high.model_copy(update={"dummy_coupon": False}), _market(1, vol, 0.228, RATE), n, seed)
    rows.append(_exact("(2c) 낙인 미발생·더미 없음", "(2b)에서 더미 쿠폰 없음", "e^(−3r)", _pv(RATE, 3), r))
    r = _price(high, _market(1, vol, 0.328, RATE), n, seed)
    rows.append(_exact(
        "(2d) 낙인 발생·손실", "σ 0.01%, q 32.8%, 만기 40.7%로 낙인 발생",
        "e^(−3r) × 만기 비율", _pv(RATE, 3) * _ratio(RATE, 0.328, 3), r,
    ))
    r = _price(high.model_copy(update={"max_loss": 0.2}), _market(1, vol, 0.328, RATE), n, seed)
    rows.append(_exact("(2e) 최대 손실률", "(2d)에 최대 손실률 20%", "e^(−3r) × 0.8", _pv(RATE, 3) * 0.8, r))

    worst_terms = BASE_TERMS.model_copy(update={"barriers": DROP_BARRIERS})
    prices = []
    for divs in ([0.0, 0.088], [0.088, 0.0]):
        market = MarketData(vols=[vol, vol], divs=divs, corr=BASE_MARKET.corr, rate=RATE)
        prices.append(_price(worst_terms, market, n, seed))
    rows.append(LogicCheck(
        name="(2f) 워스트 선택",
        condition="2자산 중 한쪽만 q 8.8%, 자산 순서를 바꿔 2회",
        expected=f"두 번 모두 (2a)와 같은 {_pct(ladder_value)}",
        actual=" / ".join(_pct(p.price) for p in prices),
        passed=all(within_tolerance(p.price, ladder_value, p.stderr) for p in prices),
    ))

    ko_market = _market(1, vol, 0.0, KO_RATE)
    r = _price(KO_TERMS, ko_market, n, seed)
    rows.append(_exact(
        "(2g) 녹아웃 미발생", "녹아웃형, σ 0.01%, 상승 배리어 137%, 12개월",
        "e^(−r) × 만기 비율", _pv(KO_RATE, 1) * _ratio(KO_RATE, 0.0, 1), r,
        extra=r.ko_prob == 0,
    ))
    r = _price(KO_TERMS.model_copy(update={"ko_upper": 1.02}), ko_market, n, seed)
    rows.append(_exact(
        "(2h) 녹아웃 발생", "(2g)에서 상승 배리어 102%, 리베이트 3%",
        "e^(−r) × 1.03", _pv(KO_RATE, 1) * 1.03, r, extra=r.ko_prob == 1,
    ))
    return rows


def check_first_redemption(seed: int) -> list[LogicCheck]:
    """1회차 배리어를 낮춰 전 경로를 6개월에 상환시키면 경로와 무관한 채권이 된다."""
    n = EXACT_PATHS
    barriers = [UNREACHABLE, *BASE_TERMS.barriers[1:]]
    terms = BASE_TERMS.model_copy(update={"barriers": barriers})
    monthly_value = _pv(RATE, 0.5) + sum(_pv(RATE, m / 12) * 0.08 / 12 for m in range(1, 7))
    cases = [
        ("(3a) 일반형", "1회차 배리어 0.01%", "e^(−0.5r) × 1.04", _pv(RATE, 0.5) * 1.04, terms),
        ("(3b) 월지급식", "(3a)에 월지급 배리어 0.01%", "월 쿠폰 6회 + 6개월 뒤 원금의 현재가치", monthly_value,
         terms.model_copy(update={"monthly_coupon_barrier": UNREACHABLE})),
        ("(3c) 리자드", "(3a)에 1회차 리자드 0.01%", "e^(−0.5r) × 1.04", _pv(RATE, 0.5) * 1.04,
         terms.model_copy(update={"lizard_barriers": [UNREACHABLE, 0.70]})),
    ]
    rows = []
    for name, condition, formula, value, ts in cases:
        r = _price(ts, BASE_MARKET, n, seed)
        rows.append(_exact(
            name, condition, f"1회차 100%, 6개월, {formula}", value, r,
            extra=math.isclose(r.redemption_probs[0], 1.0) and math.isclose(r.expected_life_months, 6.0),
            note=f", 1회차 {r.redemption_probs[0] * 100:.0f}%, {r.expected_life_months:g}개월",
        ))
    return rows


def check_perfect_correlation(seed: int) -> list[LogicCheck]:
    """상관계수 ≈ 1, 변동성·배당이 같으면 워스트가 곧 자산 하나라 1자산 상품과 같아야 한다."""
    n = COMPARE_PATHS
    single = _price(SINGLE_TERMS, _market(1, 0.3, 0.01, RATE), n, seed)
    two = _price(BASE_TERMS, _market(2, 0.3, 0.01, RATE), n, seed)
    three = _price(BASE_TERMS.model_copy(update={"underlyings": ["A", "B", "C"]}), _market(3, 0.3, 0.01, RATE), n, seed)
    ko_single = _price(KO_TERMS, _market(1, 0.3, 0.01, KO_RATE), n, seed)
    ko_two = _price(KO_TERMS.model_copy(update={"underlyings": ["A", "B"]}), _market(2, 0.3, 0.01, KO_RATE), n, seed)
    condition = "ρ 0.999999999, σ 30%, q 1%"
    return [
        _same_price("(4a) 2자산", f"일반형 2자산 vs 1자산, {condition}", single, two),
        _same_price("(4b) 3자산", f"일반형 3자산 vs 1자산, {condition}", single, three),
        _same_price("(4c) 녹아웃형", f"녹아웃형 2자산 vs 1자산, {condition}", ko_single, ko_two),
    ]


def check_vol_direction(seed: int) -> list[LogicCheck]:
    """일반형은 σ 20%부터 본다. 그보다 낮으면 상환이 늦어져 쿠폰을 더 받는 효과로 가격이 오른다."""
    n = DIRECTION_PATHS
    autocall_vols = [0.2, 0.3, 0.4, 0.5, 0.6]
    ko_vols = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    autocall = [_price(BASE_TERMS, _market(2, v, 0.01, RATE, corr=0.6), n, seed).price for v in autocall_vols]
    ko = [_price(KO_TERMS, _market(1, v, 0.01, KO_RATE), n, seed).price for v in ko_vols]

    no_ko_terms = KO_TERMS.model_copy(update={"ko_upper": NO_KNOCKOUT})
    no_ko = [_price(no_ko_terms, _market(1, v, 0.01, KO_RATE), n, seed) for v in ko_vols]
    exact = [_pv(KO_RATE, 1) + bs_call(CallParams(1.0, 1.0, v, KO_RATE, 0.01, 12)) for v in ko_vols]
    prices = [r.price for r in no_ko]
    z_max = max(abs(r.price - e) / r.stderr for r, e in zip(no_ko, exact))

    return [
        LogicCheck(
            name="(5a) 일반형", condition="기준 상품, 양쪽 σ 20 → 60%", expected="계속 하락",
            actual=_series(autocall), passed=is_decreasing(autocall),
        ),
        LogicCheck(
            name="(5b) 녹아웃형", condition="상승 배리어 137% 매일 관찰, σ 5 → 60%",
            expected="상승 후 하락 (정점이 중간)", actual=_series(ko), passed=is_hump(ko),
        ),
        LogicCheck(
            name="(5c) 녹아웃 불가능", condition="녹아웃형, 배리어를 닿을 수 없게, σ 5 → 60%",
            expected="계속 상승, 할인된 원금 + BS 콜 가격과 일치",
            actual=f"{_series(prices)}, BS 대비 최대 {z_max:.2f} 표준오차",
            passed=is_increasing(prices) and all(
                within_ci(r.price - e, r.stderr) for r, e in zip(no_ko, exact)
            ),
            uses_ci=True,
        ),
    ]


def check_corr_direction(seed: int) -> list[LogicCheck]:
    """워스트 구조는 상관계수가 낮을수록 가격이 내려간다. 녹아웃형은 수익도 녹아웃도 함께 줄어
    이론상 방향이 정해지지 않으므로 점검하지 않는다."""
    rhos = [0.9, 0.6, 0.3, 0.0, -0.3]
    prices = []
    for rho in rhos:
        market = BASE_MARKET.model_copy(update={"corr": [[1.0, rho], [rho, 1.0]]})
        prices.append(_price(BASE_TERMS, market, DIRECTION_PATHS, seed).price)
    return [LogicCheck(
        name="(6) 상관계수 하락", condition="기준 상품, ρ 0.9 → −0.3", expected="계속 하락",
        actual=_series(prices), passed=is_decreasing(prices),
    )]


def check_observation_frequency(seed: int) -> list[LogicCheck]:
    """같은 경로에서 관측일이 많을수록 낙인·녹아웃이 더 자주 잡혀야 한다."""
    n = DIRECTION_PATHS
    modes = ("daily", "monthly", "maturity")
    ki = [_price(BASE_TERMS.model_copy(update={"ki_mode": m}), BASE_MARKET, n, seed).price for m in modes]
    market = _market(1, 0.3, 0.01, KO_RATE)
    daily = _price(KO_TERMS, market, n, seed).ko_prob
    maturity = _price(KO_TERMS.model_copy(update={"ko_observation": "maturity"}), market, n, seed).ko_prob
    return [
        LogicCheck(
            name="(7a) 낙인 관측", condition="기준 상품, 낙인 관측 매일 / 매월 말일 / 만기",
            expected="가격: 매일 ≤ 매월 ≤ 만기",
            actual=" ≤ ".join(_pct(p) for p in ki), passed=ki[0] <= ki[1] <= ki[2],
        ),
        LogicCheck(
            name="(7b) 녹아웃 관측", condition="녹아웃형, σ 30%, 관측 매일 / 만기",
            expected="녹아웃 확률: 매일 ≥ 만기",
            actual=f"{daily * 100:.2f}% / {maturity * 100:.2f}%", passed=daily >= maturity,
        ),
    ]


LOGIC_GROUPS: tuple[tuple[str, Callable[[int], list[LogicCheck]]], ...] = (
    ("낙인 제거", check_ki_removed),
    ("변동성 0", check_zero_vol),
    ("1회차 상환", check_first_redemption),
    ("상관계수 1", check_perfect_correlation),
    ("변동성 방향", check_vol_direction),
    ("상관계수 방향", check_corr_direction),
    ("관측 빈도", check_observation_frequency),
)


def run_logic_checks(seed: int) -> list[LogicCheck]:
    return [check for _, group in LOGIC_GROUPS for check in group(seed)]
