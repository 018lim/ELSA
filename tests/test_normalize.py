"""표기 정규화 테스트: CLAUDE.md §7.5의 표기마다 하나씩, 그리고 실제 캡처 4장의 원문 표기."""

import json
from pathlib import Path

import pytest

from elsa.extraction.normalize import (
    months_between_dates,
    normalize_extraction,
    normalize_name,
    parse_barriers,
    parse_coupon,
    parse_ki,
    parse_ko_barriers,
    parse_ko_observation,
    max_loss_from_product_name,
    parse_lizard_barriers,
    parse_max_loss,
    parse_monthly_barrier,
    parse_months,
    to_fraction,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "value, threshold, expected",
    [
        (85, 1.0, 0.85), (0.85, 1.0, 0.85), ("85%", 1.0, 0.85), ("연 8.0%", 1.0, 0.08),
        (8, 1.0, 0.08), (0.08, 1.0, 0.08), (137, 3.0, 1.37), (1.37, 3.0, 1.37), ("137%", 3.0, 1.37),
        ("100%", 3.0, 1.0), (None, 1.0, None), ("없음", 1.0, None),
    ],
)
def test_to_fraction(value, threshold, expected):
    assert to_fraction(value, threshold) == pytest.approx(expected) if expected is not None else to_fraction(value, threshold) is None


@pytest.mark.parametrize(
    "text, barriers, ki",
    [
        ("90-90-85-85-80-75", [0.9, 0.9, 0.85, 0.85, 0.8, 0.75], None),
        ("90-90-85-85-80-75(50)", [0.9, 0.9, 0.85, 0.85, 0.8, 0.75], 0.5),
        ("90-90-85-85-80-75 KI 50", [0.9, 0.9, 0.85, 0.85, 0.8, 0.75], 0.5),
        ("85−85−80−80 / KI 40", [0.85, 0.85, 0.8, 0.8], 0.4),
        ("90%-85%-80%", [0.9, 0.85, 0.8], None),
    ],
)
def test_parse_barriers(text, barriers, ki):
    got, got_ki = parse_barriers(text)
    assert got == pytest.approx(barriers)
    assert got_ki == (pytest.approx(ki) if ki is not None else None)


@pytest.mark.parametrize(
    "text, mode, barrier",
    [
        ("NO KI", "none", None),
        ("노낙인", "none", None),
        ("KI 50", "daily", 0.5),
        ("최초기준가격의 50% 미만으로 하락한 적이 있는 경우", "daily", 0.5),
        ("만기평가일에 50% 미만", "maturity", 0.5),
        ("매월 말일 종가 기준 최초기준가격의 50% 미만", "monthly", 0.5),
        ("KI 45 (월말 관찰)", "monthly", 0.45),
        ("매월 마지막 영업일 종가가 50% 미만인 경우", "monthly", 0.5),
        ("85-85-85-85-85-85 / KI 50 / 월지급 65", "daily", 0.5),  # '월지급'을 월말 관찰로 오해하지 않음
        ("", None, None),
    ],
)
def test_parse_ki(text, mode, barrier):
    assert parse_ki(text) == (mode, pytest.approx(barrier) if barrier is not None else None)


@pytest.mark.parametrize(
    "text, months",
    [("3년", 36), ("36개월", 36), ("1년 6개월", 18), ("3년 만기 6개월 단위", 36), ("6개월", 6), ("없음", None)],
)
def test_parse_months(text, months):
    assert parse_months(text) == months


@pytest.mark.parametrize("text", ["2029년 11월 2일", "만기일 2029.11.02", "2029/11/02"])
def test_parse_months_ignores_dates(text):
    assert parse_months(text) is None  # 2029년을 기간으로 읽으면 24,348개월이 된다


def test_maturity_date_falls_back_to_investment_period():
    raw = {"product_type": "autocall", "barriers_text": "90-85-80", "maturity_text": "2029년 11월 2일",
           "investment_period_text": "2026/11/02 ~ 2029/11/02"}
    assert normalize_extraction(raw).fields["maturity_months"] == 36


@pytest.mark.parametrize(
    "text, months",
    [
        ("2026/10/30 ~ 2029/11/02", 36),
        ("2026/10/30 ~ 2027/10/29", 12),
        ("2026.10.30 - 2029.11.01", 36),
        ("2026년 10월 30일 ~ 2029년 11월 1일", 36),
        ("2026/10/30", None),
    ],
)
def test_months_between_dates(text, months):
    assert months_between_dates(text) == months


