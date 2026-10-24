"""Claude 추출기 (anthropic SDK, 구조화 출력).

현재 Claude 모델은 tool 강제 호출(tool_choice any/tool)을 지원하지 않으므로,
output_config.format(json_schema)으로 공통 스키마에 맞는 JSON을 받는다.
SDK가 429·5xx·과부하를 자동 재시도(max_retries=2)하므로 직접 재시도하지 않는다.

- ClaudeExtractor(api_key, model): extract(file_bytes, mime_type) -> ExtractionResult
  - RateLimitError → QuotaExceeded, AuthenticationError·PermissionDeniedError → NotConfigured,
    그 밖의 API 오류·거절(refusal)·파싱 실패 → ExtractionFailed
"""

from __future__ import annotations

import base64

from elsa.extraction.base import (
    MAX_OUTPUT_TOKENS,
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

TIMEOUT_S = 90.0


class ClaudeExtractor(Extractor):
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key or not model:
            raise NotConfigured("ANTHROPIC_API_KEY 또는 EXTRACTOR_MODEL이 비어 있습니다.")
        self.api_key = api_key
        self.model = model
        self.name = f"claude:{model}"

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        check_upload(file_bytes, mime_type)
        import anthropic

        data = base64.standard_b64encode(file_bytes).decode("ascii")
        block_type = "document" if mime_type == "application/pdf" else "image"
        file_block = {"type": block_type, "source": {"type": "base64", "media_type": mime_type, "data": data}}

        client = anthropic.Anthropic(api_key=self.api_key, timeout=TIMEOUT_S, max_retries=2)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": [file_block, {"type": "text", "text": USER_PROMPT}]}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
            )
        except anthropic.RateLimitError as e:
            raise QuotaExceeded(str(e)) from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise NotConfigured(str(e)) from e
        except anthropic.APIStatusError as e:
            raise ExtractionFailed(f"{e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ExtractionFailed(f"연결 실패: {e}") from e

        if response.stop_reason == "refusal":
            raise ExtractionFailed("모델이 요청을 거절했습니다.")
        if response.stop_reason == "max_tokens":
            raise ExtractionFailed("응답이 최대 길이에서 잘렸습니다.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        return normalize_extraction(parse_json_response(text), extractor=self.name)
