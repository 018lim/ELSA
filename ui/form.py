"""입력 폼: 상품 조건, 시장 데이터, 시뮬레이션 설정.

모든 위젯은 ui/state.py의 고정 key를 쓰고, 값은 session_state에서 읽는다(위젯에 value를 따로 주지 않음).
이미지로 채운 항목은 라벨 뒤 "(이미지)"와 help 툴팁의 근거 원문으로 표시한다.

- render_product_form(): 상품 유형 선택 + 기초자산 + 유형별 조건 폼
- render_market_form(): 종목별 티커·변동성·배당, 상관계수, 금리(국고채 3년 불러오기), 시세로 추정(개월 단위 기간),
  종가 붙여넣기
- render_sim_settings(): 경로 수, 시드, 적정 쿠폰 목표값 (접힌 expander)
- fetch_market_estimate(tickers, months, div_tickers): yfinance 추정 결과(변동성·상관·배당) 캐시 (하루)
- fetch_rate(day): ECOS 국고채 3년 금리 캐시 (날짜별). 인증키는 설정(ECOS_API_KEY)에서만 읽음
"""

from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from elsa import config, market, rates
from elsa.market import MarketDataUnavailable
from ui import state as S
from ui.state import autofill_help as hp
from ui.state import autofill_label as lb

KI_MODES = {"daily": "매일 관찰", "monthly": "매월 말일 관찰", "maturity": "만기일만 관찰", "none": "노낙인"}
KO_OBS = {"daily": "매일 관찰", "maturity": "만기일만 관찰"}
PRODUCTS = {"autocall": "자동조기상환형", "knockout": "녹아웃형"}


# ---------------------------------------------------------------------------
# 상품 조건
# ---------------------------------------------------------------------------


def render_product_form() -> None:
    st.subheader("상품 조건")
    if st.session_state.get(S.IS_EXAMPLE):
        st.caption("📌 예시 조건(삼성전자/SK하이닉스 스텝다운)이 채워져 있습니다. 직접 고치거나 이미지를 올려 바꾸세요.")

    st.radio(
        lb("상품 유형", S.PRODUCT), list(PRODUCTS), key=S.PRODUCT, horizontal=True,
        format_func=PRODUCTS.get, help=hp(S.PRODUCT),
    )
    _render_underlyings()
    if st.session_state[S.PRODUCT] == "autocall":
        _render_autocall()
    else:
        _render_knockout()


def _render_underlyings() -> None:
    st.markdown("**기초자산**")
    st.number_input("기초자산 개수", min_value=1, max_value=S.MAX_UL, step=1, key=S.N_UL)
    cols = st.columns(S.MAX_UL)
    for i in range(int(st.session_state[S.N_UL])):
        cols[i].text_input(
            lb(f"기초자산 {i + 1}", S.ul_name(i)), key=S.ul_name(i), help=hp(S.ul_name(i)),
            on_change=S.set_underlying_defaults, args=(i,), placeholder="예: 삼성전자",
        )


