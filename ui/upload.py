"""이미지 자동 입력 화면: 업로드, 샘플 체험, 접근 코드·횟수 제한, 추출 결과 안내.

버튼을 누르면 콜백이 작업(EXTRACT_JOB)만 등록하고, 다음 실행에서 업로드·샘플 버튼 대신 '읽는 중' 안내만
그린 채 추출한다(판독 중 다른 버튼을 눌러 작업이 끊기지 않도록). 추출이 끝나면 결과를 PENDING_FILL에 넣고
rerun한다(폼 반영은 state.apply_pending_fill). 자동 입력은 폼을 채우기만 하고 평가는 하지 않는다.
업로드 파일은 디스크에 저장하지 않는다.

- SAMPLES: 체험용 가상 샘플 이미지 목록
- render_upload(): 업로드 영역, 조건 읽기·샘플 체험 버튼, 개인정보 안내 (작업 중이면 안내만)
- render_notice(): 직전 자동 입력 결과 요약 (읽은 항목 수, 확인 필요·못 읽은 항목, 경고)
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from elsa import config
from elsa.extraction.base import ExtractionError, NotConfigured, QuotaExceeded, UploadRejected
from elsa.extraction.factory import get_extractor
from elsa.schemas import ExtractionResult
from ui import state as S

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLES = {
    "자동조기상환형 (예시증권 제1호)": "sample_termsheet.png",
    "녹아웃형 (예시증권 제2호)": "sample_knockout.png",
}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "pdf": "application/pdf"}

ERROR_TEXT = {
    QuotaExceeded: "오늘 자동 입력 한도를 모두 사용했습니다. 조건을 직접 입력해 주세요.",
    NotConfigured: "이미지 자동 입력이 설정되지 않았습니다. 조건을 직접 입력해 주세요.",
}
FAILED_TEXT = "이미지에서 조건을 읽지 못했습니다. 더 선명한 이미지를 올리거나 직접 입력해 주세요."

FIELD_NAMES = {
    "underlyings": "기초자산", "maturity_months": "만기", "obs_period_months": "평가 주기",
    "barriers": "상환 배리어", "coupon_annual": "쿠폰", "ki_mode": "낙인 방식", "ki_barrier": "낙인 배리어",
    "dummy_coupon": "더미 쿠폰", "monthly_coupon_barrier": "월지급 배리어", "lizard_barriers": "리자드 배리어",
    "max_loss": "최대 손실률",
    "strike": "행사가",
    "participation": "참여율", "ko_upper": "상승 녹아웃 배리어", "ko_lower": "하락 녹아웃 배리어",
    "rebate": "녹아웃 시 수익률", "ko_observation": "녹아웃 관찰 방식", "min_return": "최소 수익률",
}
REQUIRED = {
    # 최대 손실률이 없으면 원금 보장을 가정하지 않고 비워 두므로(= 제한 없음), 확인하도록 못 읽은 항목에 표시
    "autocall": ["underlyings", "maturity_months", "obs_period_months", "barriers", "coupon_annual", "ki_mode",
                 "max_loss"],
    "knockout": ["underlyings", "maturity_months", "strike", "participation", "rebate", "max_loss"],
}


@st.cache_data(show_spinner=False, max_entries=4)
def _extract_sample(filename: str, extractor_name: str) -> dict:
    """샘플은 모든 접속자에게 같으므로 결과를 서버에 캐시해 API 사용량을 아낀다."""
    data = (SAMPLE_DIR / filename).read_bytes()
    return get_extractor().extract(data, "image/png").model_dump()


def _start_upload_job() -> None:
    """'조건 읽기' 콜백: 업로드 파일을 작업으로 등록한다(메모리에만 보관, 디스크에 저장하지 않음)."""
    ss = st.session_state
    f = ss.get("upload_file")
    if f is None:
        return
    ext = f.name.rsplit(".", 1)[-1].lower()
    ss[S.EXTRACT_JOB] = {"kind": "upload", "data": f.getvalue(), "mime": MIME.get(ext, f.type or ""), "name": f.name}


def _start_sample_job() -> None:
    """'샘플 이미지로 체험' 콜백."""
    ss = st.session_state
    ss[S.EXTRACT_JOB] = {"kind": "sample", "file": SAMPLES[ss["sample_choice"]], "name": ss["sample_choice"]}


def _run_job(job: dict) -> None:
    """등록된 작업을 실행한다. 이 실행에서는 업로드·샘플 버튼을 그리지 않으므로 판독 중에 누를 수 없다."""
    ss = st.session_state
    st.info(f"**{job['name']}** 에서 조건을 읽는 중입니다. 끝날 때까지 기다려 주세요.")
    try:
        with st.spinner("AI가 이미지를 판독하고 있습니다... (보통 10~30초, 서비스 상태에 따라 더 걸릴 수 있습니다)"):
            if job["kind"] == "sample":
                result = _extract_sample(job["file"], config.extractor_name() + config.extractor_model())
            else:
                result = get_extractor().extract(job["data"], job["mime"]).model_dump()
                ss[S.EXTRACTION_COUNT] = ss.get(S.EXTRACTION_COUNT, 0) + 1
    except UploadRejected as e:
        ss["extract_error"] = str(e)
    except ExtractionError as e:
        ss["extract_error"] = ERROR_TEXT.get(type(e), FAILED_TEXT)
    else:
        ss[S.PENDING_FILL] = result
    st.rerun()


def render_upload() -> None:
    ss = st.session_state
    st.subheader("① 이미지로 자동 입력")
    st.caption("상품 안내 이미지(간이투자설명서, 상품 요약표, 증권사 앱 캡처)를 올리면 조건을 읽어 아래 폼에 채웁니다. 채운 뒤 값을 확인하고 평가하세요.")

    job = ss.pop(S.EXTRACT_JOB, None)
    if job:
        _run_job(job)
        return

    code = config.access_code()
    locked = False
    if code:
        entered = st.text_input("접근 코드", type="password", key="access_code_input",
                                help="이미지 자동 입력에만 필요합니다. 직접 입력·평가는 코드 없이 쓸 수 있습니다.")
        locked = entered != code
    limit = config.max_extractions_per_session()
    used = ss.get(S.EXTRACTION_COUNT, 0)
    exhausted = used >= limit

    uploaded = st.file_uploader(
        "상품 안내 이미지 또는 PDF (3페이지 이하, 10MB 이하)",
        type=list(MIME), key="upload_file", disabled=locked or exhausted,
    )
    st.button("조건 읽기", type="primary", disabled=uploaded is None or locked or exhausted,
              width="stretch", on_click=_start_upload_job)
    st.caption("업로드한 이미지는 외부 AI 서비스로 전송됩니다. 계좌번호 등 개인정보가 담긴 화면은 올리지 마세요."
               f" · 이번 접속에서 {used}/{limit}회 사용")

    st.markdown("**상품 안내 이미지가 없다면 가상 샘플로 체험해 보세요**")
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    sample_label = c1.selectbox("체험할 샘플", list(SAMPLES), key="sample_choice",
                                help="실존하지 않는 가상 상품의 안내 이미지입니다")
    c2.button("샘플 이미지로 체험", disabled=locked, width="stretch", on_click=_start_sample_job)
    with st.expander("샘플 이미지 보기"):
        st.image(str(SAMPLE_DIR / SAMPLES[sample_label]), caption="가상의 상품입니다 (실존 증권사·상품 아님)")
    if code and locked and ss.get("access_code_input"):
        st.warning("접근 코드가 맞지 않습니다.")
    if exhausted:
        st.info("이번 접속의 자동 입력 횟수를 모두 사용했습니다. 조건을 직접 입력하거나 샘플로 체험해 주세요.")

    error = ss.pop("extract_error", None)
    if error:
        st.warning(error)


def render_notice() -> None:
    raw = st.session_state.get(S.EXTRACTION_NOTICE)
    if not raw:
        return
    res = ExtractionResult.model_validate(raw)
    f = res.fields
    if not f:
        st.warning(FAILED_TEXT)
        return
    st.success(f"이미지에서 {len(f)}개 항목을 읽었습니다. 값을 확인한 뒤 평가하기를 누르세요.")

    low = [FIELD_NAMES.get(k, k) for k, v in res.field_confidence.items() if v == "low" and k in f]
    if res.ticker_suggestions:
        low.append("티커(AI 제안: " + ", ".join(f"{n} {t}" for n, t in res.ticker_suggestions.items()) + ")")
    product = res.product_type or st.session_state[S.PRODUCT]
    required = list(REQUIRED.get(product, []))
    missing = [FIELD_NAMES[k] for k in required if k not in f]
    if product == "knockout" and "ko_upper" not in f and "ko_lower" not in f:
        missing.append("녹아웃 배리어")

    lines = []
    if low:
        lines.append("**확인이 필요한 항목:** " + ", ".join(low))
    if missing:
        lines.append("**이미지에서 찾지 못한 항목:** " + ", ".join(missing))
    if lines:
        st.warning("\n\n".join(lines))
    coupon_key = "participation" if product == "knockout" else "coupon_annual"
    if coupon_key not in f:
        st.info("이미지에 쿠폰이 없습니다. 비워 두면 적정 쿠폰을 계산합니다." if product == "autocall"
                else "이미지에 참여율이 없습니다. 비워 두면 적정 참여율을 계산합니다.")
    for w in res.warnings:
        st.warning(w)
    if res.unsupported_features:
        st.warning("지원하지 않는 구조가 포함되어 있습니다: " + ", ".join(res.unsupported_features)
                   + ". 평가 결과가 실제 상품과 다를 수 있습니다.")
