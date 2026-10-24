"""Gemini 추출기 (google-genai SDK, 구조화 출력).

이미지·PDF와 공통 프롬프트를 보내고 response_json_schema로 공통 스키마에 맞는 JSON을 받는다.
일시적 과부하(503)는 짧게 기다린 뒤 최대 2회 재시도한다.

- GeminiExtractor(api_key, model): extract(file_bytes, mime_type) -> ExtractionResult
  - 429 → QuotaExceeded, 잘못된 키(400 API_KEY_INVALID·401·403) → NotConfigured,
    그 밖의 오류·파싱 실패 → ExtractionFailed
"""

from __future__ import annotations

import time

from elsa.extraction.base import (
    MAX_OUTPUT_TOKENS,
    RETRY_DELAYS,
    ExtractionFailed,
    Extractor,
    NotConfigured,
    QuotaExceeded,
    check_upload,
    parse_json_response,
)
from elsa.extraction.normalize import normalize_extraction
from elsa.extraction.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, USER_PROMPT
from elsa.schemas import ExtractionResult

TIMEOUT_MS = 180_000  # Flash-Lite는 보통 10초 안팎이지만, 느린 모델은 80~250초까지 걸린 적이 있어 넉넉히 둔다


class GeminiExtractor(Extractor):
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key or not model:
            raise NotConfigured("GEMINI_API_KEY 또는 EXTRACTOR_MODEL이 비어 있습니다.")
        self.api_key = api_key
        self.model = model
        self.name = f"gemini:{model}"

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        check_upload(file_bytes, mime_type)
        from google import genai
        from google.genai import errors, types

        client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0,
        )
        contents = [types.Part.from_bytes(data=file_bytes, mime_type=mime_type), USER_PROMPT]

        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = client.models.generate_content(model=self.model, contents=contents, config=config)
                break
            except errors.APIError as e:
                code = getattr(e, "code", None)
                message = str(e)
                if code == 429:
                    raise QuotaExceeded(message) from e
                if code in (401, 403) or "API_KEY_INVALID" in message:
                    raise NotConfigured(message) from e
                if code in (500, 502, 503, 504) and attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise ExtractionFailed(message) from e
            except Exception as e:  # 네트워크·타임아웃 등
                raise ExtractionFailed(f"{type(e).__name__}: {e}") from e

        return normalize_extraction(parse_json_response(response.text), extractor=self.name)