def _render_autocall() -> None:
    ss = st.session_state
    st.markdown("**조기상환 조건**")
    c1, c2 = st.columns(2)
    c1.number_input(lb("만기 (개월)", S.AC_MATURITY), min_value=1, max_value=120, step=1,
                    key=S.AC_MATURITY, help=hp(S.AC_MATURITY))
    c2.number_input(lb("평가 주기 (개월)", S.AC_PERIOD), min_value=1, max_value=36, step=1,
                    key=S.AC_PERIOD, help=hp(S.AC_PERIOD, "몇 개월마다 조기상환 여부를 판정하는지"))
    n_obs = ss[S.AC_MATURITY] // ss[S.AC_PERIOD] if ss[S.AC_PERIOD] else 0
    st.text_input(
        lb("상환 배리어 (%)", S.AC_BARRIERS), key=S.AC_BARRIERS,
        help=hp(S.AC_BARRIERS, "회차별 조기상환 배리어를 하이픈으로 이어 입력. 마지막 값은 만기 상환 배리어. "
                               "리자드는 90(L75)-90(L70)-85처럼 괄호로 함께 적을 수 있습니다"),
        placeholder="90-90-85-85-80-75",
    )
    st.caption(f"평가 {n_obs}회 → 배리어 {n_obs}개 필요 · 리자드는 90(L75)-90(L70)-85-80-75-70처럼 괄호로 적어도 됩니다")

    st.markdown("**쿠폰**")
    c1, c2, c3 = st.columns([2, 1, 2])
    c1.number_input(
        lb("연 쿠폰 (%)", S.AC_COUPON), value=None, min_value=0.0, max_value=100.0, step=0.5, format="%.2f",
        key=S.AC_COUPON, help=hp(S.AC_COUPON, "비워 두면 적정 쿠폰만 계산합니다"), placeholder="비우면 적정 쿠폰만 계산",
    )
    c2.checkbox(lb("월지급식", S.AC_MONTHLY), key=S.AC_MONTHLY, help=hp(
        S.AC_MONTHLY, "매월 워스트가 월지급 배리어 이상이면 그달 쿠폰(연 쿠폰/12)을 바로 지급"))
    if ss[S.AC_MONTHLY]:
        c3.number_input(lb("월지급 배리어 (%)", S.AC_MONTHLY_BARRIER), min_value=0.0, max_value=150.0,
                        step=5.0, key=S.AC_MONTHLY_BARRIER, help=hp(S.AC_MONTHLY_BARRIER))

    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    c1.checkbox(lb("리자드", S.AC_LIZARD), key=S.AC_LIZARD, help=hp(
        S.AC_LIZARD, "평가일에 상환 배리어에는 못 미쳐도 리자드 배리어 이상이면 그 회차 쿠폰을 지급하고 상품은 계속 유지. "
                     "상환될 때는 그 회차 쿠폰만 받음. 월지급식과 함께 쓸 수 없음"))
    if ss[S.AC_LIZARD]:
        c2.text_input(
            lb("회차별 리자드 배리어 (%)", S.AC_LIZARD_BARRIER), key=S.AC_LIZARD_BARRIER, placeholder="75-70",
            help=hp(S.AC_LIZARD_BARRIER, "1회차부터 순서대로 하이픈으로 입력. 90(L75)-90(L70)-85-80-75-70이면 '75-70'(뒤 회차는 리자드 없음). "
                                         "값 하나('75')는 전 회차에 적용, 중간에 없는 회차는 x (예: x-70)"),
        )

    st.markdown("**낙인 · 만기상환 조건**")
    c1, c2, c3 = st.columns(3)
    c1.selectbox(lb("낙인 방식", S.AC_KI_MODE), list(KI_MODES), format_func=KI_MODES.get,
                 key=S.AC_KI_MODE, help=hp(S.AC_KI_MODE))
    if ss[S.AC_KI_MODE] != "none":
        c2.number_input(lb("낙인 배리어 (%)", S.AC_KI_BARRIER), min_value=0.0, max_value=100.0, step=5.0,
                        key=S.AC_KI_BARRIER, help=hp(S.AC_KI_BARRIER))
        if not ss[S.AC_MONTHLY] and not ss[S.AC_LIZARD]:
            c3.checkbox(lb("더미 쿠폰", S.AC_DUMMY), key=S.AC_DUMMY, help=hp(
                S.AC_DUMMY, "낙인이 없었으면 만기 배리어 미달이어도 쿠폰을 지급"))

    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    c1.number_input(
        lb("최대 손실률 (%)", S.AC_MAX_LOSS), value=None, min_value=0.0, max_value=100.0, step=5.0, format="%.1f",
        key=S.AC_MAX_LOSS, placeholder="비우면 제한 없음",
        help=hp(S.AC_MAX_LOSS, "만기에 잃을 수 있는 최대 비율. 0%는 원금지급형(ELB), 10%는 원금 90% 보장. 일반 ELS는 비워 두세요"),
    )
    max_loss = ss.get(S.AC_MAX_LOSS)
    c2.caption(
        "제한 없음: 만기 손실 시 워스트 비율만큼 상환" if max_loss is None or max_loss >= 100
        else "원금지급형(ELB): 조건을 못 채워도 원금 100% 상환" if max_loss == 0
        else f"만기에 최소 원금의 {100 - max_loss:g}% 상환"
    )


