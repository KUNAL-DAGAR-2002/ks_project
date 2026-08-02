import asyncio
from copy import deepcopy
from unittest.mock import patch

from app.config import settings
from app.gemini_provider import GeminiProvider


def test_parser_accepts_fenced_json_with_trailing_grounding_text():
    result = GeminiProvider._parse_json(
        '```json\n{"headline":"Restock milk","actions":[]}\n```\nSources: example'
    )
    assert result["headline"] == "Restock milk"


def test_parser_accepts_text_before_json():
    result = GeminiProvider._parse_json(
        'Here is the structured response:\n{"summary":"Weekly analysis","actions":[]}'
    )
    assert result["summary"] == "Weekly analysis"


def test_generate_retries_without_thinking_config_when_model_rejects_it():
    calls = []

    class Response:
        def __init__(self, status_code): self.status_code = status_code
        def raise_for_status(self): return None
        def json(self):
            return {"candidates":[{"content":{"parts":[{"text":"{\"ok\":true}"}]}}]}

    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, **kwargs):
            calls.append((url, deepcopy(kwargs["json"])))
            return Response(400 if len(calls) == 1 else 200)

    with patch.object(settings, "gemini_api_key", "test-key"), patch.object(settings, "gemini_model", "gemini-3.5-flash"), patch("app.gemini_provider.httpx.AsyncClient", Client):
        result, _ = asyncio.run(GeminiProvider()._generate("Return JSON"))

    assert result == {"ok": True}
    assert calls[0][1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert "thinkingConfig" not in calls[1][1]["generationConfig"]
