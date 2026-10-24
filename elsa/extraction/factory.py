"""설정값(EXTRACTOR, EXTRACTOR_MODEL, API 키)으로 추출기 구현체를 만든다.

모델 이름은 코드에 두지 않고 설정에서만 읽는다.

- get_extractor(): 설정에 맞는 Extractor. 설정이 없으면 NotConfigured
- describe_extractor(): 화면 하단 표시용 "gemini:<모델명>" (설정 없으면 "미설정")
"""

from __future__ import annotations

from elsa import config
from elsa.extraction.base import Extractor, NotConfigured


def get_extractor() -> Extractor:
    name = config.extractor_name()
    model = config.extractor_model()
    if name == "gemini":
        from elsa.extraction.gemini import GeminiExtractor

        return GeminiExtractor(config.get_setting("GEMINI_API_KEY"), model)
    if name == "claude":
        from elsa.extraction.claude import ClaudeExtractor

        return ClaudeExtractor(config.get_setting("ANTHROPIC_API_KEY"), model)
    raise NotConfigured(f"알 수 없는 EXTRACTOR 값: {name}")


def describe_extractor() -> str:
    try:
        return get_extractor().name
    except NotConfigured:
        return "미설정"
