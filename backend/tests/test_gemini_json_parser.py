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
