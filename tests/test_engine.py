"""평가 엔진 테스트.

- 기준값 회귀 테스트 (CLAUDE.md §6: 공정가치 약 85.7%, 6개월 상환 약 47%, 만기 손실 약 27%)
- 적정 쿠폰·적정 참여율: 구한 값을 넣어 다시 평가하면 목표값과 일치
- 월지급식: 쿠폰 지급액이 해석값과 일치, 월지급 배리어가 높을수록 가격 하락
- 녹아웃형: 변동성이 거의 0일 때 해석값, 원금 보장, 녹아웃 확률 방향성
"""

import math

import pytest

from elsa.pricing.engine import price
from elsa.schemas import AutocallTerms, KnockoutTerms, MarketData, PricingRequest, SimSettings

BASE_TERMS = AutocallTerms(
    underlyings=["삼성전자", "SK하이닉스"],
    maturity_months=36,
    obs_period_months=6,
    barriers=[0.90, 0.90, 0.85, 0.85, 0.80, 0.75],
    coupon_annual=0.08,
    ki_mode="daily",
    ki_barrier=0.50,
    dummy_coupon=True,
)
BASE_MARKET = MarketData(vols=[0.32, 0.45], divs=[0.02, 0.008], corr=[[1, 0.6], [0.6, 1]], rate=0.028)
FAST = SimSettings(n_paths=20_000, seed=7)


def _run(ts, mk=BASE_MARKET, sim=FAST):
    return price(PricingRequest(term_sheet=ts, market=mk, sim=sim))


def test_baseline_regression():
    r = _run(BASE_TERMS, sim=SimSettings(n_paths=100_000, seed=42))
    assert r.price == pytest.approx(0.857, abs=0.005)
    assert r.redemption_probs[0] == pytest.approx(0.47, abs=0.02)
    assert r.maturity_loss_prob == pytest.approx(0.27, abs=0.02)
    assert sum(r.redemption_probs) + r.maturity_loss_prob == pytest.approx(1.0)
    assert r.stderr < 0.002


def test_same_seed_is_reproducible():
    assert _run(BASE_TERMS).price == _run(BASE_TERMS).price


def test_fair_coupon_hits_target():
    r = _run(BASE_TERMS)
    again = _run(BASE_TERMS.model_copy(update={"coupon_annual": r.fair_coupon_annual}))
    assert again.price == pytest.approx(1.0, abs=1e-9)


def test_fair_coupon_with_custom_target():
    r = _run(BASE_TERMS, sim=FAST.model_copy(update={"target": 0.97}))
    again = _run(
        BASE_TERMS.model_copy(update={"coupon_annual": r.fair_coupon_annual}),
        sim=FAST.model_copy(update={"target": 0.97}),
    )
    assert again.price == pytest.approx(0.97, abs=1e-9)


def test_missing_coupon_gives_fair_coupon_only():
    r = _run(BASE_TERMS.model_copy(update={"coupon_annual": None}))
    assert r.price is None
    assert r.fair_coupon_annual == pytest.approx(_run(BASE_TERMS).fair_coupon_annual)


def test_principal_returned_without_dummy_when_no_ki():
    # 배리어를 아주 높게, 낙인을 아주 낮게: 낙인 없음 → 더미 없으면 원금만, 더미면 쿠폰까지
    ts = BASE_TERMS.model_copy(update={"barriers": [1e9] * 6, "ki_barrier": 1e-6})
    plain = _run(ts.model_copy(update={"dummy_coupon": False}))
    dummy = _run(ts.model_copy(update={"dummy_coupon": True}))
    df = math.exp(-0.028 * 3)
    assert plain.price == pytest.approx(df, rel=1e-9)
    assert dummy.price == pytest.approx(df * (1 + 0.08 * 3), rel=1e-9)
    assert plain.maturity_loss_prob == 0


def test_no_ki_loses_below_final_barrier():
    # 노낙인: 만기 배리어(85%) 미달이면 낙인 여부와 관계없이 워스트 비율만큼 상환
    ts = BASE_TERMS.model_copy(
        update={"ki_mode": "none", "ki_barrier": None, "barriers": [1e9] * 5 + [0.85], "dummy_coupon": False}
    )
    r = _run(ts)
    assert r.early_redemption_prob == 0 and r.ki_unredeemed_prob == 0
    assert 0 < r.maturity_loss_prob < 1
    assert r.avg_loss_redemption < 0.85
    assert r.maturity_loss_prob + r.redemption_probs[-1] == pytest.approx(1.0)


