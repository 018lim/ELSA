"""화면 상태(session_state) 관리.

모든 폼 위젯은 여기 정의한 고정 key를 쓴다. 화면 값은 %(85) 단위이고,
elsa 패키지로 넘길 때 build_request()에서 한 번만 소수(0.85)로 바꾼다.

이미지 자동 입력은 Streamlit 제약(위젯 생성 후 같은 실행에서 값 변경 불가) 때문에
추출 결과를 PENDING_FILL에 넣고 rerun → 다음 실행 맨 앞에서 apply_pending_fill()이
위젯 key에 옮겨 쓴다.

- 위젯 key 상수와 key 함수(ul_name, ul_ticker, vol_key, div_key, div_meta, corr_key)
- init_state(): 첫 접속 시 기준값 예시(삼성전자/SK하이닉스)로 채움, 이후에는 persist_widgets()
- persist_widgets(): 화면에 안 그려진 위젯 값이 Streamlit에 의해 지워지지 않게 고정
- set_underlying_defaults(i): 종목명으로 종목표의 티커·기본 변동성·배당을 채움
- reset_corr(n): 상관계수를 기본값으로
- apply_pending_fill(): 대기 중인 추출 결과를 폼에 반영
- autofill_label(text, key) / autofill_help(key): "(이미지)" 표기와 근거 원문
- parse_lizard_input(text, n_obs): 리자드 입력칸 문자열("75-70", "75", "x-70") → 회차별 값
- build_request(): 화면 값 → (PricingRequest 또는 None, 오류 목록)
"""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from elsa import market
from elsa.extraction.normalize import parse_barriers, parse_lizard_barriers
from elsa.schemas import (
    AutocallTerms,
    ExtractionResult,
    KnockoutTerms,
    MarketData,
    PricingRequest,
    SimSettings,
)

MAX_UL = 3

PRODUCT = "product_type"
N_UL = "n_underlyings"
RATE = "mk_rate"

AC_MATURITY = "ac_maturity"
AC_PERIOD = "ac_period"
AC_BARRIERS = "ac_barriers"
AC_COUPON = "ac_coupon"
AC_MONTHLY = "ac_monthly"
AC_MONTHLY_BARRIER = "ac_monthly_barrier"
AC_KI_MODE = "ac_ki_mode"
AC_KI_BARRIER = "ac_ki_barrier"
AC_DUMMY = "ac_dummy"
AC_MAX_LOSS = "ac_max_loss"  # 최대 손실률 (%). None = 제한 없음
AC_LIZARD = "ac_lizard"  # 리자드 사용 여부
AC_LIZARD_BARRIER = "ac_lizard_barrier"  # 회차별 리자드 배리어 (%) 문자열. 예: "75-70" = 1·2회차만, "75" = 전 회차, "x-70" = 1회차 없음

KO_MATURITY = "ko_maturity"
KO_STRIKE = "ko_strike"
KO_PART = "ko_participation"
KO_UPPER_ON = "ko_upper_on"
KO_UPPER = "ko_upper"
KO_LOWER_ON = "ko_lower_on"
KO_LOWER = "ko_lower"
KO_REBATE = "ko_rebate"
KO_OBS = "ko_observation"
KO_MIN_RETURN = "ko_min_return"
KO_MAX_LOSS = "ko_max_loss"  # 최대 손실률 (%). None = 제한 없음, 0 = 원금지급형

EST_MONTHS = "est_months"  # 시세 추정 기간 (개월)

SIM_PATHS = "sim_paths"
SIM_SEED = "sim_seed"
SIM_TARGET = "sim_target"

VF_S0 = "vf_s0"  # 엔진 검증 탭 입력
VF_STRIKE = "vf_strike"
VF_VOL = "vf_vol"
VF_RATE = "vf_rate"
VF_DIV = "vf_div"
VF_YEARS = "vf_years"
VF_PATHS = "vf_paths"
VF_SEED = "vf_seed"
VF_RESULT = "vf_result"  # {"key": 입력값, "report": dict, "logic": list[dict]}

