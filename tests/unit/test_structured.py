import pytest

from aidirector.ai.providers._http import extract_json_object
from aidirector.errors import ProviderError


def test_plain_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json():
    text = 'Here you go:\n```json\n{"concept": "旅", "tone": "calm"}\n```\nDone.'
    assert extract_json_object(text)["concept"] == "旅"


def test_json_with_prose():
    text = 'Sure! The plan is {"a": [1, 2], "b": {"c": 3}} — hope that helps.'
    assert extract_json_object(text) == {"a": [1, 2], "b": {"c": 3}}


def test_no_json_raises():
    with pytest.raises(ProviderError):
        extract_json_object("no json here at all")