def test_payout_above_principal_is_not_a_loss():
    # 비현실적으로 높은 만기 배리어에서 워스트가 100%를 넘으면 그 비율대로 받되 손실로 세지 않는다
    ts = BASE_TERMS.model_copy(update={"ki_mode": "none", "ki_barrier": None, "barriers": [1e9] * 6})
    r = _run(ts)
    assert 0 < r.maturity_loss_prob < 1
    assert r.maturity_loss_prob + r.redemption_probs[-1] == pytest.approx(1.0)


def test_maturity_ki_is_cheaper_risk_than_daily():
    daily = _run(BASE_TERMS)
    at_maturity = _run(BASE_TERMS.model_copy(update={"ki_mode": "maturity"}))
    assert at_maturity.price > daily.price


def test_monthly_ki_sits_between_daily_and_maturity():
    # 관찰 횟수: 매일(756회) > 매월 말일(36회) > 만기(1회) → 낙인 위험이 줄어 가격은 반대 순서
    daily = _run(BASE_TERMS)
    monthly = _run(BASE_TERMS.model_copy(update={"ki_mode": "monthly"}))
    at_maturity = _run(BASE_TERMS.model_copy(update={"ki_mode": "maturity"}))
    assert daily.price < monthly.price < at_maturity.price
    assert daily.ki_unredeemed_prob > monthly.ki_unredeemed_prob > at_maturity.ki_unredeemed_prob


def test_max_loss_zero_is_principal_protected():
    # 원금지급형(ELB): 노낙인·만기 배리어 85%. 배리어 미달이어도 원금 100% → 손실 없음, 가격 ≥ 할인된 원금
    ts = BASE_TERMS.model_copy(update={
        "barriers": [1e9] * 5 + [0.85], "ki_mode": "none", "ki_barrier": None, "dummy_coupon": False, "max_loss": 0.0,
    })
    r = _run(ts)
    assert r.maturity_loss_prob == 0 and r.avg_loss_redemption is None
    assert r.redemption_probs[-1] == pytest.approx(1.0)
    assert r.price > math.exp(-0.028 * 3)
    assert r.price > _run(ts.model_copy(update={"max_loss": None})).price


def test_partial_max_loss_caps_losses():
    base = _run(BASE_TERMS)
    capped = _run(BASE_TERMS.model_copy(update={"max_loss": 0.1}))
    assert capped.avg_loss_redemption >= 0.9 - 1e-12
    assert capped.price > base.price
    assert capped.maturity_loss_prob <= base.maturity_loss_prob
    # 최대 손실 100%는 제한 없음과 같다
    assert _run(BASE_TERMS.model_copy(update={"max_loss": 1.0})).price == pytest.approx(base.price)


LIZARD_MARKET = MarketData(vols=[1e-6], divs=[0.2], corr=[[1.0]], rate=0.028)  # 워스트가 매년 약 16%씩 감소


def test_lizard_pays_each_period_coupon_and_redeems_with_that_period_only():
    # 결정적 경로: 워스트 0.5년 91.8%, 1년 84.2%, 1.5년 77.3%
    # 배리어 95·90·75, 리자드 80 → 0.5년·1년은 리자드 쿠폰만 받고 유지, 1.5년에 상환(그 회차 쿠폰 + 원금)
    ts = BASE_TERMS.model_copy(update={
        "underlyings": ["A"], "barriers": [0.95, 0.90, 0.75, 0.7, 0.7, 0.7], "ki_mode": "none", "ki_barrier": None,
        "dummy_coupon": False, "coupon_annual": 0.08, "lizard_barriers": [0.80] * 6,
    })
    r = _run(ts, mk=LIZARD_MARKET)
    df = lambda y: math.exp(-0.028 * y)
    expected = 0.08 * 0.5 * (df(0.5) + df(1.0) + df(1.5)) + df(1.5)
    assert r.price == pytest.approx(expected, rel=1e-4)
    assert r.avg_lizard_coupons == pytest.approx(3.0)
    assert r.redemption_probs[2] == pytest.approx(1.0)


