"""표기·단위 정규화 (순수 함수).

추출기는 이미지의 원문 표기를 거의 그대로 문자열로 돌려주고(예: "85-85-80-75 / KI 40",
"행사가 100%", "2026/10/30 ~ 2029/11/02"), 이 모듈이 키워드와 값 형식으로 해석한다.
글자 위치·줄 순서 같은 레이아웃에는 의존하지 않는다. 비율은 소수로 돌려준다.

숫자 단위 규칙: '%'가 붙어 있으면 % 값. 숫자만 있으면 필드별 기준값보다 클 때 %로 본다
(배리어·쿠폰 등은 1, 행사가·참여율·녹아웃 배리어는 3. 예: 137 → 1.37, 1.37 → 1.37).

- to_fraction(value, pct_threshold): 숫자·문자열 → 소수
- parse_lizard_barriers(text, n_rounds): "90(L75)-90(L70)-85-80-75-70" → 회차별 리자드 배리어 [0.75, 0.70]
- parse_barriers(text): 배리어 나열 → (배리어 목록, 괄호·KI 표기의 낙인 값). 리자드 표기는 걷어 냄
- parse_ki(text): 낙인 문구 → (ki_mode, ki_barrier). 관찰 방식은 매일 / 매월 말일 / 만기일 / 노낙인
- parse_months(text): "3년", "18개월", "1년 6개월" → 개월 수
- months_between_dates(text): "2026/10/30 ~ 2029/11/02" → 가장 가까운 개월 수
- parse_coupon(text, obs_period_months): "연 8%", "6개월마다 4%", "월 0.5%" → 연 쿠폰
- parse_monthly_barrier(text): "월지급 65" → 0.65
- parse_max_loss(text): "손실률(최대) 0%", "원금 90% 보장", "원금지급형" → 최대 손실률
- max_loss_from_product_name(name): 손실률 표기가 없을 때 상품명의 ELB·DLB로 원금지급형 판별
- parse_ko_barriers(text, strike): "낙아웃 137%" → (ko_upper, ko_lower)
- parse_ko_observation(text): 녹아웃 관찰 방식
- normalize_name(name): 종목표 기준 이름 ("코스피200" → "KOSPI200")
- normalize_extraction(raw, extractor): 추출기 응답 dict → ExtractionResult
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from elsa.extraction.prompt import NOTE_FIELD_MAP
from elsa.market import canonical_name
from elsa.schemas import ExtractionResult

_NUM = r"\d+(?:\.\d+)?"
_DASH = r"[-–—−]"  # '~'는 기간(날짜) 표기에 쓰이므로 배리어 구분자로 보지 않는다


def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(_NUM, text.replace(",", ""))]


def to_fraction(value: Any, pct_threshold: float = 1.0) -> float | None:
    """숫자나 '85%', '0.85', '연 8.0%' 같은 문자열을 소수로 바꾼다. 해석할 수 없으면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        return x / 100 if x > pct_threshold else x
    text = str(value)
    m = re.search(rf"({_NUM})\s*%", text.replace(",", ""))
    if m:
        return float(m.group(1)) / 100
    nums = _numbers(text)
    return to_fraction(nums[0], pct_threshold) if nums else None


_LIZARD_TOKEN = re.compile(rf"\(\s*L(?:izard)?\s*[:-]?\s*{_NUM}\s*%?\s*\)", re.I)
_LIZARD_VALUE = re.compile(rf"(?:\bL(?:izard)?\s*[:-]?\s*|리자드\s*(?:배리어|조건)?\s*[:-]?\s*)({_NUM})", re.I)
_ROUND = rf"{_NUM}(?:\s*\(\s*L(?:izard)?\s*[:-]?\s*{_NUM}\s*%?\s*\))?"
_ROUND_SEQ = re.compile(rf"{_ROUND}(?:\s*{_DASH}\s*{_ROUND})+", re.I)
_ROUND_ITEM = re.compile(rf"({_NUM})(?:\s*\(\s*L(?:izard)?\s*[:-]?\s*({_NUM})\s*%?\s*\))?", re.I)


