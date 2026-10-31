"""엔진 검증 테스트.

- Black-Scholes: 교과서 값, 배당 등가 관계, 내가격 극한
- MC: 기본 조건의 BS 일치, 시드 재현, 배당 반영, S0 배율, 1/√N 수렴
- 표준오차: 쌍 평균 기준 < 독립 경로 기준, 독립 경로 기준이 실제 독립 시뮬레이션과 일치
- 일괄 검증: 15개 조건, 같은 경로 공유가 개별 계산과 같은 값, 판정 불가 처리
- 화면: 검증 실행, 입력 변경 시 이전 결과 숨김
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from elsa.pricing.engine import discount_factor
from elsa.pricing.verification import (
    CONVERGENCE_NS,
    GRID_MONEYNESS,
    GRID_VOLS,
    CallParams,
    bs_call,
    mc_call,
    mc_call_strikes,
    moneyness,
    months_from_years,
    run_verification,
)

BASE = CallParams(s0=100.0, strike=100.0, vol=0.2, rate=0.03, div=0.0, months=12)
TITLES = ("① 정답 비교", "② 수렴 그래프", "③ 여러 조건 일괄 검증", "④ 대조변수 적용 효과")


def test_discount_factor():
    assert discount_factor(0.03, 12) == pytest.approx(math.exp(-0.03))
    assert discount_factor(0.03, 6) == pytest.approx(math.exp(-0.015))


def test_bs_default_value():
    assert bs_call(BASE) == pytest.approx(9.4134, abs=1e-4)


def test_bs_textbook_value():
    # Hull 교과서 예제: S=42, K=40, r=10%, σ=20%, T=0.5 → 4.76
    p = CallParams(s0=42.0, strike=40.0, vol=0.2, rate=0.1, div=0.0, months=6)
    assert bs_call(p) == pytest.approx(4.76, abs=0.005)


def test_bs_dividend_equals_lower_spot():
    # 연속 배당 q는 현재가를 S0·e^(−qT)로 낮춘 것과 같다
    p = replace(BASE, div=0.02, months=24)
    assert bs_call(p) == pytest.approx(bs_call(replace(p, s0=100.0 * math.exp(-0.04), div=0.0)))


def test_bs_deep_in_the_money_limit():
    p = replace(BASE, strike=1.0, div=0.01)
    assert bs_call(p) == pytest.approx(100.0 * math.exp(-0.01) - math.exp(-0.03))


@pytest.mark.parametrize("years,months", [(1.0, 12), (0.5, 6), (1.04, 12), (0.1, 1), (5.0, 60)])
def test_months_from_years(years, months):
    assert months_from_years(years) == months


def test_moneyness_labels_for_call():
    assert moneyness(100.0, 80.0) == "내가격"
    assert moneyness(100.0, 100.0) == "등가격"
    assert moneyness(100.0, 120.0) == "외가격"


def test_mc_matches_bs_at_defaults():
    r = mc_call(BASE, 500_000, 42)
    assert r.passed is True
    assert r.ci_low <= r.bs <= r.ci_high
    assert r.mc == pytest.approx(r.bs, rel=0.002)
    assert r.n_paths == 500_000


def test_results_use_plain_python_types():
    # st.cache_data 규칙(기본 타입만)과 `passed is True` 판정을 위해 numpy 값이 섞이면 안 된다
    r = mc_call(BASE, 10_000, 42)
    assert type(r.passed) is bool
    for name in ("mc", "bs", "diff", "rel_error", "stderr", "stderr_independent", "ci_low", "ci_high"):
        assert type(getattr(r, name)) is float, name


def test_mc_is_reproducible_with_seed():
    assert mc_call(BASE, 10_000, 7).mc == mc_call(BASE, 10_000, 7).mc
    assert mc_call(BASE, 10_000, 7).mc != mc_call(BASE, 10_000, 8).mc


def test_mc_reflects_dividend_and_rate():
    r = mc_call(replace(BASE, div=0.04, rate=0.01, vol=0.3, months=24), 200_000, 42)
    assert r.bs == pytest.approx(r.mc, abs=4 * r.stderr)


def test_mc_scales_with_spot_and_strike():
    # 엔진 경로는 시작 가격 1로 정규화되어 있으므로 S0, K를 함께 2배 하면 가격도 정확히 2배
    double = replace(BASE, s0=200.0, strike=200.0)
    assert mc_call(double, 10_000, 42).mc == pytest.approx(2 * mc_call(BASE, 10_000, 42).mc)


def test_odd_path_count_rounds_up_to_pairs():
    assert mc_call(BASE, 1_001, 42).n_paths == 1_002


def test_stderr_shrinks_with_sqrt_n():
    small, large = mc_call(BASE, 1_000, 42), mc_call(BASE, 100_000, 42)
    assert small.stderr / large.stderr == pytest.approx(10.0, rel=0.1)


def test_paired_stderr_is_smaller_than_independent():
    r = mc_call(BASE, 100_000, 42)
    assert r.stderr_independent / r.stderr == pytest.approx(1.34, abs=0.05)


def test_independent_stderr_matches_truly_independent_simulation():
    n = 100_000
    r = mc_call(BASE, n, 42)
    z = np.random.default_rng(1).standard_normal(n)
    s_T = 100.0 * np.exp(0.03 - 0.02 + 0.2 * z)
    pv = math.exp(-0.03) * np.maximum(s_T - 100.0, 0.0)
    assert r.stderr_independent == pytest.approx(pv.std(ddof=1) / math.sqrt(n), rel=0.03)


def test_strikes_share_paths_and_match_single_runs():
    strikes = [80.0, 100.0, 120.0]
    together = mc_call_strikes(BASE, strikes, 20_000, 42)
    for strike, row in zip(strikes, together):
        single = mc_call(replace(BASE, strike=strike), 20_000, 42)
        assert row.mc == single.mc
        assert row.stderr == single.stderr
        assert row.stderr_independent == single.stderr_independent


def test_no_paths_in_the_money_cannot_be_judged():
    r = mc_call(replace(BASE, strike=1000.0, vol=0.05, months=1), 10_000, 42)
    assert r.mc == 0.0
    assert r.stderr == 0.0
    assert r.passed is None


def test_run_verification_covers_all_conditions():
    report = run_verification(BASE, 20_000, 42)
    assert [c.n_paths for c in report.convergence] == list(CONVERGENCE_NS)
    assert len(report.grid) == len(GRID_MONEYNESS) * len(GRID_VOLS) == 15
    assert [r.strike for r in report.grid[:5]] == pytest.approx([80, 90, 100, 110, 120])
    assert {round(r.vol, 2) for r in report.grid} == {0.1, 0.2, 0.3}
    assert report.main.bs == pytest.approx(9.4134, abs=1e-4)
    assert sum(r.passed is True for r in report.grid) >= 13


def test_grid_strikes_follow_spot():
    report = run_verification(replace(BASE, s0=200.0, strike=200.0), 2_000, 42)
    assert [r.strike for r in report.grid[:5]] == pytest.approx([160, 180, 200, 220, 240])


def _titles(at: AppTest) -> list[str]:
    return [s.value for s in at.subheader if s.value in TITLES]


def _app() -> AppTest:
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    assert not at.exception
    return at


def test_verification_tab_shows_results():
    at = _app()
    assert [t.label for t in at.tabs] == ["ELS 평가", "엔진 검증"]
    assert not _titles(at)  # 누르기 전에는 계산하지 않음

    next(b for b in at.button if b.label == "검증 실행").click().run()
    assert not at.exception, at.exception
    assert _titles(at) == list(TITLES)
    assert any("15개 조건 중" in m.value and "0개 통과" not in m.value for m in at.markdown)
    assert any("통과 ✅" in s.value for s in at.success)
    assert any("시드 42" in c.value for c in at.caption)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["표준오차 감소 배율"].endswith("배")
    assert float(metrics["표준오차 감소 배율"][:-1]) > 1

    # 입력을 바꾸면 이전 결과를 보여 주지 않는다
    next(n for n in at.number_input if n.key == "vf_strike").set_value(110.0).run()
    assert not at.exception, at.exception
    assert not _titles(at)
    assert any("검증 실행을 다시" in c.value for c in at.caption)
