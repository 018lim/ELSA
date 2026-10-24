"""결과 화면: 공정가치·판정 문구, 적정 쿠폰, 핵심 지표, 회차별 상환 확률 표·차트, 민감도.

엔진 결과(소수)를 화면 단위(%)로 바꿔 보여 준다. 평가는 입력값(JSON) 기준으로 캐시한다.

- cached_price(request_json): 평가 결과 캐시 (@st.cache_data)
- cached_sensitivity(request_json): 민감도 결과 캐시
- verdict(price): 공정가치 수준 판정 문구
- render_result(request, result): 결과 전체 표시
- render_sensitivity(request): 민감도 분석 버튼과 결과 표
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from elsa.pricing.engine import price
from elsa.pricing.sensitivity import run_sensitivity
from elsa.schemas import AutocallTerms, KnockoutTerms, PricingRequest, PricingResult


@st.cache_data(show_spinner=False, max_entries=64)
def cached_price(request_json: str) -> dict:
    return price(PricingRequest.model_validate_json(request_json)).model_dump()


@st.cache_data(show_spinner=False, max_entries=16)
def cached_sensitivity(request_json: str) -> list[dict]:
    return [vars(r) for r in run_sensitivity(PricingRequest.model_validate_json(request_json))]


def _p(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def verdict(value: float) -> tuple[str, str]:
    """(st 함수 이름, 문구)."""
    if value < 0.94:
        return "error", "공정가치가 낮음 · 위험 대비 쿠폰이 부족한 조건"
    if value <= 0.995:
        return "info", "일반적인 발행 수준"
    return "success", "액면에 근접 · 투자자에게 유리한 조건"


def render_result(req: PricingRequest, r: PricingResult) -> None:
    ts = req.term_sheet
    is_ko = isinstance(ts, KnockoutTerms)
    fair = r.fair_participation if is_ko else r.fair_coupon_annual
    fair_name = "적정 참여율" if is_ko else "적정 연 쿠폰"
    current = ts.participation if is_ko else ts.coupon_annual

    st.subheader("평가 결과")
    c1, c2 = st.columns(2)
    if r.price is not None:
        c1.metric("공정가치 (액면 대비)", _p(r.price, 2), help=f"표준오차 ±{r.stderr * 100:.2f}%p")
        c1.caption(f"표준오차 ±{r.stderr * 100:.2f}%p · {r.n_paths:,}개 경로")
    else:
        c1.metric("공정가치 (액면 대비)", "—")
        c1.caption(("참여율" if is_ko else "쿠폰") + "이 비어 있어 공정가치 대신 적정값만 계산했습니다.")

    fair_text = "계산 불가" if fair is None else _p(fair, 2)
    delta = None
    if fair is not None and current is not None:
        delta = f"입력값 대비 {(current - fair) * 100:+.2f}%p"
    c2.metric(f"{fair_name} (목표 공정가치 {r.target * 100:g}%)", fair_text, delta=delta, delta_color="normal",
              help="공정가치가 목표값이 되는 값. 시뮬레이션 설정에서 목표를 바꿀 수 있습니다")
    if fair is not None and fair <= 0:
        c2.caption(f"{fair_name}이 0% 이하: 지급이 없어도 목표 공정가치 이상입니다.")
    elif fair is None:
        c2.caption("현실적인 범위에서 목표 공정가치에 도달하지 않습니다.")

    if r.price is not None:
        kind, text = verdict(r.price)
        getattr(st, kind)(text)

    if is_ko:
        _render_knockout(ts, r)
    else:
        _render_autocall(ts, r)


def _render_autocall(ts: AutocallTerms, r: PricingResult) -> None:
    cols = st.columns(4)
    cols[0].metric("조기상환 확률 합계", _p(r.early_redemption_prob))
    cols[1].metric("만기 원금손실 확률", _p(r.maturity_loss_prob))
    cols[2].metric("손실 시 평균 상환율", _p(r.avg_loss_redemption))
    cols[3].metric("기대 보유기간", f"{r.expected_life_months:.1f}개월")
    extra = [f"낙인 발생 후 만기까지 미상환 확률 {_p(r.ki_unredeemed_prob)}"] if ts.ki_mode != "none" else []
    if r.avg_monthly_coupons is not None:
        extra.append(f"월 쿠폰 평균 수령 횟수 {r.avg_monthly_coupons:.1f}회 (최대 {ts.maturity_months}회)")
    if r.avg_lizard_coupons is not None:
        extra.append(f"쿠폰 평균 수령 횟수 {r.avg_lizard_coupons:.1f}회 (리자드 상품은 회차마다 그 회차 쿠폰을 받음, 최대 {ts.n_obs}회)")
    if extra:
        st.caption(" · ".join(extra))

    coupon = ts.coupon_annual
    rows = []
    for k, (m, prob) in enumerate(zip(r.obs_months, r.redemption_probs)):
        last = k == len(r.obs_months) - 1
        label = "만기 상환" if last else f"{k + 1}회차"
        if ts.is_lizard:
            payoff = "—" if coupon is None else f"원금 + 해당 회차 쿠폰 {coupon * ts.obs_period_months / 12 * 100:.1f}%"
        elif ts.is_monthly or coupon is None:
            payoff = "원금" if ts.is_monthly else "—"
        else:
            payoff = f"+{coupon * ts.obs_period_months / 12 * (k + 1) * 100:.1f}%"
        rows.append({"구분": label, "평가 시점": f"{m}개월", "배리어": _p(ts.barriers[k], 0),
                     "상환 확률": prob, "상환 시 수익": payoff})
    rows.append({"구분": "만기 손실", "평가 시점": f"{ts.maturity_months}개월", "배리어": "—",
                 "상환 확률": r.maturity_loss_prob, "상환 시 수익": f"평균 {_p(r.avg_loss_redemption)} 상환"})
    df = pd.DataFrame(rows)

    st.markdown("**회차별 상환 확률**")
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("구분:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("상환 확률:Q", axis=alt.Axis(format="%"), title=None),
            color=alt.condition(alt.datum["구분"] == "만기 손실", alt.value("#d62728"), alt.value("#1f77b4")),
            tooltip=["구분", "평가 시점", alt.Tooltip("상환 확률:Q", format=".1%")],
        )
        .properties(height=260)
    )
    st.altair_chart(chart, width="stretch")
    show = df.assign(**{"상환 확률": df["상환 확률"].map(lambda x: f"{x * 100:.1f}%")})
    st.dataframe(show, hide_index=True, width="stretch")


def _render_knockout(ts: KnockoutTerms, r: PricingResult) -> None:
    cols = st.columns(4)
    cols[0].metric("녹아웃 확률", _p(r.ko_prob))
    cols[1].metric("녹아웃 시 수익률", _p(ts.rebate))
    cols[2].metric("미발생 시 평균 수익률", _p(r.avg_return_no_ko, 2))
    cols[3].metric("최소 수익만 받을 확률", _p(r.min_return_prob))
    if ts.max_loss != 0:  # 원금 손실이 가능한 상품
        c1, c2 = st.columns(4)[:2]
        c1.metric("만기 원금손실 확률", _p(r.maturity_loss_prob))
        c2.metric("손실 시 평균 상환율", _p(r.avg_loss_redemption))
    if ts.participation is None:
        st.caption("참여율이 비어 있어 적정 참여율 기준으로 지표를 계산했습니다.")


def render_sensitivity(req: PricingRequest) -> None:
    st.subheader("민감도 분석")
    is_ko = isinstance(req.term_sheet, KnockoutTerms)
    st.caption("같은 난수로 시장 가정만 바꿔 다시 평가합니다. 시나리오가 7개라 시간이 조금 걸립니다.")
    key = req.model_dump_json()
    if st.button("민감도 분석 실행"):
        with st.spinner("시나리오별로 평가하는 중..."):
            st.session_state["sensitivity"] = {"request": key, "rows": cached_sensitivity(key)}
    saved = st.session_state.get("sensitivity")
    if not saved or saved["request"] != key:
        return
    rows = saved["rows"]
    base = rows[0]
    table = []
    for row in rows:
        entry = {"시나리오": row["label"]}
        if row["skipped"]:
            entry["비고"] = row["skipped"]
        else:
            if base["price"] is not None:
                entry["공정가치"] = _p(row["price"], 2)
                entry["기준 대비"] = f"{(row['price'] - base['price']) * 100:+.2f}%p"
            entry["적정 참여율" if is_ko else "적정 연 쿠폰"] = _p(row["fair"], 2)
        table.append(entry)
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
