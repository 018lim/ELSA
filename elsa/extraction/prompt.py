"""추출기 공통 프롬프트와 응답 JSON 스키마 (Gemini·Claude가 똑같이 사용).

추출기는 값을 해석하지 않고 이미지의 원문 표기를 필드별 문자열로 옮겨 적는다.
해석(단위 변환, 날짜 → 개월, 배리어 나열 분해 등)은 normalize.py가 한다.
이렇게 하면 모델이 계산을 틀릴 여지가 줄고, 근거 원문이 자연스럽게 남는다.

- SYSTEM_PROMPT: 역할·원칙 (이미지에 없는 값은 null, 시장 데이터 추출 금지 등)
- USER_PROMPT: 이미지와 함께 보내는 요청 문장
- RESPONSE_SCHEMA: 표준 JSON Schema (Gemini response_json_schema, Claude output_config.format 공용)
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
당신은 한국 ELS(주가연계증권) 상품 안내 자료에서 상품 조건을 옮겨 적는 추출기입니다.
입력은 간이투자설명서, 상품 요약표, 증권사 앱 화면 캡처 등의 이미지 또는 PDF입니다.

원칙
1. 이미지에 실제로 적힌 내용만 옮깁니다. 이미지에 없는 값은 반드시 null로 둡니다. 일반적인 값이나 추정으로 채우지 마세요.
2. 값을 계산하거나 단위를 바꾸지 말고 원문 표기를 그대로 문자열로 적습니다. 예: "85-85-80-80-75-70 / KI 40", "연 8.0%", "2026/10/30 ~ 2029/11/02", "낙아웃 137%".
3. 변동성, 배당수익률, 상관계수, 금리 같은 시장 데이터는 추출하지 않습니다(이미지에 있어도 무시).
4. 상품 유형을 판별합니다.
   - autocall: 평가일마다 기초자산이 배리어 이상이면 조기상환되는 구조(스텝다운, 월지급식 포함)
   - knockout: 행사가·참여율·낙아웃(녹아웃) 배리어·낙아웃 시 수익률로 수익이 정해지는 구조(대개 원금지급형)
   - 판단할 수 없으면 null
5. conditions_text에는 '상환조건'처럼 상품 구조를 설명하는 문구 전체를 원문 그대로 적습니다.
6. 기초자산마다 Yahoo Finance 티커를 알고 있으면 ticker_suggestion에 적습니다(예: 삼성전자 005930.KS, 애플 AAPL, S&P500 ^GSPC). 확실하지 않으면 null. 이것만 이미지 밖 지식을 허용하는 예외입니다.
7. field_notes에는 값을 옮겨 적은 필드마다 근거가 된 원문 문장과 확신도(high/low)를 남깁니다. 글자가 흐리거나 해석이 애매하면 low.
8. 지원하지 않는 구조(기초자산 4개 이상, 쿠폰 지급 규칙을 알 수 없는 특수 구조 등)가 상환조건 문구에 보이면 unsupported_features에 짧은 영문 이름을, warnings에 한국어 설명을 적습니다. 'Hi-Five', 'Ultra', 'able' 같은 상품 브랜드 이름만으로 구조를 판단하지 마세요.
9. 청약기간·청약마감일은 투자기간이 아닙니다. investment_period_text에는 투자기간(발행일~만기일)만 적습니다.
10. '원금지급형', 'ELB', '조건 미충족시 손실률(최대) 0%'처럼 원금 손실 한도를 알려 주는 표시가 있으면 max_loss_text에 적습니다.
11. 계좌번호·이름 같은 개인정보는 어떤 필드에도 옮기지 않습니다.
12. 응답은 스키마에 맞는 JSON 하나로만 합니다.
"""

USER_PROMPT = "이 상품 안내 자료에서 ELS 조건을 스키마에 맞춰 옮겨 적어 주세요."


def _nullable_string(description: str) -> dict:
    return {"type": ["string", "null"], "description": description}