def test_super_lizard_only_first_rounds_and_plain_coupon_after():
    # 슈퍼리자드 90(L75)-90(L70)-85-80-75-70: 같은 결정적 경로(0.5년 91.8%, 1년 84.2%, 1.5년 77.3%, 2년 70.9%)
    # 배리어 [0.95, 0.90, 0.75, 0.70 ...], 리자드는 1·2회차만(0.80, 0.80). 3회차(77.3% ≥ 75%)에 상환
    # → 1·2회차 리자드 쿠폰 + 3회차 상환(그 회차 쿠폰) = 쿠폰 3번, 원금은 1.5년
    ts = BASE_TERMS.model_copy(update={
        "underlyings": ["A"], "barriers": [0.95, 0.90, 0.75, 0.7, 0.7, 0.7], "ki_mode": "none", "ki_barrier": None,
        "dummy_coupon": False, "coupon_annual": 0.08, "lizard_barriers": [0.80, 0.80],
    })
    r = _run(ts, mk=LIZARD_MARKET)
    df = lambda y: math.exp(-0.028 * y)
    assert r.price == pytest.approx(0.08 * 0.5 * (df(0.5) + df(1.0) + df(1.5)) + df(1.5), rel=1e-4)
    # 리자드가 1회차에만 있고 2회차에는 없다면 2회차(84.2% < 90%)에는 쿠폰이 없다
    only_first = _run(ts.model_copy(update={"lizard_barriers": [0.80]}), mk=LIZARD_MARKET)
    assert only_first.price == pytest.approx(0.08 * 0.5 * (df(0.5) + df(1.5)) + df(1.5), rel=1e-4)
    # 가운데 회차만 리자드(None, 0.80): 1회차(91.8% < 95%)는 쿠폰 없음
    middle = _run(ts.model_copy(update={"lizard_barriers": [None, 0.80]}), mk=LIZARD_MARKET)
    assert middle.price == pytest.approx(0.08 * 0.5 * (df(1.0) + df(1.5)) + df(1.5), rel=1e-4)


def test_lizard_without_barrier_matches_plain_in_same_path():
    # 같은 경로에서 리자드가 없으면 1.5년에 상환될 때 누적 쿠폰 3회분(12%)을 한 번에 받는다
    ts = BASE_TERMS.model_copy(update={
        "underlyings": ["A"], "barriers": [0.95, 0.90, 0.75, 0.7, 0.7, 0.7], "ki_mode": "none", "ki_barrier": None,
        "dummy_coupon": False, "coupon_annual": 0.08,
    })
    r = _run(ts, mk=LIZARD_MARKET)
    assert r.price == pytest.approx(math.exp(-0.028 * 1.5) * (1 + 0.08 * 1.5), rel=1e-4)
    assert r.avg_lizard_coupons is None


def test_lizard_always_paid_matches_analytic():
    # 리자드 배리어 ~0이면 아직 살아 있는 동안 매 평가일(만기 포함 6번)에 쿠폰 지급
    ts = BASE_TERMS.model_copy(update={
        "barriers": [1e9] * 5 + [1e-4], "ki_mode": "none", "ki_barrier": None, "dummy_coupon": False,
        "lizard_barriers": [1e-9] * 6,
    })
    with_c = _run(ts.model_copy(update={"coupon_annual": 0.06}))
    zero_c = _run(ts.model_copy(update={"coupon_annual": 0.0}))
    annuity = sum(math.exp(-0.028 * 0.5 * k) for k in range(1, 7)) * 0.5
    assert with_c.price - zero_c.price == pytest.approx(0.06 * annuity, rel=1e-9)
    assert with_c.avg_lizard_coupons == pytest.approx(6.0)


def test_lizard_higher_barrier_is_cheaper_and_fair_coupon_hits_target():
    ts = BASE_TERMS.model_copy(update={"lizard_barriers": [0.65] * 6, "coupon_annual": 0.06, "dummy_coupon": False})
    low = _run(ts)
    high = _run(ts.model_copy(update={"lizard_barriers": [0.85] * 6}))
    assert high.price < low.price
    assert 0 < low.avg_lizard_coupons <= 6
    again = _run(ts.model_copy(update={"coupon_annual": low.fair_coupon_annual}))
    assert again.price == pytest.approx(1.0, abs=1e-9)


def test_monthly_coupon_matches_analytic_when_always_paid():
    # 월지급 배리어 ~0, 조기상환 불가 → 매월 쿠폰 지급. 가격 차이 = c × Σ df_m / 12
    ts = BASE_TERMS.model_copy(
        update={"barriers": [1e9] * 6, "monthly_coupon_barrier": 1e-9, "ki_mode": "none", "ki_barrier": None}
    )
    with_c = _run(ts.model_copy(update={"coupon_annual": 0.06}))
    zero_c = _run(ts.model_copy(update={"coupon_annual": 0.0}))
    annuity = sum(math.exp(-0.028 * m / 12) for m in range(1, 37)) / 12
    assert with_c.price - zero_c.price == pytest.approx(0.06 * annuity, rel=1e-9)
    assert with_c.avg_monthly_coupons == pytest.approx(36)


def test_higher_monthly_barrier_is_cheaper():
    ts = BASE_TERMS.model_copy(update={"monthly_coupon_barrier": 0.65, "coupon_annual": 0.06})
    low = _run(ts)
    high = _run(ts.model_copy(update={"monthly_coupon_barrier": 0.85}))
    assert high.price < low.price
    assert 0 < low.avg_monthly_coupons < 36


# --- 녹아웃형 ---------------------------------------------------------------