PENDING_FILL = "pending_fill"
AUTOFILL_META = "autofill_meta"  # {위젯 key: {"source": 근거 원문, "low": bool}}
MARKET_META = "market_meta"  # {자산 번호(변동성) 또는 div_meta(i)(배당) 또는 "corr": 출처 표기}
EXTRACTION_NOTICE = "extraction_notice"  # 직전 자동 입력 결과 요약 (안내 표시용)
IS_EXAMPLE = "is_example"
EXTRACTION_COUNT = "extraction_count"
EXTRACT_JOB = "extract_job"  # 실행 대기 중인 이미지 추출 작업 (업로드 바이트 또는 샘플 파일명)
LAST_RESULT = "last_result"  # {"request": json, "result": dict}
MARKET_MSG = "market_msg"  # 시세 추정 결과 안내 (종류, 문구)


def ul_name(i: int) -> str:
    return f"ul_name_{i}"


def ul_ticker(i: int) -> str:
    return f"ul_ticker_{i}"


def vol_key(i: int) -> str:
    return f"mk_vol_{i}"


def div_key(i: int) -> str:
    return f"mk_div_{i}"


def div_meta(i: int) -> str:
    """MARKET_META에서 i번째 자산 배당수익률의 출처 표기 key (변동성 출처는 정수 i)."""
    return f"div{i}"


def corr_key(i: int, j: int) -> str:
    i, j = min(i, j), max(i, j)
    return f"mk_corr_{i}_{j}"


EXAMPLE: dict[str, Any] = {
    PRODUCT: "autocall",
    N_UL: 2,
    RATE: 2.8,
    AC_MATURITY: 36,
    AC_PERIOD: 6,
    AC_BARRIERS: "90-90-85-85-80-75",
    AC_COUPON: 8.0,
    AC_MONTHLY: False,
    AC_MONTHLY_BARRIER: 65.0,
    AC_KI_MODE: "daily",
    AC_KI_BARRIER: 50.0,
    AC_DUMMY: True,
    AC_MAX_LOSS: None,
    AC_LIZARD: False,
    AC_LIZARD_BARRIER: "75-70",
    KO_MATURITY: 12,
    KO_STRIKE: 100.0,
    KO_PART: 100.0,
    KO_UPPER_ON: True,
    KO_UPPER: 137.0,
    KO_LOWER_ON: False,
    KO_LOWER: 70.0,
    KO_REBATE: 3.0,
    KO_OBS: "daily",
    KO_MIN_RETURN: 0.0,
    KO_MAX_LOSS: 0.0,  # 예시 녹아웃 상품(가상 샘플 제2호)이 원금지급형(ELB)이라 0%. 가정이 아닌 예시 조건
    EST_MONTHS: 12,
    SIM_PATHS: 100_000,
    SIM_SEED: 42,
    SIM_TARGET: 100.0,
    VF_S0: 100.0,
    VF_STRIKE: 100.0,
    VF_VOL: 20.0,
    VF_RATE: 3.0,
    VF_DIV: 0.0,
    VF_YEARS: 1.0,
    VF_PATHS: 100_000,
    VF_SEED: 42,
}
EXAMPLE_UNDERLYINGS = ["삼성전자", "SK하이닉스"]
EXAMPLE_CORR = 0.6


def _widget_defaults() -> dict[str, Any]:
    """모든 폼 위젯 key와 값이 없을 때 쓸 기본값."""
    defaults: dict[str, Any] = dict(EXAMPLE)
    defaults["sample_choice"] = "자동조기상환형 (예시증권 제1호)"  # ui/upload.py SAMPLES의 첫 항목
    defaults["access_code_input"] = ""
    defaults["paste_closes"] = ""
    for i in range(MAX_UL):
        defaults[ul_name(i)] = ""
        defaults[ul_ticker(i)] = ""
        defaults[vol_key(i)] = None
        defaults[div_key(i)] = 0.0
        for j in range(i + 1, MAX_UL):
            defaults[corr_key(i, j)] = market.DEFAULT_CORR
    return defaults


