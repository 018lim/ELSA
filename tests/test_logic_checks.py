"""ELS 로직 점검 테스트.

- 시드 42에서 7개 그룹 21개 점검이 모두 통과
- 판정 함수: 허용오차, 신뢰구간, 상승·하락·정점 판정
- 점검이 실제로 오류를 잡는지: 평가값이나 할인을 일부러 틀리게 하면 ⚠️가 나온다
- 95% 판정 테스트에 uses_ci 표시, 결과는 기본 타입
"""

import math

import pytest

from elsa.pricing import engine, logic_checks
from elsa.pricing.logic_checks import (
    LOGIC_GROUPS,
    TOLERANCE,
    check_corr_direction,
    check_first_redemption,
    check_ki_removed,
    check_vol_direction,
    check_zero_vol,
    is_decreasing,
    is_hump,
    is_increasing,
    run_logic_checks,
    within_ci,
    within_tolerance,
)


@pytest.fixture(scope="module")
def checks():
    return run_logic_checks(42)


def test_all_checks_pass_at_default_seed(checks):
    assert len(checks) == 21
    assert [c.name for c in checks if not c.passed] == []


def test_every_candidate_group_is_covered(checks):
    names = " ".join(c.name for c in checks)
    assert len(LOGIC_GROUPS) == 7
    for n in range(1, 8):
        assert f"({n}" in names


def test_ci_checks_are_marked(checks):
    assert {c.name for c in checks if c.uses_ci} == {
        "(4a) 2자산", "(4b) 3자산", "(4c) 녹아웃형", "(5c) 녹아웃 불가능",
    }


def test_results_are_plain_types_with_text(checks):
    # st.cache_data는 기본 타입만 다룬다
    for c in checks:
        assert type(c.passed) is bool
        assert c.name and c.condition and c.expected and c.actual


def test_within_tolerance_uses_larger_of_ci_and_tolerance():
    assert within_tolerance(1.0 + 0.5 * TOLERANCE, 1.0, 0.0)  # 표준오차 0이어도 반올림 오차는 허용
    assert not within_tolerance(1.0 + 2 * TOLERANCE, 1.0, 0.0)
    assert within_tolerance(1.001, 1.0, 0.001)  # 표준오차가 크면 신뢰구간 기준
    assert not within_tolerance(1.003, 1.0, 0.001)


def test_within_ci():
    assert within_ci(0.0019, 0.001)
    assert not within_ci(0.0021, 0.001)


def test_direction_helpers():
    assert is_increasing([1, 2, 3]) and not is_increasing([1, 3, 2]) and not is_increasing([1, 1])
    assert is_decreasing([3, 2, 1]) and not is_decreasing([3, 1, 2])
    assert is_hump([1, 3, 2]) and is_hump([1, 2, 3, 2, 1])
    assert not is_hump([1, 2, 3])  # 정점이 끝
    assert not is_hump([3, 2, 1])
    assert not is_hump([1, 3, 2, 4, 1])  # 중간에 다시 오름


def _shift_price(monkeypatch, amount: float) -> None:
    """평가값을 일부러 틀리게 만든다."""
    real = logic_checks.price

    def wrong(request):
        r = real(request)
        return r.model_copy(update={"price": r.price + amount})

    monkeypatch.setattr(logic_checks, "price", wrong)


def test_exact_checks_catch_small_price_error(monkeypatch):
    # 1bp(0.01%p) 오차도 허용오차 0.0001%p를 넘으므로 잡혀야 한다
    _shift_price(monkeypatch, 1e-4)
    assert not any(c.passed for c in check_zero_vol(42))
    assert not any(c.passed for c in check_first_redemption(42))


def test_exact_checks_catch_wrong_discounting(monkeypatch):
    # 기대값을 엔진과 따로 계산하므로 엔진의 할인이 틀리면 점검에서 드러난다
    monkeypatch.setattr(engine, "discount_factor", lambda rate, months: math.exp(-1.01 * rate * months / 12))
    assert not any(c.passed for c in check_first_redemption(42))


def test_direction_checks_catch_flat_price(monkeypatch):
    real = logic_checks.price

    def flat(request):
        return real(request).model_copy(update={"price": 1.0, "maturity_loss_prob": 0.1})

    monkeypatch.setattr(logic_checks, "price", flat)
    assert not check_corr_direction(42)[0].passed
    assert not check_ki_removed(42)[0].passed  # 만기 손실이 남으면 실패
    assert not any(c.passed for c in check_vol_direction(42))
