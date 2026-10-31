"""엔진 검증 탭: 유럽형 콜옵션의 MC 가격을 Black-Scholes 해석해와 비교해 보여 준다.

계산은 elsa/pricing/verification.py가 하고 여기서는 입력·표시만 한다. 화면 값은 %(20)이고
소수로 바꾸는 곳은 _read_inputs() 한 곳이다. st.tabs는 보이지 않는 탭도 매 실행 그리므로
"검증 실행"을 눌렀을 때만 계산하고, 결과는 입력값 기준으로 캐시한다.

- cached_report(params_json, n_paths, seed): 검증 결과 캐시 (@st.cache_data, dict 반환)
- render_verification(): 탭 전체 표시
"""

from __future__ import annotations

import json
from dataclasses import asdict

import altair as alt
import pandas as pd
import streamlit as st

from elsa.pricing.verification import (
    CONVERGENCE_NS,
    Z_95,
    CallParams,
    moneyness,
    months_from_years,
    run_verification,
)
from ui import state as S

MIN_N, MAX_N = 1_000, 500_000
TABLE_ROW_PX = 35  # st.dataframe 행 높이. 내부 스크롤 없이 15행을 모두 보여 주려고 씀
PASS_TEXT = {True: "통과 ✅", False: "확인 필요 ⚠️", None: "판정 불가"}


@st.cache_data(show_spinner=False, max_entries=32)
def cached_report(params_json: str, n_paths: int, seed: int) -> dict:
    return asdict(run_verification(CallParams(**json.loads(params_json)), n_paths, seed))


def _inputs() -> None:
    c1, c2, c3 = st.columns(3)
    c1.number_input("현재가 S0", min_value=0.01, max_value=1_000_000.0, step=10.0, key=S.VF_S0)
    c2.number_input("행사가 K", min_value=0.01, max_value=1_000_000.0, step=10.0, key=S.VF_STRIKE)
    c3.number_input("변동성 σ (%, 연율)", min_value=1.0, max_value=150.0, step=1.0, key=S.VF_VOL)
    c1, c2, c3 = st.columns(3)
    c1.number_input("무위험이자율 r (%, 연속복리)", min_value=-5.0, max_value=20.0, step=0.1, format="%.2f", key=S.VF_RATE)
    c2.number_input("배당수익률 q (%, 연속)", min_value=0.0, max_value=20.0, step=0.1, format="%.2f", key=S.VF_DIV)
    c3.number_input("만기 T (년)", min_value=0.1, max_value=5.0, step=0.25, format="%.2f", key=S.VF_YEARS,
                    help="엔진이 월 단위 격자를 쓰므로 가장 가까운 개월로 맞춰 MC와 BS에 똑같이 적용합니다")
    c1, c2 = st.columns(2)
    c1.number_input("시뮬레이션 횟수 N", min_value=MIN_N, max_value=MAX_N, step=10_000, key=S.VF_PATHS)
    c2.number_input("시드", min_value=0, max_value=2**31 - 1, step=1, key=S.VF_SEED)


def _read_inputs() -> tuple[str, int, int]:
    """화면 값(%)을 소수로 바꿔 (조건 JSON, N, 시드)로 돌려준다."""
    ss = st.session_state
    params = CallParams(
        s0=ss[S.VF_S0],
        strike=ss[S.VF_STRIKE],
        vol=ss[S.VF_VOL] / 100.0,
        rate=ss[S.VF_RATE] / 100.0,
        div=ss[S.VF_DIV] / 100.0,
        months=months_from_years(ss[S.VF_YEARS]),
    )
    return json.dumps(asdict(params), sort_keys=True), ss[S.VF_PATHS], ss[S.VF_SEED]


def _rel(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.3f}%"


def _render_main(check: dict) -> None:
    st.subheader("① 정답 비교")
    if check["passed"] is None:
        st.info("모든 경로의 페이오프가 같아 표준오차가 0이므로 신뢰구간으로 판정할 수 없습니다. 행사가나 변동성을 바꿔 주세요.")
    elif check["passed"]:
        st.success("BS 가격이 95% 신뢰구간 안에 있습니다. 통과 ✅")
    else:
        st.warning("BS 가격이 95% 신뢰구간 밖에 있습니다. 확인 필요 ⚠️")
    rows = [
        ("MC 가격", f"{check['mc']:.4f}"),
        ("BS 가격 (정답)", f"{check['bs']:.4f}"),
        ("차이 (MC − BS)", f"{check['diff']:+.4f}"),
        ("상대오차", _rel(check["rel_error"])),
        ("MC 표준오차 (쌍 평균 기준)", f"{check['stderr']:.4f}"),
        ("95% 신뢰구간", f"{check['ci_low']:.4f} ~ {check['ci_high']:.4f}"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["항목", "값"]), hide_index=True, width="stretch")


