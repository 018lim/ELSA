"""무위험이자율 조회: 한국은행 ECOS API의 국고채(3년) 일별 금리 (streamlit 의존 없음).

국내 ELS는 원화로 정산되므로 원화 금리를 쓴다. 인증키(ECOS_API_KEY)는 Gemini 키처럼
.streamlit/secrets.toml(로컬) 또는 배포 환경의 Secrets에서만 읽고 코드에 넣지 않는다.
표준 라이브러리(urllib)만 쓴다.

- RateQuote: 금리(소수), 기준일, 항목 이름
- RateUnavailable: 키 없음·잘못된 키·데이터 없음·네트워크 오류
- parse_ecos_response(data): ECOS 응답 JSON → 가장 최근 RateQuote
- fetch_ktb3y(api_key, today): 최근 열흘 국고채 3년 금리를 조회해 가장 최근 값
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10/{stat}/D/{start}/{end}/{item}"
STAT_MARKET_RATE_DAILY = "817Y002"  # 1.3.2.1. 시장금리(일별)
ITEM_KTB_3Y = "010200000"  # 국고채(3년)
# 응답은 오래된 날짜부터 오고 한 번에 10건까지만 받으므로(공개 테스트 키 제한), 영업일이 10일을
# 넘지 않는 10일 범위로 조회해 가장 최근 값이 잘리지 않게 한다
LOOKBACK_DAYS = 10
TIMEOUT_S = 15


class RateUnavailable(Exception):
    """금리를 가져오지 못함 (사용자에게 보여 줄 한국어 메시지를 담는다)."""


@dataclass(frozen=True)
class RateQuote:
    rate: float  # 소수 (0.0395 = 3.95%)
    as_of: str  # YYYY-MM-DD
    name: str


def parse_ecos_response(data: dict) -> RateQuote:
    result = data.get("RESULT")
    if result:  # 오류 응답: {"RESULT": {"CODE": "INFO-100", "MESSAGE": "..."}}
        code = result.get("CODE", "")
        if code == "INFO-100":
            raise RateUnavailable("ECOS 인증키가 유효하지 않습니다. ECOS_API_KEY를 확인해 주세요.")
        if code == "INFO-200":
            raise RateUnavailable("최근 열흘간 국고채 금리 데이터가 없습니다. 금리를 직접 입력해 주세요.")
        raise RateUnavailable(f"ECOS 조회 오류: {result.get('MESSAGE', code)}")
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    rows = [r for r in rows if r.get("DATA_VALUE") not in (None, "", "-")]
    if not rows:
        raise RateUnavailable("최근 열흘간 국고채 금리 데이터가 없습니다. 금리를 직접 입력해 주세요.")
    latest = max(rows, key=lambda r: r["TIME"])
    t = latest["TIME"]
    return RateQuote(
        rate=float(latest["DATA_VALUE"]) / 100,
        as_of=f"{t[:4]}-{t[4:6]}-{t[6:8]}",
        name=latest.get("ITEM_NAME1") or "국고채(3년)",
    )


def fetch_ktb3y(api_key: str, today: date | None = None) -> RateQuote:
    if not api_key:
        raise RateUnavailable("ECOS_API_KEY가 설정되지 않았습니다. 금리를 직접 입력해 주세요.")
    today = today or date.today()
    url = ECOS_URL.format(
        key=urllib.parse.quote(api_key, safe=""),
        stat=STAT_MARKET_RATE_DAILY,
        start=(today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
        end=today.strftime("%Y%m%d"),
        item=ITEM_KTB_3Y,
    )
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise RateUnavailable(f"ECOS 접속에 실패했습니다 ({type(e).__name__}).") from e
    return parse_ecos_response(data)