def persist_widgets() -> None:
    """화면에 그려지지 않은 위젯의 값이 지워지지 않게 매 실행 시작 시 다시 고정한다.

    Streamlit은 한 번의 실행에서 그려지지 않은 위젯(예: 노낙인일 때의 더미 쿠폰 체크박스,
    녹아웃형일 때의 자동조기상환형 입력)의 값을 session_state에서 지운다. 위젯을 그리기 전에
    값을 다시 대입하면 지워지지 않는다. 이미 지워진 값은 기본값으로 되살린다.
    """
    ss = st.session_state
    for key, default in _widget_defaults().items():
        ss[key] = ss[key] if key in ss else default


def init_state() -> None:
    """첫 실행에는 기준값 예시로 채우고, 이후 실행에는 위젯 값을 고정한다."""
    ss = st.session_state
    if "initialized" in ss:
        persist_widgets()
        return
    ss.update(EXAMPLE)
    for i in range(MAX_UL):
        ss[ul_name(i)] = EXAMPLE_UNDERLYINGS[i] if i < len(EXAMPLE_UNDERLYINGS) else ""
        set_underlying_defaults(i)
    reset_corr(EXAMPLE_CORR)
    ss[AUTOFILL_META] = {}
    ss[IS_EXAMPLE] = True
    ss[EXTRACTION_COUNT] = 0
    ss["initialized"] = True


def set_underlying_defaults(i: int, ticker_suggestion: str | None = None) -> None:
    """종목명으로 티커·변동성·배당을 채운다. 종목표에 없으면 비우고 제안 티커만 넣는다."""
    ss = st.session_state
    info = market.lookup(ss.get(ul_name(i), "") or "")
    meta = ss.setdefault(MARKET_META, {})
    if info:
        ss[ul_ticker(i)] = info.ticker
        ss[vol_key(i)] = round(info.vol * 100, 2)
        ss[div_key(i)] = round(info.div * 100, 2)
        meta[i] = meta[div_meta(i)] = "기본값"
    else:
        ss[ul_ticker(i)] = ticker_suggestion or ""
        ss[vol_key(i)] = None
        ss[div_key(i)] = 0.0
        meta.pop(i, None)
        meta.pop(div_meta(i), None)


def reset_corr(value: float = market.DEFAULT_CORR) -> None:
    for i in range(MAX_UL):
        for j in range(i + 1, MAX_UL):
            st.session_state[corr_key(i, j)] = value
    st.session_state.setdefault(MARKET_META, {}).pop("corr", None)


# ---------------------------------------------------------------------------
# 이미지 자동 입력 반영
# ---------------------------------------------------------------------------

_FIELD_KEYS = {
    "maturity_months": (AC_MATURITY, KO_MATURITY),
    "obs_period_months": (AC_PERIOD,),
    "barriers": (AC_BARRIERS,),
    "coupon_annual": (AC_COUPON,),
    "monthly_coupon_barrier": (AC_MONTHLY_BARRIER, AC_MONTHLY),
    "ki_mode": (AC_KI_MODE,),
    "ki_barrier": (AC_KI_BARRIER,),
    "dummy_coupon": (AC_DUMMY,),
    "max_loss": (AC_MAX_LOSS,),
    "lizard_barriers": (AC_LIZARD_BARRIER, AC_LIZARD),
    "strike": (KO_STRIKE,),
    "participation": (KO_PART,),
    "ko_upper": (KO_UPPER, KO_UPPER_ON),
    "ko_lower": (KO_LOWER, KO_LOWER_ON),
    "rebate": (KO_REBATE,),
    "ko_observation": (KO_OBS,),
    "min_return": (KO_MIN_RETURN,),
}

# 이미지에 없으면 비우거나 기본으로 돌릴 값 (예시값이 남아 사용자를 헷갈리게 하지 않도록)
_CLEAR_IF_MISSING = {
    AC_COUPON: None,
    AC_MONTHLY: False,
    AC_LIZARD: False,
    AC_DUMMY: False,
    AC_MAX_LOSS: None,
    KO_PART: None,
    KO_UPPER_ON: False,
    KO_LOWER_ON: False,
    KO_REBATE: 0.0,
    KO_MIN_RETURN: 0.0,
    KO_MAX_LOSS: None,  # 이미지에 손실률이 없으면 원금 보장을 가정하지 않고 비워 둔다
}