@pytest.mark.parametrize("text", ["2026/13/30 ~ 2029/11/02", "2026/02/31 ~ 2029/11/02", "2029/11/02 ~ 2026/10/30"])
def test_months_between_dates_rejects_bad_dates(text):
    assert months_between_dates(text) is None


@pytest.mark.parametrize(
    "text, period, expected",
    [
        ("연 8.0%", 6, 0.08), ("세전 연 8.0%", 6, 0.08), ("6개월마다 4%", 6, 0.08), ("월 0.5%", 6, 0.06),
        ("8%", 6, 0.08), ("월 수익률 0.51%, 연 수익률 6.12%", 6, 0.0612), ("연 6.12% (월 0.51%)", 6, 0.0612),
        ("월 수익률 0.51%", 6, 0.0612), ("매월 0.51%", 6, 0.0612), ("연 수익률: 6.12%", 6, 0.0612),
        ("3년 만기 연 7%", 6, 0.07),
    ],
)
def test_parse_coupon(text, period, expected):
    assert parse_coupon(text, period) == pytest.approx(expected)


def test_monthly_and_knockout_texts():
    assert parse_monthly_barrier("월지급 65") == pytest.approx(0.65)
    assert parse_monthly_barrier("월지급식(65%)") == pytest.approx(0.65)
    assert parse_ko_barriers("낙아웃 137%") == (pytest.approx(1.37), None)
    assert parse_ko_barriers("낙아웃 70%") == (None, pytest.approx(0.7))
    assert parse_ko_barriers("녹아웃 130% / 70%") == (pytest.approx(1.3), pytest.approx(0.7))
    assert parse_ko_observation("만기평가일에만 관찰") == "maturity"
    assert parse_ko_observation("투자기간 중 한번이라도") == "daily"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("조건 미충족시 손실률(최대) 0%", 0.0),
        ("손실률(최대) 10%", 0.1),
        ("최대 손실률 20%", 0.2),
        ("원금 90% 보장", 0.1),
        ("원금의 95% 지급", 0.05),
        ("원금지급형", 0.0),
        ("KB able ELB 제409호", 0.0),
        ("원금비보장형", None),
        ("", None),
        ("85-85-85 / NO KI", None),
    ],
)
def test_parse_max_loss(text, expected):
    got = parse_max_loss(text)
    assert got == (pytest.approx(expected) if expected is not None else None)


@pytest.mark.parametrize(
    "text, name",
    [("KOSPI200지수", "KOSPI200"), ("코스피200", "KOSPI200"), ("S&P 500 지수", "S&P500"),
     ("Euro Stoxx 50", "EUROSTOXX50"), ("Eurostoxx50", "EUROSTOXX50"), ("테슬라", "테슬라")],
)
def test_normalize_name(text, name):
    assert normalize_name(text) == name


@pytest.mark.parametrize(
    "text, n_rounds, expected",
    [
        ("90(L75)-90(L70)-85-80-75-70 / KI 40", 6, [0.75, 0.70]),  # 슈퍼리자드: 앞 두 회차만
        ("90(L75)-90(L75)-85(L75)-85(L75)-80(L75)-75(L75)", 6, [0.75] * 6),
        ("90-90(L70)-85-80-75-70", 6, [None, 0.70]),  # 가운데 회차만
        ("90(L75)", 6, [0.75] * 6),  # 나열이 아니라 표기 하나만 있으면 전 회차에 적용
        ("리자드 75", 6, [0.75] * 6),  # 값 하나는 전 회차
        ("90-90-85-85-80-75 / KI 50", 6, None),
        ("", 6, None),
        ("KI 50 / Level", 6, None),
    ],
)
def test_parse_lizard_barriers(text, n_rounds, expected):
    got = parse_lizard_barriers(text, n_rounds)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_lizard_notation_does_not_break_barrier_parsing():
    got, ki = parse_barriers("90(L75)-90(L70)-85-80-75-70 / KI 40")
    assert got == pytest.approx([0.9, 0.9, 0.85, 0.8, 0.75, 0.7]) and ki == pytest.approx(0.4)
    res = normalize_extraction({"product_type": "autocall", "barriers_text": "90(L75)-90(L70)-85-80-75-70 / KI 40",
                                "warnings": ["리자드 구조가 포함되어 있습니다."], "unsupported_features": ["lizard"]})
    assert res.fields["lizard_barriers"] == pytest.approx([0.75, 0.70])
    assert res.fields["barriers"] == pytest.approx([0.9, 0.9, 0.85, 0.8, 0.75, 0.7])
    assert res.warnings == [] and res.unsupported_features == []  # 이제 지원하는 구조


