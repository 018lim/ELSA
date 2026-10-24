"""추출기 테스트.

기본(pytest): 실제 API를 부르지 않는다. 응답 파싱, 업로드 검사, 스키마 일관성, 설정에 따른 구현체 선택,
가짜 Gemini 클라이언트로 오류 변환·재시도를 검증한다.
live(pytest -m live): 실제 추출기로 샘플 이미지와 tests/fixtures/images/의 실제 캡처를 읽는다.
"""

import io
import json
from pathlib import Path

import pytest

from elsa.extraction import base, gemini
from elsa.extraction.base import (
    ExtractionFailed,
    NotConfigured,
    QuotaExceeded,
    UploadRejected,
    check_upload,
    parse_json_response,
)
from elsa.extraction.factory import get_extractor
from elsa.extraction.prompt import NOTE_FIELD_MAP, RESPONSE_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
PNG = (ROOT / "samples" / "sample_termsheet.png").read_bytes()


def test_parse_json_response():
    assert parse_json_response('{"a": 1}') == {"a": 1}
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    for bad in ["", None, "not json", "[1, 2]"]:
        with pytest.raises(ExtractionFailed):
            parse_json_response(bad)


def test_check_upload_limits():
    check_upload(PNG, "image/png")
    with pytest.raises(UploadRejected):
        check_upload(PNG, "image/gif")
    with pytest.raises(UploadRejected):
        check_upload(b"0" * (base.MAX_BYTES + 1), "image/png")
    with pytest.raises(UploadRejected):
        check_upload(b"%PDF-broken", "application/pdf")


def test_check_upload_pdf_page_limit():
    from pypdf import PdfWriter

    def pdf(pages):
        w = PdfWriter()
        for _ in range(pages):
            w.add_blank_page(width=100, height=100)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    check_upload(pdf(3), "application/pdf")
    with pytest.raises(UploadRejected, match="3페이지"):
        check_upload(pdf(4), "application/pdf")


def test_schema_is_consistent():
    props = RESPONSE_SCHEMA["properties"]
    assert set(RESPONSE_SCHEMA["required"]) == set(props)
    assert set(NOTE_FIELD_MAP) <= set(props)
    # 시장 데이터는 추출 대상이 아니다
    assert not any(k in props for k in ("vols", "volatility", "corr", "rate", "dividend"))


def test_factory_not_configured(monkeypatch):
    monkeypatch.setattr("elsa.config.get_setting", lambda name, default="": {"EXTRACTOR": "gemini"}.get(name, default))
    with pytest.raises(NotConfigured):
        get_extractor()
    monkeypatch.setattr("elsa.config.get_setting", lambda name, default="": {"EXTRACTOR": "other"}.get(name, default))
    with pytest.raises(NotConfigured):
        get_extractor()


def test_factory_picks_claude(monkeypatch):
    values = {"EXTRACTOR": "claude", "EXTRACTOR_MODEL": "m", "ANTHROPIC_API_KEY": "k"}
    monkeypatch.setattr("elsa.config.get_setting", lambda name, default="": values.get(name, default))
    assert get_extractor().name == "claude:m"


# --- 가짜 Gemini 클라이언트 -------------------------------------------------


class _FakeResponse:
    def __init__(self, text):
        self.text = text


def _install_fake_gemini(monkeypatch, outcomes):
    """outcomes: 호출마다 돌려줄 응답 문자열 또는 던질 예외."""
    from google.genai import errors

    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(model)
            out = outcomes[len(calls) - 1]
            if isinstance(out, Exception):
                raise out
            return _FakeResponse(out)

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr(gemini, "RETRY_DELAYS", (0.0, 0.0))
    return calls, errors


def _api_error(errors, code, status):
    return errors.APIError(code, {"error": {"code": code, "message": status, "status": status}})


def test_gemini_success_goes_through_normalize(monkeypatch):
    raw = json.loads((FIXTURES / "raw_knockout_samsung_up137.json").read_text(encoding="utf-8"))["raw"]
    calls, _ = _install_fake_gemini(monkeypatch, [json.dumps(raw, ensure_ascii=False)])
    res = gemini.GeminiExtractor("key", "model-x").extract(PNG, "image/png")
    assert res.product_type == "knockout" and res.fields["ko_upper"] == pytest.approx(1.37)
    assert res.extractor == "gemini:model-x"


def test_gemini_retries_503_then_succeeds(monkeypatch):
    from google.genai import errors

    outcomes = [_api_error(errors, 503, "UNAVAILABLE"), _api_error(errors, 503, "UNAVAILABLE"), '{"underlyings": []}']
    calls, _ = _install_fake_gemini(monkeypatch, outcomes)
    gemini.GeminiExtractor("key", "m").extract(PNG, "image/png")
    assert len(calls) == 3


