"""민감도 분석 테스트: 시나리오 이동이 맞게 적용되고, 방향성이 금융적으로 타당한지 확인."""

import pytest

from elsa.pricing.engine import price
from elsa.pricing.sensitivity import fair_sensitivity, run_sensitivity, shift_market
from elsa.schemas import AutocallTerms, KnockoutTerms, MarketData, PricingRequest, SimSettings

MK = MarketData(vols=[0.32, 0.45], divs=[0.02, 0.008], corr=[[1, 0.6], [0.6, 1]], rate=0.028)
AC = AutocallTerms(
    underlyings=["삼성전자", "SK하이닉스"], maturity_months=36, obs_period_months=6,
    barriers=[0.9, 0.9, 0.85, 0.85, 0.8, 0.75], coupon_annual=0.08, ki_mode="daily", ki_barrier=0.5,
    dummy_coupon=True,
)
SIM = SimSettings(n_paths=10_000, seed=3)


def test_shift_market():
    m = shift_market(MK, 0.1, 0.05, 0.005)
    assert m.corr[0][1] == pytest.approx(0.7) and m.corr[0][0] == 1.0
    assert m.vols == pytest.approx([0.37, 0.50])
    assert m.rate == pytest.approx(0.033)
    assert shift_market(MK, 0.5, 0, 0).corr[0][1] == 0.99


def test_autocall_directions():
    res = {r.label: r for r in run_sensitivity(PricingRequest(term_sheet=AC, market=MK, sim=SIM))}
    assert len(res) == 7
    assert res["변동성 +5%p"].price < res["기준"].price < res["변동성 −5%p"].price
    assert res["상관계수 +0.1"].price > res["기준"].price  # 상관이 높을수록 워스트 위험 감소
    assert res["변동성 +5%p"].fair > res["기준"].fair


def test_single_asset_skips_corr():
    ko = KnockoutTerms(underlyings=["삼성전자"], maturity_months=12, participation=1.0, ko_upper=1.37, rebate=0.03)
    mk = MarketData(vols=[0.3], divs=[0.02], corr=[[1.0]], rate=0.03)
    res = run_sensitivity(PricingRequest(term_sheet=ko, market=mk, sim=SIM))
    assert [r.label for r in res] == ["기준", "변동성 +5%p", "변동성 −5%p", "금리 +0.5%p", "금리 −0.5%p"]
    assert all(r.fair is not None for r in res)


def test_fair_sensitivity_matches_coupon_slope():
    # 쿠폰 민감도는 1 ÷ (쿠폰 1.0당 현재가치)다. 쿠폰을 바꿔 평가한 기울기로 따로 구해 비교한다
    req = PricingRequest(term_sheet=AC, market=MK, sim=SIM)
    high = req.model_copy(update={"term_sheet": AC.model_copy(update={"coupon_annual": 0.18})})
    slope = (price(high).price - price(req).price) / 0.10
    assert fair_sensitivity(req, price(req).fair_coupon_annual) == pytest.approx(0.001 / slope, rel=1e-6)


KO = KnockoutTerms(underlyings=["A"], maturity_months=12, participation=1.0, ko_upper=1.37, rebate=0.03, max_loss=0.0)
MK1 = MarketData(vols=[0.3], divs=[0.01], corr=[[1.0]], rate=0.03)


def test_fair_sensitivity_knockout_moves_price_by_bump():
    # 적정 참여율에 변화폭을 더해 다시 평가하면 공정가치가 0.1%p 올라야 한다
    req = PricingRequest(term_sheet=KO, market=MK1, sim=SIM.model_copy(update={"target": 1.02}))
    fair = price(req).fair_participation
    change = fair_sensitivity(req, fair)
    moved = req.model_copy(update={"term_sheet": KO.model_copy(update={"participation": fair + change})})
    assert price(moved).price == pytest.approx(1.021, abs=2e-4)


def test_fair_sensitivity_none_when_target_needs_no_coupon():
    req = PricingRequest(term_sheet=KO, market=MK1, sim=SIM.model_copy(update={"target": 0.9}))
    assert price(req).fair_participation == 0
    assert fair_sensitivity(req, 0.0) is None
    assert fair_sensitivity(req, None) is None