_TEXT_FIELDS: dict[str, str] = {
    "underlyings_text": "기초자산 원문",
    "investment_period_text": "투자기간·발행일~만기일 원문 (예: 2026/10/30 ~ 2029/11/02)",
    "maturity_text": "만기 원문 (예: 3년, 36개월)",
    "obs_period_text": "조기상환 평가 주기 원문 (예: 6개월마다)",
    "barriers_text": "조기상환·만기 배리어 나열 원문 (예: 90-90-85-85-80-75(50))",
    "coupon_text": "쿠폰·수익률 원문 (예: 연 8.0%, 6개월마다 4%)",
    "ki_text": "낙인 조건과 관찰 방식 원문 (예: KI 50, NO KI, 최초기준가격의 50% 미만으로 하락한 적이 있는 경우, 매월 말일 종가 기준 50% 미만)",
    "monthly_coupon_text": "월지급 조건 원문 (예: 월지급 65)",
    "lizard_text": "리자드 배리어 별도 설명 원문 (예: 리자드 75). 배리어 나열 안의 90(L75) 표기는 barriers_text에 그대로 적고 여기는 null",
    "product_name_text": "상품명·상품유형 원문 (예: KB able ELB 제409호, KB able ELS 제4600호, 슈퍼리자드스텝다운, 수익참조상승형)",
    "max_loss_text": "최대 손실률·원금 보장 원문 (예: 조건 미충족시 손실률(최대) 0%, 원금지급형, 원금 90% 보장)",
    "strike_text": "녹아웃형 행사가 원문 (예: 행사가 100%)",
    "participation_text": "녹아웃형 참여율 원문 (예: 참여율 100%)",
    "ko_barriers_text": "녹아웃(낙아웃) 배리어 원문 (예: 낙아웃 137%)",
    "rebate_text": "녹아웃 발생 시 수익률 원문 (예: 낙아웃발생시 수익률 3%)",
    "ko_observation_text": "녹아웃 관찰 방식 원문 (예: 투자기간 중 종가 기준 한번이라도)",
    "min_return_text": "최소 보장 수익률 원문",
    "conditions_text": "상품 구조를 설명하는 상환조건 문구 전체 원문",
}

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "product_type": {"type": ["string", "null"], "enum": ["autocall", "knockout", None]},
        "underlyings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "기초자산 이름 원문"},
                    "ticker_suggestion": _nullable_string("Yahoo Finance 티커 (모르면 null)"),
                },
                "required": ["name", "ticker_suggestion"],
                "additionalProperties": False,
            },
        },
        **{k: _nullable_string(v) for k, v in _TEXT_FIELDS.items()},
        "dummy_coupon": {"type": ["boolean", "null"], "description": "더미 쿠폰 여부 (언급 없으면 null)"},
        "field_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "필드 이름 (예: barriers_text)"},
                    "source_text": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["field", "source_text", "confidence"],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "unsupported_features": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "product_type", "underlyings", *_TEXT_FIELDS, "dummy_coupon",
        "field_notes", "warnings", "unsupported_features",
    ],
    "additionalProperties": False,
}

# field_notes의 필드 이름(원문 필드) → normalize 결과 필드 이름
NOTE_FIELD_MAP: dict[str, list[str]] = {
    "underlyings_text": ["underlyings"],
    "investment_period_text": ["maturity_months"],
    "maturity_text": ["maturity_months"],
    "obs_period_text": ["obs_period_months"],
    "barriers_text": ["barriers"],
    "coupon_text": ["coupon_annual"],
    "ki_text": ["ki_mode", "ki_barrier"],
    "monthly_coupon_text": ["monthly_coupon_barrier"],
    "max_loss_text": ["max_loss"],
    "lizard_text": ["lizard_barriers"],
    "strike_text": ["strike"],
    "participation_text": ["participation"],
    "ko_barriers_text": ["ko_upper", "ko_lower"],
    "rebate_text": ["rebate"],
    "ko_observation_text": ["ko_observation"],
    "min_return_text": ["min_return"],
}