def parse_lizard_barriers(text: str | None, n_rounds: int | None = None) -> list[float | None] | None:
    """회차별 리자드 배리어.

    '90(L75)-90(L70)-85-80-75-70' → [0.75, 0.70, None, None, None, None] (뒤쪽 None은 잘라 [0.75, 0.70]).
    회차별 표기가 없고 '리자드 75'처럼 값 하나만 있으면 모든 회차(n_rounds)에 같은 값. 리자드 표기가 없으면 None.
    """
    if not text:
        return None
    m = _ROUND_SEQ.search(text)
    if m:
        values = [to_fraction(float(l)) if l else None for _, l in _ROUND_ITEM.findall(m.group(0))]
        while values and values[-1] is None:
            values.pop()
        if values:
            return values
    single = _LIZARD_VALUE.search(text)
    if single and n_rounds:
        return [to_fraction(float(single.group(1)))] * n_rounds
    return None


def parse_barriers(text: str | None) -> tuple[list[float], float | None]:
    """'90-90-85-85-80-75(50)', '85-...-70 / KI 40' → ([0.9, ...], 낙인 값 또는 None).
    '90(L75)-90(L75)'처럼 배리어 뒤에 붙은 리자드 표기는 배리어 해석 전에 걷어 낸다."""
    if not text:
        return [], None
    text = _LIZARD_TOKEN.sub("", text)
    m = re.search(rf"{_NUM}(?:\s*%?\s*{_DASH}\s*{_NUM})+\s*%?", text)
    if not m:
        nums = _numbers(text)
        return ([to_fraction(n) for n in nums] if len(nums) == 1 else []), None
    barriers = [to_fraction(n) for n in _numbers(m.group(0))]
    rest = text[m.end():]
    ki = None
    paren = re.match(rf"\s*\(\s*(?:KI\s*)?({_NUM})\s*%?\s*\)", rest, re.I)
    if paren:
        ki = to_fraction(float(paren.group(1)))
    else:
        _, ki = parse_ki(text)
    return barriers, ki


_NO_KI = re.compile(r"NO\s*-?\s*KI|노\s*낙인|무\s*낙인|낙인\s*없", re.I)
_DURING = re.compile(r"적이|기간\s*중|한\s*번이라도|하루라도|매일|일일|장중|종가\s*기준")
_MONTH_END = re.compile(r"매\s*월\s*(?:말|마지막|최종)|월\s*말일?|매월\s*평가|월별\s*관찰|매월\s*관찰")
_MATURITY_ONLY = re.compile(r"만기\s*평가일|만기일|만기\s*시")


def parse_ki(text: str | None) -> tuple[str | None, float | None]:
    """낙인 문구 → (ki_mode, ki_barrier). 해석할 수 없으면 (None, None)."""
    if not text:
        return None, None
    if _NO_KI.search(text):
        return "none", None
    m = re.search(rf"(?:KI|낙인|Knock[\s-]*In)\s*[:(]?\s*({_NUM})\s*%?", text, re.I)
    if not m:
        m = re.search(rf"({_NUM})\s*%\s*미만", text)
    if not m:
        return None, None
    barrier = to_fraction(float(m.group(1)))
    if _MONTH_END.search(text):
        return "monthly", barrier
    if _MATURITY_ONLY.search(text) and not _DURING.search(text):
        return "maturity", barrier
    return "daily", barrier


_DATE = re.compile(r"(\d{4})\s*[./\-년]\s*(\d{1,2})\s*[./\-월]\s*(\d{1,2})")


def parse_months(text: str | None) -> int | None:
    """'3년' → 36, '18개월' → 18, '1년 6개월' → 18, '6개월마다' → 6. 없으면 None.

    '2029년 11월 2일' 같은 날짜는 기간이 아니므로 지우고 본다 (2029년을 기간으로 읽지 않도록).
    """
    if not text:
        return None
    text = _DATE.sub(" ", text)
    # "1년 6개월"처럼 붙어 있으면 합산, "3년 만기 6개월 단위"처럼 떨어져 있으면 처음 나온 기간
    both = re.search(rf"({_NUM})\s*년\s*({_NUM})\s*개월", text)
    if both:
        return int(round(float(both.group(1)) * 12 + float(both.group(2))))
    m = re.search(rf"({_NUM})\s*(년|개월)", text)
    if not m:
        return None
    return int(round(float(m.group(1)) * (12 if m.group(2) == "년" else 1)))


