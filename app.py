"""ELSA Streamlit 진입점. 화면 구성만 하고 계산 로직은 elsa 패키지에 둔다.

소개 아래에 두 탭을 둔다.
- ELS 평가: 이미지 자동 입력 → 상품 조건 → 시장 데이터 → 시뮬레이션 설정
  → 평가하기·결과 → 민감도 분석 → 모형 가정
- 엔진 검증: 유럽형 콜옵션의 MC 가격을 Black-Scholes 해석해와 비교
탭 아래에 면책 문구와 추출기를 표시한다. 자동 입력 결과는 위젯을 그리기 전에 apply_pending_fill()로 폼에 반영한다.
"""

from __future__ import annotations

import streamlit as st

from elsa.extraction.factory import describe_extractor
from elsa.schemas import PricingRequest, PricingResult
from elsa.validation import validate_request
from ui import form, results, upload, verification
from ui import state as S

st.set_page_config(page_title="ELSA · ELS 평가기", page_icon="📊", layout="centered")

S.init_state()
S.apply_pending_fill()  # 반드시 위젯보다 먼저

st.title("ELSA")
st.markdown(
    "**ELS Scan & Assess** — 자동조기상환형·녹아웃형 ELS의 조건을 입력하거나 상품 안내 이미지를 올리면, "
    "몬테카를로 시뮬레이션으로 **공정가치**, **적정 쿠폰**, **상환 확률**을 계산합니다."
)
tab_main, tab_verify = st.tabs(["ELS 평가", "엔진 검증"])

with tab_main:
    c1, c2, c3 = st.columns(3)
    c1.info("**1. 입력**\n\n이미지를 올리거나 조건을 직접 입력")
    c2.info("**2. 확인**\n\n자동으로 채운 값과 시장 데이터 확인")
    c3.info("**3. 평가**\n\n평가하기를 눌러 결과 보기")

    upload.render_upload()
    st.divider()
    upload.render_notice()
    form.render_product_form()
    st.divider()
    form.render_market_form()
    form.render_sim_settings()

    if st.button("평가하기", type="primary", width="stretch"):
        req, errors = S.build_request()
        if req is not None:
            report = validate_request(req)
            errors = report.errors
        if errors:
            st.session_state[S.LAST_RESULT] = None
            st.error("입력값을 확인해 주세요.\n\n" + "\n".join(f"- {e}" for e in errors))
        else:
            with st.spinner("시뮬레이션 중입니다..."):
                key = req.model_dump_json()
                st.session_state[S.LAST_RESULT] = {
                    "request": key, "result": results.cached_price(key), "warnings": report.warnings,
                }

    saved = st.session_state.get(S.LAST_RESULT)
    if saved:
        st.divider()
        req = PricingRequest.model_validate_json(saved["request"])
        current, _ = S.build_request()
        if current is None or current.model_dump_json() != saved["request"]:
            st.caption("⚠ 입력값이 바뀌었습니다. 아래 결과는 이전 입력 기준이니 다시 평가해 주세요.")
        for w in saved["warnings"]:
            st.warning(w)
        results.render_result(req, PricingResult.model_validate(saved["result"]))
        st.divider()
        results.render_sensitivity(req)

    st.divider()
    with st.expander("모형 가정과 한계"):
        st.markdown(
            """
- 위험중립 기하브라운운동, 촐레스키 분해로 기초자산 간 상관관계 반영, 연 252영업일 일별 시뮬레이션, 대조변수 분산 축소
- 변동성·배당·금리는 상수로 가정합니다. 변동성은 과거(실현) 변동성 또는 참고용 기본값이며, 실제 평가사가 쓰는 내재변동성·변동성 곡면과 다릅니다
- 금리 기간구조, 이산배당, 발행사 신용위험은 반영하지 않습니다
- 녹아웃형은 만기 일시 지급을 가정합니다. 원금 보장 여부는 가정하지 않고 '최대 손실률'로 입력받습니다(0%는 원금지급형, 비우면 제한 없음). 녹아웃이 없을 때 불리한 방향(상승형은 하락, 하락형은 상승)으로 움직인 만큼 최대 손실률까지 손실을 봅니다. 기초자산이 여러 개면 워스트 퍼포머 기준입니다
- 자동조기상환형도 같은 '최대 손실률' 규칙을 씁니다. 비워 두면 만기 손실 시 워스트 비율만큼 상환합니다
- 월지급식은 매월 말 워스트가 월지급 배리어 이상이면 그달 쿠폰(연 쿠폰 ÷ 12)을 지급한다고 가정합니다
- 리자드(슈퍼리자드 포함)는 평가일에 워스트가 회차별 리자드 배리어 이상이면 그 회차 쿠폰을 그날 지급하고, 상환될 때는 그 회차 쿠폰만 받는다고 가정합니다. 실제 지급 규칙은 상품마다 달라 공정가치가 크게 달라질 수 있으니 투자설명서와 대조해 주세요
- 배당수익률은 최근 1년 실제 배당으로 계산하며, 지수는 추종 ETF의 배당을 대신 씁니다(지수 배당수익률과 조금 다를 수 있음)
- 적정 쿠폰은 같은 난수에서 공정가치가 목표값이 되는 쿠폰으로, 입력한 시장 가정에 따라 크게 달라집니다
"""
        )

with tab_verify:
    verification.render_verification()

st.divider()
st.caption(
    "이 평가기의 결과는 투자 권유나 공식 평가가격이 아닙니다. 학습용 평가기이며, 모형 가정과 입력값에 따라 결과가 크게 달라질 수 있습니다."
)
st.caption(f"이미지 추출기: {describe_extractor()}")