def _render_knockout() -> None:
    ss = st.session_state
    st.markdown("**녹아웃 조건**")
    c1, c2, c3 = st.columns(3)
    c1.number_input(lb("만기 (개월)", S.KO_MATURITY), min_value=1, max_value=120, step=1,
                    key=S.KO_MATURITY, help=hp(S.KO_MATURITY))
    c2.number_input(lb("행사가 (%)", S.KO_STRIKE), min_value=1.0, max_value=200.0, step=5.0,
                    key=S.KO_STRIKE, help=hp(S.KO_STRIKE))
    c3.number_input(lb("참여율 (%)", S.KO_PART), value=None, min_value=0.0, max_value=1000.0, step=5.0,
                    key=S.KO_PART, help=hp(S.KO_PART, "비워 두면 적정 참여율만 계산합니다"),
                    placeholder="비우면 적정 참여율만 계산")

    c1, c2 = st.columns(2)
    c1.checkbox(lb("상승 녹아웃 사용", S.KO_UPPER_ON), key=S.KO_UPPER_ON)
    if ss[S.KO_UPPER_ON]:
        c1.number_input(lb("상승 녹아웃 배리어 (%)", S.KO_UPPER), min_value=100.0, max_value=300.0, step=1.0,
                        key=S.KO_UPPER, help=hp(S.KO_UPPER))
    c2.checkbox(lb("하락 녹아웃 사용", S.KO_LOWER_ON), key=S.KO_LOWER_ON)
    if ss[S.KO_LOWER_ON]:
        c2.number_input(lb("하락 녹아웃 배리어 (%)", S.KO_LOWER), min_value=1.0, max_value=100.0, step=1.0,
                        key=S.KO_LOWER, help=hp(S.KO_LOWER))

    c1, c2, c3 = st.columns(3)
    c1.selectbox(lb("녹아웃 관찰", S.KO_OBS), list(KO_OBS), format_func=KO_OBS.get, key=S.KO_OBS, help=hp(S.KO_OBS))
    c2.number_input(lb("녹아웃 시 수익률 (%)", S.KO_REBATE), min_value=0.0, max_value=100.0, step=0.5,
                    key=S.KO_REBATE, help=hp(S.KO_REBATE))
    c3.number_input(lb("최소 수익률 (%)", S.KO_MIN_RETURN), min_value=0.0, max_value=100.0, step=0.5,
                    key=S.KO_MIN_RETURN, help=hp(S.KO_MIN_RETURN, "녹아웃이 없을 때 보장되는 최소 수익률"))

    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    c1.number_input(
        lb("최대 손실률 (%)", S.KO_MAX_LOSS), value=None, min_value=0.0, max_value=100.0, step=5.0, format="%.1f",
        key=S.KO_MAX_LOSS, placeholder="비우면 제한 없음",
        help=hp(S.KO_MAX_LOSS, "녹아웃이 없고 기초자산이 불리하게 움직였을 때 잃을 수 있는 최대 비율. "
                               "0%는 원금지급형(ELB), 10%는 원금 90% 보장"),
    )
    max_loss = ss.get(S.KO_MAX_LOSS)
    c2.caption(
        "제한 없음: 불리하게 움직인 만큼 전부 손실" if max_loss is None or max_loss >= 100
        else "원금지급형(ELB): 녹아웃이 없고 불리하게 움직여도 원금 100% 상환" if max_loss == 0
        else f"불리하게 움직인 만큼 손실, 최대 {max_loss:g}%까지 (최소 원금의 {100 - max_loss:g}% 상환)"
    )

    kind = {(True, False): "상승형", (False, True): "하락형", (True, True): "양방향(투웨이)"}.get(
        (ss[S.KO_UPPER_ON], ss[S.KO_LOWER_ON]), "녹아웃 배리어를 하나 이상 켜 주세요"
    )
    adverse = {"상승형": "행사가보다 하락", "하락형": "행사가보다 상승", "양방향(투웨이)": "없음(어느 방향이든 수익)"}
    st.caption(f"구조: {kind} · 불리한 움직임: {adverse.get(kind, '-')} · 기초자산이 여러 개면 워스트 퍼포머 기준")


# ---------------------------------------------------------------------------
# 시장 데이터
# ---------------------------------------------------------------------------


@st.cache_data(ttl=86_400, show_spinner=False)
def fetch_market_estimate(tickers: tuple[str, ...], months: int, div_tickers: tuple[str | None, ...]) -> dict:
    """캐시에는 dict만 저장한다(코드가 다시 불러와져 클래스가 바뀌어도 캐시 저장·복원이 깨지지 않도록)."""
    return asdict(market.estimate_from_tickers(list(tickers), months, list(div_tickers)))