def months_between_dates(text: str | None) -> int | None:
    """기간 문자열 속 두 날짜 사이의 개월 수 (가장 가까운 정수).

    날짜가 두 개 없거나, 잘못 읽은 날짜이거나, 순서가 뒤집혀 기간이 0 이하면 None.
    """
    if not text:
        return None
    found = _DATE.findall(text)
    if len(found) < 2:
        return None
    try:
        d1, d2 = (date(int(y), int(m), int(d)) for y, m, d in found[:2])
    except ValueError:
        return None
    months = int(round((d2 - d1).days / (365.25 / 12)))
    return months if months > 0 else None


def parse_coupon(text: str | None, obs_period_months: int | None = None) -> float | None:
    """쿠폰 문구 → 연 쿠폰(소수). '6개월마다 4%'는 평가 주기로 연환산, '월 0.5%'는 ×12."""
    if not text:
        return None
    # 연·월 수익률이 함께 적힌 경우("월 0.51%, 연 6.12%")를 위해 키워드 바로 뒤의 숫자를 우선한다
    label = r"\s*(?:수익률|쿠폰|이율|지급)?\s*[:：]?\s*(?:최대\s*)?(" + _NUM + r")\s*%"
    annual = re.search(r"(?:연|annual|p\.?a\.?)" + label, text, re.I)
    if annual:
        return float(annual.group(1)) / 100
    monthly = re.search(r"(?:(?<!개)월|매월)" + label, text)
    if monthly:
        return float(monthly.group(1)) / 100 * 12
    rate = to_fraction(text)
    if rate is None:
        return None
    per = parse_months(text) if "마다" in text or "매" in text else None
    if per:
        return rate * 12 / per
    return rate


def parse_monthly_barrier(text: str | None) -> float | None:
    """'월지급 65', '월지급식(65%)' → 0.65."""
    if not text:
        return None
    m = re.search(rf"월\s*지급\D{{0,8}}?({_NUM})\s*%?", text)
    if m:
        return to_fraction(float(m.group(1)))
    nums = _numbers(text)
    return to_fraction(nums[0]) if nums else None


def parse_max_loss(text: str | None) -> float | None:
    """최대 손실률 문구 → 소수. '손실률(최대) 0%' → 0, '원금 90% 보장' → 0.1, '원금지급형'·'ELB' → 0.

    '원금비보장'처럼 보장이 없다는 표기나 해석할 수 없는 문구는 None(제한 없음).
    """
    if not text:
        return None
    if re.search(r"비\s*보장|원금\s*손실\s*가능", text):
        return None
    m = re.search(rf"손실\s*률?[^0-9%]{{0,12}}({_NUM})\s*%", text)
    if m:
        return float(m.group(1)) / 100
    m = re.search(rf"원금\D{{0,6}}({_NUM})\s*%\s*(?:보장|지급)", text)
    if m:
        return 1 - float(m.group(1)) / 100
    if re.search(r"원금\s*(?:지급|보장)형?|\bELB\b|\bDLB\b|파생결합사채", text, re.I):
        return 0.0
    return None


def max_loss_from_product_name(name: str | None) -> float | None:
    """손실률 표기가 없을 때 상품명으로 원금지급형을 판별한다.

    'ELB'·'DLB'(파생결합사채)나 '원금지급형'이 상품명에 있으면 원금을 지급하는 구조라 0.
    'ELS'는 원금비보장이 기본이라 그것만으로는 손실 한도를 알 수 없어 None(제한 없음으로 둠).
    """
    if not name:
        return None
    return 0.0 if re.search(r"\b[ED]LB\b|원금\s*지급\s*형|파생결합사채", name, re.I) else None


def parse_ko_barriers(text: str | None, strike: float = 1.0) -> tuple[float | None, float | None]:
    """녹아웃 배리어 문구 → (ko_upper, ko_lower). 행사가보다 크면 상승, 작으면 하락."""
    if not text:
        return None, None
    m = re.search(rf"(?:낙아웃|녹아웃|knock[\s-]*out|KO)[^0-9]{{0,10}}({_NUM}\s*%?(?:\s*[/,및]\s*{_NUM}\s*%?)*)", text, re.I)
    segment = m.group(1) if m else text
    values = [to_fraction(v, 3.0) for v in _numbers(segment)]
    upper = [v for v in values if v > strike]
    lower = [v for v in values if v < strike]
    return (min(upper) if upper else None), (max(lower) if lower else None)


def parse_ko_observation(text: str | None) -> str | None:
    if not text:
        return None
    if _MATURITY_ONLY.search(text) and not _DURING.search(text):
        return "maturity"
    if _DURING.search(text):
        return "daily"
    return None