@pytest.mark.parametrize(
    "name, expected",
    [("KB able ELB 제409호", 0.0), ("DLB 제12호", 0.0), ("원금지급형 월지급식", 0.0), ("KB able ELS 제4600호", None),
     ("슈퍼리자드스텝다운", None), ("", None), (None, None)],
)
def test_max_loss_from_product_name(name, expected):
    assert max_loss_from_product_name(name) == expected


def test_elb_name_fills_max_loss_only_when_no_loss_figure():
    base = {"product_type": "autocall", "barriers_text": "85-85-85-85-85-85 / NO KI",
            "product_name_text": "KB able ELB 제409호"}
    assert normalize_extraction(base).fields["max_loss"] == 0.0
    # 손실률 표기가 있으면 그것이 우선한다 (상품명이 ELB여도 표기가 -100%면 제한 없음)
    explicit = normalize_extraction({**base, "max_loss_text": "조건 미충족시 손실률(최대) -100%"})
    assert explicit.fields["max_loss"] == 1.0
    # ELS 이름이고 손실률 표기가 없으면 비워 둔다 (찾지 못한 항목으로 안내)
    els = normalize_extraction({**base, "product_name_text": "KB able ELS 제4600호"})
    assert "max_loss" not in els.fields


def test_knockout_reads_max_loss_without_assuming_protection():
    base = {"product_type": "knockout", "ko_barriers_text": "낙아웃 136%", "participation_text": "참여율 100%"}
    full = normalize_extraction({**base, "max_loss_text": "조건 미충족시 손실률(최대) 0%"})
    assert full.fields["max_loss"] == 0.0 and full.warnings == []
    partial = normalize_extraction({**base, "max_loss_text": "조건 미충족시 손실률(최대) 10%"})
    assert partial.fields["max_loss"] == pytest.approx(0.1) and partial.warnings == []
    missing = normalize_extraction(base)
    assert "max_loss" not in missing.fields  # 표기가 없으면 원금 보장을 가정하지 않고 비워 둔다


def test_supported_structures_are_not_reported_as_unsupported():
    raw = {
        "product_type": "autocall",
        "barriers_text": "85-85-85-85-85-85 / NO KI / 월지급 65",
        "warnings": [
            "상품 유형에 월지급식, Ultra, Hi-Five 같은 특수 구조 명칭이 포함되어 있습니다.",
            "쿠폰 지급 규칙을 알 수 없는 특수 구조입니다.",
        ],
        "unsupported_features": ["monthly_coupon", "ultra_hi_five", "Hi-Five", "lizard", "range_accrual"],
    }
    res = normalize_extraction(raw)
    assert res.unsupported_features == ["range_accrual"]  # 월지급·Hi-Five·리자드는 지원하는 구조라 걸러진다
    assert res.warnings == ["쿠폰 지급 규칙을 알 수 없는 특수 구조입니다."]


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture",
    sorted(p.name for p in FIXTURES.glob("raw_*.json")),
)
def test_fixture_matches_expected(fixture):
    case = _load(fixture)
    result = normalize_extraction(case["raw"], extractor="test")
    expected = case["expected"]
    assert result.product_type == expected["product_type"]
    for key, value in expected["fields"].items():
        got = result.fields.get(key)
        if isinstance(value, float):
            assert got == pytest.approx(value), key
        elif isinstance(value, list) and value and isinstance(value[0], float):
            assert got == pytest.approx(value), key
        else:
            assert got == value, key
    for key in expected.get("absent", []):
        assert key not in result.fields, key
    for key in expected.get("low_confidence", []):
        assert result.field_confidence.get(key) == "low", key
    assert len(result.warnings) == expected.get("n_warnings", len(result.warnings))