def _pct(x: float) -> float:
    return round(x * 100, 4)


def apply_pending_fill() -> None:
    """PENDING_FILL의 ExtractionResult를 위젯 key에 옮겨 쓴다. 위젯을 그리기 전에 호출해야 한다."""
    ss = st.session_state
    raw = ss.pop(PENDING_FILL, None)
    if raw is None:
        return
    res = ExtractionResult.model_validate(raw)
    f = res.fields
    meta: dict[str, dict] = {}

    def mark(field: str, *keys: str) -> None:
        for key in keys:
            meta[key] = {
                "source": res.source_text.get(field, ""),
                "low": res.field_confidence.get(field) == "low",
            }

    if res.product_type:
        ss[PRODUCT] = res.product_type

    for key, value in _CLEAR_IF_MISSING.items():
        ss[key] = value

    names = f.get("underlyings")
    if names:
        names = names[:MAX_UL]
        ss[N_UL] = len(names)
        for i in range(MAX_UL):
            ss[ul_name(i)] = names[i] if i < len(names) else ""
            set_underlying_defaults(i, res.ticker_suggestions.get(names[i]) if i < len(names) else None)
            if i < len(names):
                mark("underlyings", ul_name(i))
                if names[i] in res.ticker_suggestions and market.lookup(names[i]) is None:
                    meta[ul_ticker(i)] = {"source": "AI가 제안한 티커입니다. 확인해 주세요.", "low": True}
        reset_corr()

    for field, keys in _FIELD_KEYS.items():
        if field not in f:
            continue
        value = f[field]
        if field == "max_loss" and res.product_type == "knockout":
            keys = (KO_MAX_LOSS,)  # 최대 손실률은 상품 유형별 입력칸이 따로 있다
        primary = keys[0]
        if field == "barriers":
            ss[primary] = "-".join(f"{_pct(b):g}" for b in value)
        elif field == "lizard_barriers":
            ss[primary] = "-".join("x" if v is None else f"{_pct(v):g}" for v in value)
            ss[keys[1]] = True
        elif field in ("maturity_months", "obs_period_months"):
            for key in keys:
                ss[key] = int(value)
        elif field in ("ki_mode", "ko_observation", "dummy_coupon"):
            ss[primary] = value
        else:
            ss[primary] = _pct(float(value))
            if len(keys) > 1:  # 켜짐 체크박스
                ss[keys[1]] = True
        mark(field, *keys)

    ss[AUTOFILL_META] = meta
    ss[IS_EXAMPLE] = False
    ss[LAST_RESULT] = None
    ss[EXTRACTION_NOTICE] = res.model_dump()


def autofill_label(text: str, key: str) -> str:
    return f"{text} (이미지)" if key in st.session_state.get(AUTOFILL_META, {}) else text


def autofill_help(key: str, default: str | None = None) -> str | None:
    info = st.session_state.get(AUTOFILL_META, {}).get(key)
    if not info:
        return default
    prefix = "⚠ 확인 필요 · " if info.get("low") else ""
    return f"{prefix}근거 원문: {info['source']}" if info.get("source") else default


# ---------------------------------------------------------------------------
# 화면 값 → 평가 요청
# ---------------------------------------------------------------------------


def _frac(key: str) -> float | None:
    v = st.session_state.get(key)
    return None if v is None else float(v) / 100


def parse_lizard_input(text: str, n_obs: int) -> tuple[list[float | None] | None, str | None]:
    """리자드 배리어 입력 문자열 → (회차별 값 목록, 오류 메시지).

    '75-70' = 1·2회차만, '75' = 전 회차, 'x-70' 또는 '-70' = 1회차는 리자드 없음. 값은 % 단위.
    """
    tokens = [t.strip() for t in re.split(r"[-–—,]", text.strip().lower())]
    if all(t in ("", "x") for t in tokens):
        return None, "리자드 배리어를 '75-70'처럼 입력해 주세요. 1회차부터 순서대로 하이픈(-)으로 잇고, 없는 회차는 x로 적습니다."
    values: list[float | None] = []
    for t in tokens:
        if t in ("", "x"):
            values.append(None)
            continue
        try:
            values.append(float(t) / 100)
        except ValueError:
            return None, f"리자드 배리어에 숫자가 아닌 값('{t}')이 있습니다. '75-70'처럼 입력해 주세요."
    if len(values) == 1 and values[0] is not None:
        values = values * n_obs  # 값 하나는 전 회차에 적용
    return values, None