DET_MARKET = MarketData(vols=[1e-6], divs=[0.0], corr=[[1.0]], rate=0.03)
KO_TERMS = KnockoutTerms(
    underlyings=["삼성전자"], maturity_months=12, strike=1.0, participation=1.0,
    ko_upper=1.37, rebate=0.03, ko_observation="daily", max_loss=0.0,  # 원금지급형(ELB)
)


def test_knockout_deterministic_no_ko():
    # 변동성 0: S_T = e^{0.03}, 녹아웃 없음 → e^{-0.03}(1 + e^{0.03} − 1) = 1
    r = _run(KO_TERMS, mk=DET_MARKET)
    assert r.ko_prob == 0
    assert r.price == pytest.approx(1.0, abs=1e-5)


def test_knockout_deterministic_ko_hit():
    r = _run(KO_TERMS.model_copy(update={"ko_upper": 1.01}), mk=DET_MARKET)
    assert r.ko_prob == 1
    assert r.price == pytest.approx(math.exp(-0.03) * 1.03, rel=1e-9)


def test_knockout_maturity_observation_misses_intraperiod_touch():
    # 만기 관찰이면 기간 중 1.01을 지나도 만기 값(1.0305)이 1.04 미만이면 녹아웃 아님
    r = _run(KO_TERMS.model_copy(update={"ko_upper": 1.04, "ko_observation": "maturity"}), mk=DET_MARKET)
    assert r.ko_prob == 0


def test_knockout_down_and_two_way():
    mk = MarketData(vols=[0.25], divs=[0.01], corr=[[1.0]], rate=0.03)
    down = _run(KO_TERMS.model_copy(update={"ko_upper": None, "ko_lower": 0.70}), mk=mk)
    two = _run(KO_TERMS.model_copy(update={"ko_lower": 0.70}), mk=mk)
    up = _run(KO_TERMS, mk=mk)
    assert two.ko_prob > max(up.ko_prob, down.ko_prob)
    for r in (down, two, up):
        assert r.price >= math.exp(-0.03) - 1e-12  # 원금 보장


@pytest.mark.parametrize(
    "max_loss, expected_payout",
    [
        (None, math.exp(-0.10)),  # 제한 없음: 하락한 만큼 전부 손실 (만기 약 90.5%)
        (0.05, 0.95),  # 최대 5%까지만 손실
        (0.0, 1.0),  # 원금지급형
    ],
)
def test_knockout_max_loss_on_decline(max_loss, expected_payout):
    # 변동성 0, 배당 13% > 금리 3% → 상승형 기초자산이 만기에 e^{-0.10} ≈ 90.5%로 하락, 녹아웃 없음
    mk = MarketData(vols=[1e-6], divs=[0.13], corr=[[1.0]], rate=0.03)
    r = _run(KO_TERMS.model_copy(update={"max_loss": max_loss}), mk=mk)
    assert r.ko_prob == 0
    assert r.price == pytest.approx(math.exp(-0.03) * expected_payout, rel=1e-5)
    assert r.maturity_loss_prob == (0.0 if max_loss == 0 else 1.0)


def test_knockout_down_type_loses_when_price_rises():
    # 하락형: 기초자산이 오르면(e^{0.03}) 불리한 방향. 제한 없음이면 상승폭만큼 손실
    mk = MarketData(vols=[1e-6], divs=[0.0], corr=[[1.0]], rate=0.03)
    ts = KO_TERMS.model_copy(update={"ko_upper": None, "ko_lower": 0.7, "max_loss": None})
    r = _run(ts, mk=mk)
    assert r.price == pytest.approx(math.exp(-0.03) * (1 - (math.exp(0.03) - 1)), rel=1e-5)


def test_knockout_fair_participation_hits_target():
    mk = MarketData(vols=[0.25], divs=[0.01], corr=[[1.0]], rate=0.03)
    ts = KO_TERMS.model_copy(update={"participation": None, "min_return": 0.005})
    r = _run(ts, mk=mk)
    assert r.price is None and r.fair_participation is not None
    again = _run(ts.model_copy(update={"participation": r.fair_participation}), mk=mk)
    assert again.price == pytest.approx(1.0, abs=1e-6)


def test_knockout_multi_asset_uses_worst():
    mk = MarketData(vols=[0.25, 0.25], divs=[0.0, 0.0], corr=[[1, 0.5], [0.5, 1]], rate=0.03)
    single = _run(KO_TERMS, mk=MarketData(vols=[0.25], divs=[0.0], corr=[[1.0]], rate=0.03))
    multi = _run(KO_TERMS.model_copy(update={"underlyings": ["A", "B"]}), mk=mk)
    assert multi.ko_prob < single.ko_prob  # 워스트가 녹아웃 배리어에 닿기 더 어렵다
