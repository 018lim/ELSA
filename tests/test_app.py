"""화면 통합 테스트 (streamlit.testing.AppTest, 실제 API 호출 없음).

- 첫 화면이 예시 조건으로 열리고, 평가하기를 누르면 결과가 나온다
- 쿠폰을 비우면 적정 쿠폰만 계산된다
- 녹아웃형으로 바꿔 평가할 수 있다
- 자동 입력(pending_fill)이 위젯 값에 반영된다
- 잘못된 입력은 한국어 오류로 안내된다
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from elsa.extraction.normalize import normalize_extraction

FIXTURES = Path(__file__).parent / "fixtures"


def _app() -> AppTest:
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    assert not at.exception
    at.session_state["sim_paths"] = 10_000
    return at


def _click(at: AppTest, label: str) -> AppTest:
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, at.exception
    return at


def _metric(at: AppTest, label_start: str):
    return next(m for m in at.metric if m.label.startswith(label_start))


def test_example_evaluates():
    at = _click(_app(), "평가하기")
    price = _metric(at, "공정가치").value
    assert price.endswith("%") and 80 < float(price[:-1]) < 92
    assert _metric(at, "적정 연 쿠폰").value.endswith("%")


def test_blank_coupon_gives_fair_coupon_only():
    at = _app()
    at.session_state["ac_coupon"] = None
    at = _click(at, "평가하기")
    assert _metric(at, "공정가치").value == "—"
    assert _metric(at, "적정 연 쿠폰").value.endswith("%")


def test_knockout_product():
    at = _app()
    at.session_state["product_type"] = "knockout"
    at.session_state["n_underlyings"] = 1
    at = _click(at, "평가하기")
    assert _metric(at, "녹아웃 확률").value.endswith("%")
    assert float(_metric(at, "공정가치").value[:-1]) > 95  # 원금 보장


def _set_widget(at: AppTest, key: str, value) -> AppTest:
    """사용자가 위젯을 조작한 것처럼 값을 바꾸고 다시 실행한다."""
    for group in (at.selectbox, at.checkbox, at.radio, at.number_input):
        for w in group:
            if w.key == key:
                w.set_value(value).run()
                assert not at.exception, at.exception
                return at
    raise KeyError(key)


def test_hidden_widgets_keep_values():
    # 노낙인 → 더미 쿠폰 체크박스가 사라졌다가 다시 나타나도 평가가 되어야 한다 (KeyError 회귀 테스트)
    at = _app()
    _set_widget(at, "ac_ki_mode", "none")
    _set_widget(at, "ac_ki_mode", "daily")
    _set_widget(at, "ac_monthly", True)
    at = _click(at, "평가하기")
    assert _metric(at, "공정가치").value.endswith("%")

    # 녹아웃형으로 갔다가 돌아와도 자동조기상환형 입력이 남아 있어야 한다
    _set_widget(at, "product_type", "knockout")
    _set_widget(at, "ko_upper_on", False)
    _set_widget(at, "ko_lower_on", True)
    _set_widget(at, "product_type", "autocall")
    assert at.session_state["ac_barriers"] == "90-90-85-85-80-75"
    assert at.session_state["ac_coupon"] == 8.0
    at = _click(at, "평가하기")
    assert _metric(at, "공정가치").value.endswith("%")

    # 기초자산 개수를 줄였다 늘려도 값이 유지된다
    _set_widget(at, "n_underlyings", 1)
    _set_widget(at, "n_underlyings", 2)
    assert at.session_state["ul_name_1"] == "SK하이닉스"
    assert at.session_state["mk_vol_1"] == 45.0


def test_fetch_market_fills_vol_corr_and_stock_dividend(monkeypatch):
    from elsa import market

    calls = []

    def fake_estimate(tickers, months, div_tickers=None):
        calls.append((tuple(tickers), months))
        return market.MarketEstimate(
            vols=[0.77, 0.92], corr=[[1, 0.71], [0.71, 1]], divs=[0.0061, None],
            div_sources=[tickers[0], tickers[1]], n_days=120, start="2026-04-24", end="2026-10-23",
        )

    monkeypatch.setattr(market, "estimate_from_tickers", fake_estimate)
    at = _app()
    _set_widget(at, "est_months", 6)
    at = _click(at, "📈 시세로 추정")
    ss = at.session_state
    assert calls == [(("005930.KS", "000660.KS"), 6)]
    assert ss["mk_vol_0"] == 77.0 and ss["mk_vol_1"] == 92.0
    assert ss["mk_corr_0_1"] == pytest.approx(0.71)
    assert ss["mk_div_0"] == pytest.approx(0.61)  # 실제 배당으로 갱신
    assert ss["mk_div_1"] == pytest.approx(0.2)  # 배당 정보 없음 → 기존 값 유지
    labels = [n.label for n in at.number_input]
    assert "변동성 (%) (과거 6개월 기준)" in labels
    assert "배당수익률 (%) (최근 1년 배당 기준)" in labels
    assert any("6개월" not in s.value and "공통 거래일 120일" in s.value for s in at.success)


def test_buttons_hidden_while_extracting(monkeypatch):
    import streamlit

    from ui import upload

    raw = json.loads((FIXTURES / "raw_knockout_samsung_up137.json").read_text(encoding="utf-8"))["raw"]

    class FakeExtractor:
        name = "fake"

        def extract(self, data, mime):
            return normalize_extraction(raw, "fake")

    monkeypatch.setattr(upload, "get_extractor", lambda: FakeExtractor())
    upload._extract_sample.clear()

    at = _app()
    labels = [b.label for b in at.button]
    assert "샘플 이미지로 체험" in labels and "조건 읽기" in labels

    # 작업이 실행되는 화면을 붙잡기 위해 작업 후 rerun을 잠시 막는다
    monkeypatch.setattr(streamlit, "rerun", lambda *a, **k: None)
    _click(at, "샘플 이미지로 체험")
    labels = [b.label for b in at.button]
    assert "샘플 이미지로 체험" not in labels and "조건 읽기" not in labels
    assert all(s.key != "sample_choice" for s in at.selectbox)
    assert any("조건을 읽는 중" in i.value for i in at.info)

    # 다음 실행에서 결과가 폼에 반영되고 버튼이 돌아온다
    monkeypatch.undo()
    at.run()
    assert not at.exception
    assert at.session_state["product_type"] == "knockout"
    assert at.session_state["ko_upper"] == pytest.approx(137)
    assert "샘플 이미지로 체험" in [b.label for b in at.button]


def test_rate_button_fills_ktb3y(monkeypatch):
    from elsa import rates
    from ui import form

    form.fetch_rate.clear()
    monkeypatch.setattr(rates, "fetch_ktb3y", lambda key: rates.RateQuote(0.03803, "2026-10-23", "국고채(3년)"))
    at = _click(_app(), "🏦 국고채 3년 불러오기")
    assert at.session_state["mk_rate"] == pytest.approx(3.803)
    assert "무위험이자율 (%) (국고채(3년) 2026-10-23)" in [n.label for n in at.number_input]
    form.fetch_rate.clear()


def test_rate_cache_survives_module_reload(monkeypatch):
    """앱 실행 중 코드가 다시 불러와지면 RateQuote 클래스가 바뀐다. 캐시가 객체를 그대로 저장하면
    'not the same object' 피클 오류가 났다(회귀 테스트). 다시 불러온 뒤의 '옛 클래스' 객체를 흉내 낸다."""
    from dataclasses import dataclass

    from elsa import rates
    from ui import form

    @dataclass(frozen=True)
    class RateQuote:  # 이름·모듈은 같지만 elsa.rates.RateQuote와 다른 클래스
        rate: float
        as_of: str
        name: str

    RateQuote.__module__ = "elsa.rates"
    form.fetch_rate.clear()
    monkeypatch.setattr(rates, "fetch_ktb3y", lambda key: RateQuote(0.0381, "2026-10-23", "국고채(3년)"))
    at = _click(_app(), "🏦 국고채 3년 불러오기")
    assert at.session_state["mk_rate"] == pytest.approx(3.81)
    form.fetch_rate.clear()


def test_rate_button_without_key_keeps_value(monkeypatch):
    from ui import form

    form.fetch_rate.clear()
    monkeypatch.setattr("elsa.config.get_setting", lambda name, default="": "")
    at = _click(_app(), "🏦 국고채 3년 불러오기")
    assert at.session_state["mk_rate"] == 2.8
    assert any("ECOS_API_KEY" in w.value for w in at.warning)
    form.fetch_rate.clear()


@pytest.mark.parametrize(
    "fixture, expected_ko_max_loss",
    [("raw_knockout_max_return_36.json", 0.0), ("raw_knockout_samsung_up137.json", None)],
)
def test_knockout_autofill_max_loss_without_assumption(fixture, expected_ko_max_loss):
    raw = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))["raw"]
    at = _app()
    at.session_state["pending_fill"] = normalize_extraction(raw, "test").model_dump()
    at.run()
    assert not at.exception
    assert at.session_state["ko_max_loss"] == expected_ko_max_loss
    assert at.session_state["ac_max_loss"] is None  # 자동조기상환형 칸은 건드리지 않는다
    missing_notice = any("찾지 못한 항목" in w.value and "최대 손실률" in w.value for w in at.warning)
    assert missing_notice == (expected_ko_max_loss is None)


def test_lizard_flow_and_conflict_with_monthly():
    at = _app()
    _set_widget(at, "ac_lizard", True)
    assert at.session_state["ac_lizard_barrier"] == "75-70"
    at = _click(at, "평가하기")
    assert _metric(at, "공정가치").value.endswith("%")
    assert any("쿠폰 평균 수령 횟수" in c.value for c in at.caption)

    _set_widget(at, "ac_monthly", True)  # 리자드와 월지급식은 함께 쓸 수 없다
    at = _click(at, "평가하기")
    assert any("함께 쓸 수 없습니다" in e.value for e in at.error)


def test_lizard_typed_in_barrier_field():
    at = _app()
    at.session_state["ac_maturity"] = 12
    at.session_state["ac_period"] = 6
    at.session_state["ac_barriers"] = "90(L75)-80"
    at = _click(at, "평가하기")
    # 배리어 2개 = 평가 2회. 리자드 체크박스를 켜지 않아도 괄호 표기만으로 리자드가 인식되고 입력 오류도 없다
    assert not any("입력값을 확인해 주세요" in e.value for e in at.error)
    assert _metric(at, "공정가치").value.endswith("%")
    assert any("쿠폰 평균 수령 횟수" in c.value for c in at.caption)


def test_validation_error_message():
    at = _app()
    at.session_state["ac_barriers"] = "90-90-85"
    at = _click(at, "평가하기")
    assert any("상환 배리어를 6개" in e.value for e in at.error)


def test_pending_fill_updates_form():
    raw = json.loads((FIXTURES / "raw_autocall_3index_ki40.json").read_text(encoding="utf-8"))["raw"]
    at = _app()
    at.session_state["pending_fill"] = normalize_extraction(raw, "test").model_dump()
    at.run()
    assert not at.exception
    ss = at.session_state
    assert ss["n_underlyings"] == 3
    assert [ss[f"ul_name_{i}"] for i in range(3)] == ["KOSPI200", "S&P500", "EUROSTOXX50"]
    assert ss["ul_ticker_2"] == "^STOXX50E"
    assert ss["ac_period"] == 3 and ss["ac_maturity"] == 36
    assert ss["ac_barriers"] == "85-85-85-85-80-80-80-80-75-75-70-70"
    assert ss["ac_coupon"] is None and ss["ac_ki_barrier"] == pytest.approx(40)
    assert any("이미지에서" in s.value for s in at.success)
    assert any("쿠폰이 없습니다" in i.value for i in at.info)
    labels = [t.label for t in at.text_input]
    assert "기초자산 1 (이미지)" in labels
