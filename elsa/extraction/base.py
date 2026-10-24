"""추출기 공통 인터페이스, 오류 타입, 업로드 파일 검사.

구현체(Gemini/Claude)는 Extractor.extract()만 구현하면 된다. API별 오류는 아래 공통 오류로 바꿔 던지고,
화면은 공통 오류 종류만 보고 안내 문구를 고른다.

- Extractor: extract(file_bytes, mime_type) -> ExtractionResult 인터페이스
- ExtractionError: 공통 오류의 부모
  - QuotaExceeded: 사용 한도 초과 (429)
  - NotConfigured: API 키·모델 설정 없음 또는 잘못된 키
  - ExtractionFailed: 응답 파싱 실패, 타임아웃, 과부하 재시도 실패 등
  - UploadRejected: 파일 형식·크기·페이지 수 문제 (사용자에게 보여 줄 문구를 담음)
- check_upload(file_bytes, mime_type): 업로드 제한 검사
- parse_json_response(text): 모델 응답 문자열 → dict
"""

from __future__ import annotations

import io
import json
from abc import ABC, abstractmethod

from elsa.schemas import ExtractionResult

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 3
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
MAX_OUTPUT_TOKENS = 4096
RETRY_DELAYS = (2.0, 5.0)  # 일시적 서버 오류(5xx) 재시도 간격 (초), 최대 2회. Gemini에서 사용


class ExtractionError(Exception):
    """추출 공통 오류."""


class QuotaExceeded(ExtractionError):
    pass


class NotConfigured(ExtractionError):
    pass


class ExtractionFailed(ExtractionError):
    pass


class UploadRejected(ExtractionError):
    pass


class Extractor(ABC):
    name: str = ""

    @abstractmethod
    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult: ...


def check_upload(file_bytes: bytes, mime_type: str) -> None:
    if mime_type not in ALLOWED_MIME:
        raise UploadRejected("PNG, JPG, WEBP 이미지나 PDF만 올릴 수 있습니다.")
    if len(file_bytes) > MAX_BYTES:
        raise UploadRejected("파일이 10MB를 넘습니다. 더 작은 파일을 올려 주세요.")
    if mime_type == "application/pdf":
        try:
            from pypdf import PdfReader

            pages = len(PdfReader(io.BytesIO(file_bytes)).pages)
        except Exception as e:
            raise UploadRejected("PDF를 읽을 수 없습니다. 이미지로 캡처해 올려 주세요.") from e
        if pages > MAX_PDF_PAGES:
            raise UploadRejected(f"PDF는 {MAX_PDF_PAGES}페이지까지만 올릴 수 있습니다. 지금은 {pages}페이지입니다.")


def parse_json_response(text: str | None) -> dict:
    if not text:
        raise ExtractionFailed("빈 응답")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionFailed("응답 JSON 파싱 실패") from e
    if not isinstance(data, dict):
        raise ExtractionFailed("응답이 JSON 객체가 아님")
    return data