def normalize_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    return canonical_name(name) or name


def _text(raw: dict, key: str) -> str | None:
    v = raw.get(key)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def normalize_extraction(raw: dict[str, Any], extractor: str = "") -> ExtractionResult:
    """추출기 응답(prompt.py 스키마) → ExtractionResult.

    필드별 전용 문구를 먼저 보고, 없으면 conditions_text(상환조건 전체 원문)에서 키워드로 찾는다.
    """
    fields: dict[str, Any] = {}
    source: dict[str, str] = {}
    confidence: dict[str, str] = {}
    warnings = _drop_supported([str(w) for w in raw.get("warnings") or []])
    unsupported = _drop_supported([str(u) for u in raw.get("unsupported_features") or []])
    all_text = _text(raw, "conditions_text") or ""

    for note in raw.get("field_notes") or []:
        f = note.get("field")
        if not f:
            continue
        for target in NOTE_FIELD_MAP.get(f, [f]):
            if note.get("source_text"):
                source[target] = str(note["source_text"])
            if note.get("confidence") in ("high", "low"):
                confidence[target] = note["confidence"]

    def put(name: str, value: Any, src: str | None, conf: str | None = None) -> None:
        if value is None:
            return
        fields[name] = value
        if src and name not in source:
            source[name] = src
        if conf:
            confidence[name] = conf
        confidence.setdefault(name, "high")

    def pick(key: str) -> str | None:
        return _text(raw, key) or None

    # 기초자산 · 티커 제안
    tickers: dict[str, str] = {}
    names = []
    for u in raw.get("underlyings") or []:
        u = u if isinstance(u, dict) else {"name": u}
        if not u.get("name"):
            continue
        name = normalize_name(str(u["name"]))
        names.append(name)
        if u.get("ticker_suggestion") and canonical_name(name) is None:
            tickers[name] = str(u["ticker_suggestion"]).strip()
    if names:
        put("underlyings", names, pick("underlyings_text"))
        if len(names) > 3:
            unsupported.append("more_than_3_underlyings")
            warnings.append(f"기초자산이 {len(names)}개입니다. 3개까지만 지원합니다.")

    # 만기: 명시 문구 → 투자기간 날짜
    maturity = parse_months(pick("maturity_text"))
    if maturity:
        put("maturity_months", maturity, pick("maturity_text"))
    else:
        maturity = months_between_dates(pick("investment_period_text"))
        if maturity:
            put("maturity_months", maturity, pick("investment_period_text"))

    # 상품 유형
    ko_text = pick("ko_barriers_text") or (all_text if re.search(r"낙아웃|녹아웃|knock[\s-]*out", all_text, re.I) else None)
    product_type = raw.get("product_type")
    if product_type not in ("autocall", "knockout"):
        product_type = "knockout" if ko_text else ("autocall" if pick("barriers_text") else None)

    if product_type == "autocall":
        barriers_src = pick("barriers_text") or all_text
        barriers, ki_from_barriers = parse_barriers(barriers_src)
        if len(barriers) >= 2:
            put("barriers", barriers, barriers_src)

        period = parse_months(pick("obs_period_text"))
        if period:
            put("obs_period_months", period, pick("obs_period_text"))
        elif maturity and len(barriers) >= 2 and maturity % len(barriers) == 0:
            period = maturity // len(barriers)
            put("obs_period_months", period, f"만기 {maturity}개월 ÷ 배리어 {len(barriers)}개로 추정", "low")

        ki_src = pick("ki_text") or barriers_src
        ki_mode, ki_barrier = parse_ki(ki_src)
        if ki_mode is None and ki_from_barriers is not None:
            ki_mode, ki_barrier = "daily", ki_from_barriers
        if ki_mode:
            put("ki_mode", ki_mode, ki_src, "high" if pick("ki_text") and ki_mode != "daily" else None)
            if ki_barrier is not None:
                put("ki_barrier", ki_barrier, ki_src)

        coupon = parse_coupon(pick("coupon_text"), period)
        if coupon is not None:
            put("coupon_annual", coupon, pick("coupon_text"))

        mcb_src = pick("monthly_coupon_text") or (all_text if re.search(r"월\s*지급", all_text) else None)
        mcb = parse_monthly_barrier(mcb_src)
        if mcb is not None:
            put("monthly_coupon_barrier", mcb, mcb_src)

        if isinstance(raw.get("dummy_coupon"), bool):
            put("dummy_coupon", raw["dummy_coupon"], source.get("dummy_coupon"))

        loss_src = pick("max_loss_text") or all_text
        max_loss = parse_max_loss(loss_src)
        if max_loss is None:  # 손실률 표기가 없으면 상품명(ELB 등)으로 판별
            loss_src = pick("product_name_text")
            max_loss = max_loss_from_product_name(loss_src)
        if max_loss is not None:
            put("max_loss", max_loss, loss_src)

        # 배리어 나열 안의 90(L75) 표기가 우선, 없으면 별도 리자드 문구("리자드 75")
        lizard_src = barriers_src
        lizard = parse_lizard_barriers(barriers_src, len(barriers))
        if lizard is None:
            lizard_src = pick("lizard_text")
            lizard = parse_lizard_barriers(lizard_src, len(barriers))
        if lizard:
            put("lizard_barriers", lizard, lizard_src)

        if maturity and period and barriers and maturity // period != len(barriers):
            warnings.append(
                f"평가 횟수({maturity // period}회)와 배리어 개수({len(barriers)}개)가 다릅니다. 값을 확인해 주세요."
            )

    elif product_type == "knockout":
        strike = to_fraction(pick("strike_text") or _after(all_text, "행사가"), 3.0)
        if strike is not None:
            put("strike", strike, pick("strike_text") or all_text)
        part = to_fraction(pick("participation_text") or _after(all_text, "참여율"), 3.0)
        if part is not None:
            put("participation", part, pick("participation_text") or all_text)
        upper, lower = parse_ko_barriers(ko_text, strike or 1.0)
        if upper is not None:
            put("ko_upper", upper, ko_text)
        if lower is not None:
            put("ko_lower", lower, ko_text)
        rebate_src = pick("rebate_text") or _after(all_text, r"(?:낙아웃|녹아웃)\s*발생\s*시?\s*수익률")
        rebate = to_fraction(rebate_src)
        if rebate is not None:
            put("rebate", rebate, pick("rebate_text") or all_text)
        min_ret = to_fraction(pick("min_return_text") or _after(all_text, r"최소\s*(?:보장\s*)?수익률?"))
        if min_ret is not None:
            put("min_return", min_ret, pick("min_return_text") or all_text)
        loss_src = pick("max_loss_text")
        max_loss = parse_max_loss(loss_src)
        if max_loss is None:
            loss_src = pick("product_name_text")
            max_loss = max_loss_from_product_name(loss_src)
        if max_loss is not None:
            put("max_loss", max_loss, loss_src)
        obs_src = pick("ko_observation_text")
        obs = parse_ko_observation(obs_src)
        if obs:
            put("ko_observation", obs, obs_src)
        else:
            put("ko_observation", "daily", "관찰 방식 문구 없음 → 매일 관찰로 가정", "low")

    return ExtractionResult(
        product_type=product_type,
        fields=fields,
        ticker_suggestions=tickers,
        field_confidence={k: v for k, v in confidence.items() if k in fields},
        source_text={k: v for k, v in source.items() if k in fields},
        warnings=list(dict.fromkeys(warnings)),
        unsupported_features=list(dict.fromkeys(unsupported)),
        extractor=extractor,
    )


# 앱이 지원하는 구조·상품 브랜드 이름. 모델이 이것들을 '지원하지 않는 구조'로 잘못 보고하면 걸러 낸다
_SUPPORTED_FEATURE = re.compile(
    r"month|monthly|월\s*지급|hi[\s_-]*five|하이\s*파이브|ultra|울트라|lizard|리자드|knock[\s_-]*out|녹아웃|낙아웃|"
    r"principal|원금\s*(?:지급|보장)|\belb\b|step[\s_-]*down|스텝\s*다운|no[\s_-]*ki|노낙인|dummy|더미",
    re.I,
)


def _drop_supported(items: list[str]) -> list[str]:
    return [x for x in items if not _SUPPORTED_FEATURE.search(x)]


def _after(text: str, keyword: str) -> str | None:
    """text에서 keyword 바로 뒤의 '숫자[%]'를 문자열로 돌려준다."""
    if not text:
        return None
    m = re.search(rf"{keyword}\s*[:은는이가]?\s*({_NUM}\s*%?)", text)
    return m.group(1) if m else None
