"""민감도 분석 테스트: 시나리오 이동이 맞게 적용되고, 방향성이 금융적으로 타당한지 확인."""

import pytest

from elsa.pricing.sensitivity import run_sensitivity, shift_market
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