def build_request() -> tuple[PricingRequest | None, list[str]]:
    """화면 값으로 PricingRequest를 만든다. 형식 문제(빈 칸, 배리어 해석 실패)는 오류 목록으로."""
    ss = st.session_state
    errors: list[str] = []
    n = int(ss[N_UL])
    names = [(ss.get(ul_name(i)) or "").strip() for i in range(n)]

    if ss[PRODUCT] == "autocall":
        barriers, _ = parse_barriers(ss.get(AC_BARRIERS) or "")
        if not barriers:
            errors.append("상환 배리어를 '90-90-85-85-80-75'처럼 하이픈(-)으로 이어 입력해 주세요.")
        ki_mode = ss[AC_KI_MODE]
        # 배리어 칸에 90(L75)-80처럼 적은 리자드 표기가 우선, 없으면 리자드 체크박스의 입력칸
        lizard_values = parse_lizard_barriers(ss.get(AC_BARRIERS) or "", len(barriers))
        if lizard_values is None and ss[AC_LIZARD]:
            lizard_values, lizard_error = parse_lizard_input(
                str(ss.get(AC_LIZARD_BARRIER) or ""), max(len(barriers), 1)
            )
            if lizard_error:
                errors.append(lizard_error)
        ts = AutocallTerms(
            underlyings=names,
            maturity_months=int(ss[AC_MATURITY] or 0),
            obs_period_months=int(ss[AC_PERIOD] or 0),
            barriers=barriers,
            coupon_annual=_frac(AC_COUPON),
            ki_mode=ki_mode,
            ki_barrier=_frac(AC_KI_BARRIER) if ki_mode != "none" else None,
            dummy_coupon=bool(ss[AC_DUMMY]) and ki_mode != "none",  # 월지급식·리자드면 엔진이 무시
            monthly_coupon_barrier=_frac(AC_MONTHLY_BARRIER) if ss[AC_MONTHLY] else None,
            max_loss=_frac(AC_MAX_LOSS),
            lizard_barriers=lizard_values,
        )
        if ss[AC_MONTHLY] and ss.get(AC_MONTHLY_BARRIER) is None:
            errors.append("월지급식이면 월지급 배리어를 입력해 주세요.")
    else:
        ts = KnockoutTerms(
            underlyings=names,
            maturity_months=int(ss[KO_MATURITY] or 0),
            strike=_frac(KO_STRIKE) or 1.0,
            participation=_frac(KO_PART),
            ko_upper=_frac(KO_UPPER) if ss[KO_UPPER_ON] else None,
            ko_lower=_frac(KO_LOWER) if ss[KO_LOWER_ON] else None,
            rebate=_frac(KO_REBATE) or 0.0,
            ko_observation=ss[KO_OBS],
            min_return=_frac(KO_MIN_RETURN) or 0.0,
            max_loss=_frac(KO_MAX_LOSS),
        )

    vols, divs = [], []
    for i in range(n):
        v = _frac(vol_key(i))
        if v is None:
            errors.append(f"{names[i] or f'{i + 1}번째 기초자산'}의 변동성을 입력해 주세요.")
        vols.append(v or 0.0)
        divs.append(_frac(div_key(i)) or 0.0)
    corr = [[1.0 if i == j else float(ss[corr_key(i, j)]) for j in range(n)] for i in range(n)]
    mk = MarketData(vols=vols, divs=divs, corr=corr, rate=_frac(RATE) or 0.0)
    sim = SimSettings(n_paths=int(ss[SIM_PATHS]), seed=int(ss[SIM_SEED]), target=_frac(SIM_TARGET) or 1.0)

    if errors:
        return None, errors
    return PricingRequest(term_sheet=ts, market=mk, sim=sim), []
