"""조건 검증 테스트: 오류·경고가 필요한 경우에 정확히 나오고, 정상 조건은 통과하는지 확인."""

from elsa.schemas import AutocallTerms, KnockoutTerms, MarketData, PricingRequest, SimSettings
from elsa.validation import validate_market, validate_request, validate_sim, validate_terms

AC = AutocallTerms(
    underlyings=["삼성전자", "SK하이닉스"], maturity_months=36, obs_period_months=6,
    barriers=[0.9, 0.9, 0.85, 0.85, 0.8, 0.75], coupon_annual=0.08, ki_mode="daily", ki_barrier=0.5,
)
KO = KnockoutTerms(underlyings=["삼성전자"], maturity_months=12, participation=1.0, ko_upper=1.37, rebate=0.03)
MK2 = MarketData(vols=[0.32, 0.45], divs=[0.02, 0.008], corr=[[1, 0.6], [0.6, 1]], rate=0.028)


def test_valid_request_passes():
    r = validate_request(PricingRequest(term_sheet=AC, market=MK2))
    assert r.ok and not r.warnings


def test_barrier_count_message():
    r = validate_terms(AC.model_copy(update={"barriers": [0.9] * 5}))
    assert r.errors == ["평가가 6회이므로 상환 배리어를 6개 입력해 주세요. 지금은 5개입니다."]


def test_maturity_not_divisible():
    r = validate_terms(AC.model_copy(update={"obs_period_months": 5}))
    assert "나누어떨어지지" in r.errors[0]


def test_underlying_count_and_blank_name():
    assert not validate_terms(AC.model_copy(update={"underlyings": ["a", "b", "c", "d"]})).ok
    assert "비어 있습니다" in validate_terms(AC.model_copy(update={"underlyings": ["삼성전자", " "]})).errors[0]


def test_ki_checks():
    assert not validate_terms(AC.model_copy(update={"ki_barrier": None})).ok
    assert validate_terms(AC.model_copy(update={"ki_mode": "none", "ki_barrier": None})).ok
    warn = validate_terms(AC.model_copy(update={"ki_barrier": 0.8}))
    assert warn.ok and "낙인 배리어" in warn.warnings[0]


def test_coupon_optional_but_not_negative():
    assert validate_terms(AC.model_copy(update={"coupon_annual": None})).ok
    assert not validate_terms(AC.model_copy(update={"coupon_annual": -0.01})).ok


def test_monthly_barrier_range():
    assert validate_terms(AC.model_copy(update={"monthly_coupon_barrier": 0.65})).ok
    assert not validate_terms(AC.model_copy(update={"monthly_coupon_barrier": 0})).ok


def test_lizard_checks():
    assert validate_terms(AC.model_copy(update={"lizard_barriers": [0.75, 0.70]})).ok
    assert validate_terms(AC.model_copy(update={"lizard_barriers": [None, 0.70]})).ok
    assert not validate_terms(AC.model_copy(update={"lizard_barriers": [0.0]})).ok
    assert not validate_terms(AC.model_copy(update={"lizard_barriers": [0.75] * 7})).ok  # 평가 6회 초과
    both = validate_terms(AC.model_copy(update={"lizard_barriers": [0.75], "monthly_coupon_barrier": 0.65}))
    assert any("함께 쓸 수 없습니다" in e for e in both.errors)
    high = validate_terms(AC.model_copy(update={"lizard_barriers": [0.95, 0.5]}))  # 1회차 배리어 90% ≤ 95%
    assert high.ok and "1회차" in high.warnings[0]


def test_max_loss_range():
    assert validate_terms(AC.model_copy(update={"max_loss": 0.0})).ok
    assert validate_terms(AC.model_copy(update={"max_loss": 0.1})).ok
    assert not validate_terms(AC.model_copy(update={"max_loss": 1.2})).ok
    assert not validate_terms(AC.model_copy(update={"max_loss": -0.1})).ok


def test_knockout_checks():
    assert validate_terms(KO).ok
    assert not validate_terms(KO.model_copy(update={"ko_upper": None})).ok
    assert not validate_terms(KO.model_copy(update={"ko_upper": 0.9})).ok
    assert not validate_terms(KO.model_copy(update={"ko_lower": 1.1})).ok
    assert validate_terms(KO.model_copy(update={"ko_lower": 0.7, "participation": None})).ok
    # 최대 손실률: 비움(제한 없음)·0·부분 모두 허용, 범위 밖은 오류
    assert validate_terms(KO.model_copy(update={"max_loss": 0.1})).ok
    assert not validate_terms(KO.model_copy(update={"max_loss": 1.5})).ok
    # 최소 수익률이 있으면 원금 손실이 없어야 한다 (최대 손실률 0만 허용)
    assert validate_terms(KO.model_copy(update={"min_return": 0.005, "max_loss": 0.0})).ok
    assert not validate_terms(KO.model_copy(update={"min_return": 0.005})).ok
    assert not validate_terms(KO.model_copy(update={"min_return": 0.005, "max_loss": 0.1})).ok


def test_market_checks():
    assert not validate_market(MK2, 3).ok
    assert not validate_market(MK2.model_copy(update={"vols": [0.32, 0]}), 2).ok
    assert not validate_market(MK2.model_copy(update={"corr": [[1, 1.2], [1.2, 1]]}), 2).ok
    assert not validate_market(MK2.model_copy(update={"corr": [[1, 0.6], [0.5, 1]]}), 2).ok


def test_non_positive_definite_corr():
    bad = MarketData(
        vols=[0.2] * 3, divs=[0] * 3, rate=0.03,
        corr=[[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]],
    )
    r = validate_market(bad, 3)
    assert "양의 정부호" in r.errors[0]


def test_sim_path_range():
    assert not validate_sim(SimSettings(n_paths=5_000)).ok
    assert not validate_sim(SimSettings(n_paths=200_000)).ok
    assert validate_sim(SimSettings(n_paths=50_000)).ok
