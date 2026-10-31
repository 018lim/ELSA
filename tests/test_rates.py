"""무위험이자율 테스트: ECOS·네이버 응답 파싱, 오류 변환, 대체 조회 순서(네트워크 없음), (live) 실제 조회."""

import http.client
import io
import urllib.error
import urllib.request

import pytest

from elsa import config, rates
from elsa.rates import (
    SOURCE_NAVER,
    TIMEOUT_S,
    RateQuote,
    RateUnavailable,
    fetch_ktb3y,
    fetch_ktb3y_naver,
    fetch_latest_rate,
    parse_ecos_response,
    parse_naver_response,
)

NAVER_OK = {"reutersCode": "KR3YT=RR", "closePrice": "3.7130", "localTradedAt": "2026-10-30T16:05:24+09:00"}


def _row(time, value):
    return {"STAT_CODE": "817Y002", "ITEM_NAME1": "국고채(3년)", "UNIT_NAME": "연%", "TIME": time, "DATA_VALUE": value}


def test_parse_takes_latest_row():
    data = {"StatisticSearch": {"row": [_row("20261021", "3.95"), _row("20261023", "3.981"), _row("20261022", "3.97")]}}
    q = parse_ecos_response(data)
    assert q.rate == pytest.approx(0.03981)
    assert q.as_of == "2026-10-23" and q.name == "국고채(3년)"
    assert q.source == "한국은행 ECOS"


@pytest.mark.parametrize(
    "data, message",
    [
        ({"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}, "인증키"),
        ({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}, "데이터가 없습니다"),
        ({"StatisticSearch": {"row": [_row("20261023", "")]}}, "데이터가 없습니다"),
        ({"RESULT": {"CODE": "ERROR-500", "MESSAGE": "서버 오류"}}, "서버 오류"),
    ],
)
def test_parse_errors(data, message):
    with pytest.raises(RateUnavailable, match=message):
        parse_ecos_response(data)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"RESULT": "오류"},
        {"StatisticSearch": {"row": [{"DATA_VALUE": "3.7"}]}},  # 날짜 없음
        {"StatisticSearch": {"row": [_row("20261023", "숫자 아님")]}},
    ],
)
def test_parse_ecos_unexpected_format_becomes_common_error(data):
    # 공통 오류여야 네이버 조회로 넘어갈 수 있다
    with pytest.raises(RateUnavailable, match="금리를 직접 입력해 주세요"):
        parse_ecos_response(data)


def test_missing_key():
    with pytest.raises(RateUnavailable, match="ECOS_API_KEY"):
        fetch_ktb3y("")


def test_parse_naver_response():
    q = parse_naver_response(NAVER_OK)
    assert q.rate == pytest.approx(0.03713)
    assert q.as_of == "2026-10-30"
    assert q.name == "국고채(3년)" and q.source == SOURCE_NAVER


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {**NAVER_OK, "closePrice": ""},
        {**NAVER_OK, "closePrice": None},
        {**NAVER_OK, "closePrice": "0"},
        {**NAVER_OK, "closePrice": "-1"},
        {**NAVER_OK, "closePrice": "62525.0"},  # 금리가 아닌 값이 들어온 경우
        {**NAVER_OK, "reutersCode": "KR10YT=RR"},  # 다른 만기
        {**NAVER_OK, "localTradedAt": ""},
    ],
)
def test_parse_naver_rejects_unexpected_response(data):
    with pytest.raises(RateUnavailable, match="금리를 직접 입력해 주세요"):
        parse_naver_response(data)


def _fail_with(monkeypatch, failure):
    seen = {}

    def fake_urlopen(url, timeout):
        seen["timeout"] = timeout
        if isinstance(failure, Exception):
            raise failure
        return failure()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


NETWORK_FAILURES = [
    urllib.error.URLError(TimeoutError("timed out")),  # Streamlit Cloud에서 실제로 나는 오류
    TimeoutError("timed out"),
    http.client.RemoteDisconnected("closed"),  # 응답 중 연결이 끊기면 URLError로 감싸이지 않는다
    lambda: io.BytesIO(b"<html>not json</html>"),
    lambda: io.BytesIO(b"\xff\xfe"),
]


@pytest.mark.parametrize("failure", NETWORK_FAILURES)
@pytest.mark.parametrize("fetch", [lambda: fetch_ktb3y("key"), fetch_ktb3y_naver], ids=["ecos", "naver"])
def test_network_failure_tells_user_what_to_do(monkeypatch, failure, fetch):
    seen = _fail_with(monkeypatch, failure)
    with pytest.raises(RateUnavailable, match="금리를 직접 입력해 주세요") as e:
        fetch()
    assert "Error" not in str(e.value)  # 내부 오류 이름을 화면에 보여 주지 않는다
    assert seen["timeout"] == TIMEOUT_S


def _stub(monkeypatch, ecos=None, naver=None):
    """ecos·naver: RateQuote면 그 값을 돌려주고, None이면 실패한다. 호출 순서를 기록한다."""
    calls = []

    def make(name, quote):
        def fetch(*args):
            calls.append(name)
            if quote is None:
                raise RateUnavailable(f"{name} 실패")
            return quote
        return fetch

    monkeypatch.setattr(rates, "fetch_ktb3y", make("ecos", ecos))
    monkeypatch.setattr(rates, "fetch_ktb3y_naver", make("naver", naver))
    return calls


ECOS_QUOTE = RateQuote(0.03715, "2026-10-30", "국고채(3년)")
NAVER_QUOTE = RateQuote(0.03713, "2026-10-30", "국고채(3년)", SOURCE_NAVER)


def test_latest_rate_prefers_ecos(monkeypatch):
    calls = _stub(monkeypatch, ecos=ECOS_QUOTE, naver=NAVER_QUOTE)
    assert fetch_latest_rate("key") == ECOS_QUOTE
    assert calls == ["ecos"]  # ECOS가 되면 네이버는 부르지 않는다


def test_latest_rate_falls_back_to_naver(monkeypatch):
    calls = _stub(monkeypatch, naver=NAVER_QUOTE)
    assert fetch_latest_rate("key") == NAVER_QUOTE
    assert calls == ["ecos", "naver"]


def test_latest_rate_reports_ecos_error_when_both_fail(monkeypatch):
    _stub(monkeypatch)
    with pytest.raises(RateUnavailable, match="ecos 실패"):
        fetch_latest_rate("key")


@pytest.mark.live
def test_live_ecos():
    # 설정된 키가 없으면 ECOS 공개 테스트 키(sample)로 조회
    q = fetch_ktb3y(config.get_setting("ECOS_API_KEY") or "sample")
    assert 0.0 < q.rate < 0.15


@pytest.mark.live
def test_live_naver_matches_ecos():
    naver, ecos = fetch_ktb3y_naver(), fetch_ktb3y(config.get_setting("ECOS_API_KEY") or "sample")
    assert naver.rate == pytest.approx(ecos.rate, abs=0.003)  # 기준일이 달라도 0.3%p 안쪽이어야 정상