def _apply_estimate(vols: list[float], corr: list[list[float]], label: str) -> None:
    ss = st.session_state
    n = len(vols)
    for i in range(n):
        ss[S.vol_key(i)] = round(vols[i] * 100, 2)
        ss[S.MARKET_META][i] = label
        for j in range(i + 1, n):
            ss[S.corr_key(i, j)] = round(max(-0.99, min(0.99, corr[i][j])), 3)
    ss[S.MARKET_META]["corr"] = label


def _on_fetch(months: int) -> None:
    ss = st.session_state
    n = int(ss[S.N_UL])
    tickers = tuple((ss.get(S.ul_ticker(i)) or "").strip() for i in range(n))
    if not all(tickers):
        ss[S.MARKET_MSG] = ("warning", "티커가 비어 있는 종목이 있습니다. 티커를 입력한 뒤 다시 눌러 주세요.")
        return
    # 종목표에 대체 ETF(div_ticker)가 있는 자산은, 티커를 바꾸지 않았을 때만 그 ETF로 배당을 계산한다
    div_tickers = []
    for i in range(n):
        info = market.lookup(ss.get(S.ul_name(i)) or "")
        div_tickers.append(info.div_ticker if info and info.ticker == tickers[i] else None)
    try:
        est = market.MarketEstimate(**fetch_market_estimate(tickers, int(months), tuple(div_tickers)))
    except MarketDataUnavailable as e:
        ss[S.MARKET_MSG] = ("warning", f"시세를 가져오지 못했습니다 ({e}). 기본값을 쓰거나 직접 입력해 주세요.")
        return
    _apply_estimate(est.vols, est.corr, f"과거 {months}개월 기준")
    kept = []
    for i, d in enumerate(est.divs):
        if d is None:
            kept.append(ss.get(S.ul_name(i)) or tickers[i])
            continue
        ss[S.div_key(i)] = round(d * 100, 2)
        src = est.div_sources[i]
        ss[S.MARKET_META][S.div_meta(i)] = (
            "최근 1년 배당 기준" if src == tickers[i] else f"{src} ETF의 최근 1년 배당 기준"
        )
    msg = f"{est.start} ~ {est.end} 공통 거래일 {est.n_days}일의 종가로 변동성과 상관계수를 계산했습니다."
    msg += " 배당수익률은 최근 1년 실제 배당금 ÷ 현재 주가로 채웠습니다." if len(kept) < n else ""
    if kept:
        msg += f" ({', '.join(kept)}: 배당 정보가 없어 기존 값 유지)"
    ss[S.MARKET_MSG] = ("success", msg)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_rate(day: str) -> dict:
    """day는 캐시 구분용(하루 단위). 인증키를 캐시 key에 넣지 않으려고 함수 안에서 읽는다.
    캐시에는 dict만 저장한다(코드가 다시 불러와져도 저장·복원이 깨지지 않도록)."""
    return asdict(rates.fetch_ktb3y(config.get_setting("ECOS_API_KEY")))


def _on_fetch_rate() -> None:
    import datetime as dt

    ss = st.session_state
    try:
        q = rates.RateQuote(**fetch_rate(dt.date.today().isoformat()))
    except rates.RateUnavailable as e:
        ss[S.MARKET_MSG] = ("warning", str(e))
        return
    ss[S.RATE] = round(q.rate * 100, 3)
    ss[S.MARKET_META]["rate"] = f"{q.name} {q.as_of}"
    ss[S.MARKET_MSG] = ("success", f"한국은행 ECOS의 {q.name} 금리 {q.rate * 100:.3f}% ({q.as_of} 기준)를 넣었습니다.")


def _on_paste() -> None:
    ss = st.session_state
    n = int(ss[S.N_UL])
    try:
        closes = market.parse_pasted_closes(ss.get("paste_closes", ""), n)
        vols, corr = market.estimate_from_closes(closes)
    except MarketDataUnavailable as e:
        ss[S.MARKET_MSG] = ("warning", str(e))
        return
    _apply_estimate(vols, corr, "붙여넣은 종가 기준")
    ss[S.MARKET_MSG] = ("success", f"붙여넣은 종가 {len(closes)}일로 변동성과 상관계수를 계산했습니다.")


