"""무위험이자율 조회: 국고채(3년) 금리 (streamlit 의존 없음).

국내 ELS는 원화로 정산되므로 원화 금리를 쓴다. 공식 출처인 한국은행 ECOS를 먼저 조회하고,
실패하면 네이버 금융의 값을 쓴다. Streamlit Cloud에서 ECOS 접속이 제한 시간 초과로 계속 실패한 적이
있기 때문이다. 네이버 주소는 공개 문서가 없는 비공식 주소라 응답을 검사해, 형식이 바뀌면 값을 넣지 않고
금리 직접 입력을 안내한다.

인증키(ECOS_API_KEY)는 Gemini 키처럼 .streamlit/secrets.toml(로컬) 또는 배포 환경의 Secrets에서만
읽고 코드에 넣지 않는다. 표준 라이브러리(urllib)만 쓴다.

- RateQuote: 금리(소수), 기준일, 항목 이름, 출처
- RateUnavailable: 키 없음·잘못된 키·데이터 없음·네트워크 오류·응답 형식 변경
- parse_ecos_response(data): ECOS 응답 JSON → 가장 최근 RateQuote
- parse_naver_response(data): 네이버 응답 JSON → RateQuote
- fetch_ktb3y(api_key, today): ECOS에서 최근 열흘 국고채 3년 금리를 조회해 가장 최근 값
- fetch_ktb3y_naver(): 네이버 금융의 국고채 3년 금리
- fetch_latest_rate(api_key): ECOS → 네이버 순으로 조회. 둘 다 실패하면 ECOS의 오류 안내
"""

from __future__ import annotations

import http.client
import json
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
NAVER_URL = "https://api.stock.naver.com/marketindex/bond/KR3YT=RR"
NAVER_CODE = "KR3YT=RR"  # 한국 국채 3년 (레피니티브 코드)
MAX_NAVER_RATE_PCT = 20.0  # 형식이 바뀌어 다른 값이 들어오는 것을 막는 상한
SOURCE_ECOS = "한국은행 ECOS"
SOURCE_NAVER = "네이버 금융(레피니티브)"
KTB_3Y_NAME = "국고채(3년)"
ENTER_MANUALLY = "금리를 직접 입력해 주세요."
TIMEOUT_S = 5  # 정상이면 0.1초 안팎이고, 접속이 막혔을 때는 기다려도 응답이 없다


class RateUnavailable(Exception):
    """금리를 가져오지 못함 (사용자에게 보여 줄 한국어 메시지를 담는다)."""


@dataclass(frozen=True)
class RateQuote:
    rate: float  # 소수 (0.0395 = 3.95%)
    as_of: str  # YYYY-MM-DD
    name: str
    source: str = SOURCE_ECOS


def parse_ecos_response(data: dict) -> RateQuote:
    try:
        result = data.get("RESULT")
        if result:  # 오류 응답: {"RESULT": {"CODE": "INFO-100", "MESSAGE": "..."}}
            code = result.get("CODE", "")
            if code == "INFO-100":
                raise RateUnavailable("ECOS 인증키가 유효하지 않습니다. ECOS_API_KEY를 확인해 주세요.")
            if code == "INFO-200":
                raise RateUnavailable(f"최근 열흘간 국고채 금리 데이터가 없습니다. {ENTER_MANUALLY}")
            raise RateUnavailable(f"ECOS 조회 오류: {result.get('MESSAGE', code)}")
        rows = (data.get("StatisticSearch") or {}).get("row") or []
        rows = [r for r in rows if r.get("DATA_VALUE") not in (None, "", "-")]
        if not rows:
            raise RateUnavailable(f"최근 열흘간 국고채 금리 데이터가 없습니다. {ENTER_MANUALLY}")
        latest = max(rows, key=lambda r: r["TIME"])
        t = latest["TIME"]
        return RateQuote(
            rate=float(latest["DATA_VALUE"]) / 100,
            as_of=f"{t[:4]}-{t[4:6]}-{t[6:8]}",
            name=latest.get("ITEM_NAME1") or KTB_3Y_NAME,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as e:  # 대체 조회로 넘어갈 수 있게 공통 오류로
        raise RateUnavailable(f"ECOS 응답 형식이 달라졌습니다. {ENTER_MANUALLY}") from e


def parse_naver_response(data: dict) -> RateQuote:
    invalid = RateUnavailable(f"네이버 금융의 금리 응답 형식이 달라졌습니다. {ENTER_MANUALLY}")
    try:
        value = float(data["closePrice"])
        as_of = str(data["localTradedAt"])[:10]
        code = data["reutersCode"]
    except (KeyError, TypeError, ValueError) as e:
        raise invalid from e
    if code != NAVER_CODE or not 0 < value < MAX_NAVER_RATE_PCT or len(as_of) != 10:
        raise invalid
    return RateQuote(rate=value / 100, as_of=as_of, name=KTB_3Y_NAME, source=SOURCE_NAVER)


def _get_json(url: str, unavailable: str) -> dict:
    """응답을 JSON으로 읽는다. 연결·읽기·해석 실패는 모두 unavailable 문구로 알린다.

    연결이 응답 중간에 끊기면 URLError가 아닌 오류(RemoteDisconnected)가 그대로 올라오므로
    OSError와 HTTPException을 함께 잡는다. JSON·인코딩 오류는 ValueError다.
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise RateUnavailable(unavailable) from e


def fetch_ktb3y(api_key: str, today: date | None = None) -> RateQuote:
    if not api_key:
        raise RateUnavailable(f"ECOS_API_KEY가 설정되지 않았습니다. {ENTER_MANUALLY}")
    today = today or date.today()
    url = ECOS_URL.format(
        key=urllib.parse.quote(api_key, safe=""),
        stat=STAT_MARKET_RATE_DAILY,
        start=(today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
        end=today.strftime("%Y%m%d"),
        item=ITEM_KTB_3Y,
    )
    return parse_ecos_response(_get_json(url, (
        f"한국은행 ECOS에서 응답을 받지 못했습니다. 해외 서버에서 실행 중이면 접속이 막혀 있을 수 있습니다. {ENTER_MANUALLY}"
    )))


def fetch_ktb3y_naver() -> RateQuote:
    return parse_naver_response(_get_json(NAVER_URL, f"네이버 금융에서 금리를 받지 못했습니다. {ENTER_MANUALLY}"))


def fetch_latest_rate(api_key: str) -> RateQuote:
    try:
        return fetch_ktb3y(api_key)
    except RateUnavailable as ecos_error:
        try:
            return fetch_ktb3y_naver()
        except RateUnavailable:
            raise ecos_error from None