def _render_convergence(rows: list[dict], bs: float) -> None:
    st.subheader("② 수렴 그래프")
    st.markdown(f"점과 세로선은 MC 가격과 95% 신뢰구간, 점선은 **BS 정답 {bs:.4f}** 입니다.")
    df = pd.DataFrame(rows)[["n_paths", "mc", "stderr", "ci_low", "ci_high"]]
    x = alt.X("n_paths:Q", title="시뮬레이션 횟수 N (로그 스케일)",
              scale=alt.Scale(type="log", domain=[700, 700_000]),
              axis=alt.Axis(values=list(CONVERGENCE_NS), format=",d", labelAngle=0, grid=False))
    tooltip = [alt.Tooltip("n_paths:Q", title="N", format=",d"), alt.Tooltip("mc:Q", title="MC 가격", format=".4f"),
               alt.Tooltip("ci_low:Q", title="95% 하한", format=".4f"),
               alt.Tooltip("ci_high:Q", title="95% 상한", format=".4f")]
    bars = alt.Chart(df).mark_rule(color="#1f77b4", strokeWidth=2).encode(
        x=x, y=alt.Y("ci_low:Q", title="MC 가격", scale=alt.Scale(zero=False)), y2="ci_high:Q", tooltip=tooltip,
    )
    points = alt.Chart(df).mark_circle(color="#1f77b4", size=90, opacity=1).encode(x=x, y="mc:Q", tooltip=tooltip)
    answer = alt.Chart(pd.DataFrame({"bs": [bs]})).mark_rule(color="#888888", strokeDash=[6, 4], strokeWidth=2).encode(
        y="bs:Q", tooltip=[alt.Tooltip("bs:Q", title="BS 정답", format=".4f")],
    )
    st.altair_chart(answer + bars + points, width="stretch")
    st.caption("N이 늘수록 MC 가격이 BS 정답으로 수렴하고, 오차는 1/√N 속도로 줄어든다.")
    table = pd.DataFrame({
        "N": df["n_paths"].map("{:,}".format),
        "MC 가격": df["mc"].map("{:.4f}".format),
        "표준오차": df["stderr"].map("{:.4f}".format),
        "95% 신뢰구간": [f"{lo:.4f} ~ {hi:.4f}" for lo, hi in zip(df["ci_low"], df["ci_high"])],
    })
    st.dataframe(table, hide_index=True, width="stretch")


def _render_grid(rows: list[dict], s0: float) -> None:
    st.subheader("③ 여러 조건 일괄 검증")
    passed = sum(r["passed"] is True for r in rows)
    st.markdown(f"**{len(rows)}개 조건 중 {passed}개 통과**")
    table = pd.DataFrame([{
        "K": f"{r['strike']:g}",
        "구분": moneyness(s0, r["strike"]),
        "σ": f"{r['vol'] * 100:g}%",
        "MC 가격": f"{r['mc']:.4f}",
        "BS 가격": f"{r['bs']:.4f}",
        "상대오차": _rel(r["rel_error"]),
        "판정": PASS_TEXT[r["passed"]],
    } for r in rows])
    st.dataframe(table, hide_index=True, width="stretch", height=(len(table) + 1) * TABLE_ROW_PX + 3)
    st.caption(
        "행사가는 현재가의 80~120%입니다. 콜옵션은 행사가가 현재가보다 낮으면 내가격, 높으면 외가격입니다. "
        "모든 행이 같은 시드의 난수를 공유해 오차가 한쪽으로 쏠릴 수 있습니다. "
        "95% 기준이라 엔진이 정확해도 가끔 ⚠️가 나올 수 있고, 외가격일수록 가격이 작아 상대오차가 커 보입니다."
    )


def _render_antithetic(check: dict) -> None:
    st.subheader("④ 대조변수 적용 효과")
    independent, paired = check["stderr_independent"], check["stderr"]
    c1, c2, c3 = st.columns(3)
    c1.metric("독립 경로 기준 표준오차", f"{independent:.4f}")
    c2.metric("쌍 평균 기준 표준오차", f"{paired:.4f}")
    if paired > 0:
        ratio = independent / paired
        c3.metric("표준오차 감소 배율", f"{ratio:.2f}배", help="독립 경로 기준 ÷ 쌍 평균 기준")
        st.caption(
            f"①과 같은 조건, 같은 {check['n_paths']:,}개 경로의 값입니다. 독립 경로 기준은 경로별 표준편차 ÷ √N으로, "
            "대조변수 없이 독립 경로를 썼을 때의 표준오차 추정치입니다. 쌍 평균 기준은 쌍 평균의 표준편차 ÷ √쌍 수로, "
            f"대조변수를 쓰는 이 엔진의 실제 표준오차입니다. 같은 정밀도를 독립 경로로 내려면 경로가 약 {ratio**2:.1f}배 필요합니다."
        )
    else:
        c3.metric("표준오차 감소 배율", "—")


def render_verification() -> None:
    st.markdown(
        "**엔진이 정확한지 정답을 아는 문제로 확인합니다.** ELS 평가와 같은 경로 생성·할인 코드에 페이오프만 "
        "유럽형 콜옵션 max(S_T − K, 0)로 바꿔 계산하고, Black-Scholes 해석해와 비교합니다(단일 기초자산)."
    )
    st.caption(
        "확인되는 것: 위험중립 드리프트·변동성·배당이 반영된 만기 가격 분포, 할인, 대조변수 통계. "
        "확인되지 않는 것: 낙인·조기상환 판정, 월중 최저·최고, 상관계수·워스트 퍼포머 계산."
    )
    _inputs()
    key = _read_inputs()
    if st.button("검증 실행", type="primary", width="stretch"):
        with st.spinner("시뮬레이션 중입니다... (수렴 그래프의 500,000회 포함)"):
            st.session_state[S.VF_RESULT] = {"key": key, "report": cached_report(*key)}

    saved = st.session_state.get(S.VF_RESULT)
    if not saved:
        return
    if saved["key"] != key:
        st.caption("⚠ 입력값이 바뀌었습니다. 검증 실행을 다시 눌러 주세요.")
        return
    report = saved["report"]
    p = report["params"]
    st.caption(
        f"시드 {report['seed']} 고정 · 같은 입력이면 같은 결과 · 적용 만기 {p['months']}개월 · "
        f"신뢰구간 MC ± {Z_95} × 표준오차 · 표준오차는 대조변수 쌍 평균 기준"
    )
    _render_main(report["main"])
    _render_convergence(report["convergence"], report["main"]["bs"])
    _render_grid(report["grid"], p["s0"])
    _render_antithetic(report["main"])
