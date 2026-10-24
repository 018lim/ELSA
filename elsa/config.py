"""설정값 읽기: st.secrets → 환경변수 순으로 조회.

elsa 패키지는 streamlit 없이도 동작해야 하므로 streamlit import는 함수 안에서만 하고,
st.secrets가 없거나(테스트·스크립트) 파일이 없으면 조용히 환경변수로 넘어간다.

- get_setting(name, default): 설정값 하나를 문자열로
- get_int(name, default): 정수 설정값
- extractor_name() / extractor_model(): 사용할 추출기와 모델 이름
- access_code(): 접근 코드 (비어 있으면 None)
- max_extractions_per_session(): 세션당 자동 입력 최대 횟수
"""

from __future__ import annotations

import os


def get_setting(name: str, default: str = "") -> str:
    try:
        import streamlit as st

        if name in st.secrets:
            value = st.secrets[name]
            return "" if value is None else str(value).strip()
    except Exception:  # streamlit 미설치, secrets.toml 없음 등
        pass
    return os.environ.get(name, default).strip()


def get_int(name: str, default: int) -> int:
    try:
        return int(get_setting(name, str(default)) or default)
    except ValueError:
        return default


def extractor_name() -> str:
    return (get_setting("EXTRACTOR", "gemini") or "gemini").lower()


def extractor_model() -> str:
    return get_setting("EXTRACTOR_MODEL")


def access_code() -> str | None:
    return get_setting("ACCESS_CODE") or None


def max_extractions_per_session() -> int:
    return get_int("MAX_EXTRACTIONS_PER_SESSION", 5)