def render_market_form() -> None:
    ss = st.session_state
    n = int(ss[S.N_UL])
    meta = ss.setdefault(S.MARKET_META, {})
    st.subheader("시장 데이터")
    st.caption("상품 안내 자료에는 나오지 않는 값입니다. 기본값은 참고용 가정이며, 시세로 추정하면 과거 실현변동성과 "
               "최근 1년 실제 배당(지수는 추종 ETF 배당)을 씁니다.")

    for i in range(n):
        name = ss.get(S.ul_name(i)) or f"기초자산 {i + 1}"
        tag = f" ({meta[i]})" if i in meta else ""
        div_tag = f" ({meta[S.div_meta(i)]})" if S.div_meta(i) in meta else ""
        c0, c1, c2, c3 = st.columns([2, 2, 2, 2])
        c0.markdown(f"**{name}**")
        c1.text_input(lb("티커", S.ul_ticker(i)), key=S.ul_ticker(i), help=hp(S.ul_ticker(i), "Yahoo Finance 티커"),
                      placeholder="예: 005930.KS")
        c2.number_input(f"변동성 (%){tag}", value=None, min_value=0.0, max_value=200.0, step=1.0, key=S.vol_key(i),
                        placeholder="입력 필요")
        c3.number_input(f"배당수익률 (%){div_tag}", min_value=0.0, max_value=20.0, step=0.1, format="%.2f",
                        key=S.div_key(i))

    if n > 1:
        corr_tag = f" ({meta['corr']})" if "corr" in meta else ""
        st.markdown(f"**상관계수**{corr_tag}")
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        cols = st.columns(len(pairs))
        for col, (i, j) in zip(cols, pairs):
            a = ss.get(S.ul_name(i)) or f"자산{i + 1}"
            b = ss.get(S.ul_name(j)) or f"자산{j + 1}"
            col.number_input(f"{a} · {b}", min_value=-0.99, max_value=0.99, step=0.05, format="%.2f",
                             key=S.corr_key(i, j))

    rate_tag = f" ({meta['rate']})" if "rate" in meta else ""
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.number_input(f"무위험이자율 (%){rate_tag}", min_value=-5.0, max_value=20.0, step=0.1, format="%.3f",
                    key=S.RATE, help="원화로 정산되는 ELS이므로 원화 금리(국고채 등)를 씁니다")
    c2.button("🏦 국고채 3년 불러오기", on_click=_on_fetch_rate, width="stretch",
              help="한국은행 ECOS에서 가장 최근 국고채(3년) 금리를 가져옵니다")

    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    months = c1.number_input("변동성 추정 기간 (개월)", min_value=market.MIN_EST_MONTHS, max_value=market.MAX_EST_MONTHS,
                             step=1, key=S.EST_MONTHS, help="최근 몇 개월의 일별 종가로 변동성·상관계수를 계산할지")
    c2.button("📈 시세로 추정", on_click=_on_fetch, args=(months,), width="stretch",
              help="티커로 Yahoo Finance에서 일별 종가를 받아 과거 변동성·상관계수와 최근 1년 배당수익률을 계산합니다")

    msg = ss.pop(S.MARKET_MSG, None)
    if msg:
        getattr(st, msg[0])(msg[1])

    with st.expander("종가 데이터로 추정 (붙여넣기)"):
        st.caption(f"엑셀 등에서 날짜별 종가를 붙여넣으세요. 한 줄에 하루, 기초자산 {n}개의 종가를 순서대로 (날짜 칸은 있어도 됩니다).")
        st.text_area("종가", key="paste_closes", height=150, placeholder="2024-01-02\t78500\t140000\n2024-01-03\t77000\t138500")
        st.button("붙여넣은 종가로 추정", on_click=_on_paste)


def render_sim_settings() -> None:
    with st.expander("시뮬레이션 설정"):
        c1, c2, c3 = st.columns(3)
        c1.number_input("경로 수", min_value=10_000, max_value=100_000, step=10_000, key=S.SIM_PATHS)
        c2.number_input("시드", min_value=0, max_value=2**31 - 1, step=1, key=S.SIM_SEED)
        c3.number_input("적정 쿠폰 목표 공정가치 (%)", min_value=50.0, max_value=110.0, step=0.5, key=S.SIM_TARGET,
                        help="적정 쿠폰(참여율)을 구할 때 맞출 공정가치. 100%는 액면, 실제 발행 상품은 보통 94~99%")