def test_gemini_error_mapping(monkeypatch):
    _, errors = _install_fake_gemini(monkeypatch, [None])
    cases = [
        (_api_error(errors, 429, "RESOURCE_EXHAUSTED"), QuotaExceeded),
        (_api_error(errors, 400, "API_KEY_INVALID"), NotConfigured),
        (_api_error(errors, 503, "UNAVAILABLE"), ExtractionFailed),  # 재시도 후에도 실패
        (TimeoutError("timeout"), ExtractionFailed),
    ]
    for error, expected in cases:
        _install_fake_gemini(monkeypatch, [error, error, error])
        with pytest.raises(expected):
            gemini.GeminiExtractor("key", "m").extract(PNG, "image/png")
    _install_fake_gemini(monkeypatch, ["not json"])
    with pytest.raises(ExtractionFailed):
        gemini.GeminiExtractor("key", "m").extract(PNG, "image/png")


# --- 실제 API (pytest -m live) ----------------------------------------------

LIVE_CASES = [
    (ROOT / "samples" / "sample_termsheet.png", "autocall",
     {"underlyings": ["삼성전자", "SK하이닉스"], "maturity_months": 36, "obs_period_months": 6,
      "barriers": [0.9, 0.9, 0.85, 0.85, 0.8, 0.75], "coupon_annual": 0.08, "ki_mode": "daily", "ki_barrier": 0.5}),
    (ROOT / "samples" / "sample_knockout.png", "knockout",
     {"underlyings": ["KOSPI200"], "maturity_months": 12, "strike": 1.0, "participation": 0.8,
      "ko_upper": 1.3, "rebate": 0.025, "ko_observation": "daily"}),
    (FIXTURES / "images" / "autocall_3index_ki40.jpg", "autocall",
     {"underlyings": ["KOSPI200", "S&P500", "EUROSTOXX50"], "maturity_months": 36, "obs_period_months": 3,
      "ki_barrier": 0.4}),
    (FIXTURES / "images" / "autocall_monthly65_noki.jpg", "autocall",
     {"underlyings": ["삼성전자", "SK하이닉스"], "ki_mode": "none", "monthly_coupon_barrier": 0.65}),
    (FIXTURES / "images" / "knockout_samsung_up137.jpg", "knockout",
     {"underlyings": ["삼성전자"], "maturity_months": 12, "ko_upper": 1.37, "rebate": 0.03}),
    (FIXTURES / "images" / "knockout_kospi200_up136.jpg", "knockout",
     {"underlyings": ["KOSPI200"], "ko_upper": 1.36, "rebate": 0.02}),
    (FIXTURES / "images" / "autocall_elb_monthly_6p12.jpg", "autocall",
     {"underlyings": ["삼성전자", "SK하이닉스"], "coupon_annual": 0.0612, "ki_mode": "none",
      "monthly_coupon_barrier": 0.65, "max_loss": 0.0}),
    (FIXTURES / "images" / "knockout_elb_kospi200_max36.jpg", "knockout",
     {"underlyings": ["KOSPI200"], "participation": 1.0, "ko_upper": 1.36, "rebate": 0.02}),
    (FIXTURES / "images" / "autocall_super_lizard_3index_10pct.jpg", "autocall",
     {"underlyings": ["EUROSTOXX50", "KOSPI200", "S&P500"], "barriers": [0.9, 0.9, 0.85, 0.8, 0.75, 0.7],
      "coupon_annual": 0.10, "ki_mode": "daily", "ki_barrier": 0.4, "lizard_barriers": [0.75, 0.70],
      "max_loss": 1.0}),
    (FIXTURES / "images" / "autocall_els_3index_75_ki40.jpg", "autocall",
     {"underlyings": ["KOSPI200", "S&P500", "EUROSTOXX50"], "barriers": [0.75] * 5 + [0.7],
      "coupon_annual": 0.075, "ki_mode": "daily", "ki_barrier": 0.4}),
]


@pytest.mark.live
@pytest.mark.parametrize("path, product_type, expected", LIVE_CASES, ids=[c[0].name for c in LIVE_CASES])
def test_live_extraction(path, product_type, expected):
    if not path.exists():
        pytest.skip(f"{path.name} 없음 (로컬 전용 캡처)")
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    res = get_extractor().extract(path.read_bytes(), mime)
    assert res.product_type == product_type
    for key, value in expected.items():
        got = res.fields.get(key)
        assert got == (pytest.approx(value) if isinstance(value, (float, list)) and key != "underlyings" else value), key
    assert not any(k in res.fields for k in ("vols", "corr", "rate"))
