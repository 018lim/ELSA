"""무위험이자율(ECOS 국고채 3년) 테스트: 응답 파싱·오류 변환(네트워크 없음), (live) 실제 조회."""

import pytest

from elsa import config
from elsa.rates import RateUnavailable, fetch_ktb3y, parse_ecos_response


def _row(time, value):
    return {"STAT_CODE": "817Y002", "ITEM_NAME1": "국고채(3년)", "UNIT_NAME": "연%", "TIME": time, "DATA_VALUE": value}


def test_parse_takes_latest_row():
    data = {"StatisticSearch": {"row": [_row("20261021", "3.95"), _row("20261023", "3.981"), _row("20261022", "3.97")]}}
    q = parse_ecos_response(data)
    assert q.rate == pytest.approx(0.03981)
    assert q.as_of == "2026-10-23" and q.name == "국고채(3년)"


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


def test_missing_key():
    with pytest.raises(RateUnavailable, match="ECOS_API_KEY"):
        fetch_ktb3y("")


@pytest.mark.live
def test_live_ecos():
    # 설정된 키가 없으면 ECOS 공개 테스트 키(sample)로 조회
    q = fetch_ktb3y(config.get_setting("ECOS_API_KEY") or "sample")
    assert 0.0 < q.rate < 0.15
