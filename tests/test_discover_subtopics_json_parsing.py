"""Discovery/extension-pass JSON parsing tolerates trailing model prose (2026-08-01 clinical_neurology fix)."""
import json

import cli.discover_subtopics as discover_subtopics


def test_raw_decode_ignores_trailing_prose_after_json():
    cleaned = discover_subtopics._strip_json_fences(
        '```json\n{"subtopics": [{"id": "s1"}]}\n```\nNote: coverage reached 76%.'
    )
    parsed, _ = json.JSONDecoder().raw_decode(cleaned)
    assert parsed == {"subtopics": [{"id": "s1"}]}


def test_raw_decode_still_raises_on_genuinely_malformed_json():
    cleaned = discover_subtopics._strip_json_fences('{"subtopics": [{"id": "s1"]}')
    try:
        json.JSONDecoder().raw_decode(cleaned)
        assert False, "expected JSONDecodeError"
    except json.JSONDecodeError:
        pass
