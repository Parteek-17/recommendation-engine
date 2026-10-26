"""Client mock mode + the red-team suite meets its target."""

import pytest

from llm import redteam
from llm.client import LLMClient, LLMError, _supports_effort, mock_text


def test_empty_key_means_mock_mode():
    client = LLMClient(api_key="")
    assert client.is_mock
    reply = client.create("fast", system="s", messages=[], mock=lambda m, t: mock_text("hi"))
    assert reply.content[0].text == "hi"


def test_mock_without_fake_reply_raises():
    with pytest.raises(LLMError):
        LLMClient(api_key="").create("fast", system="s", messages=[])


def test_model_names_have_defaults():
    assert LLMClient.model_for("fast")
    assert LLMClient.model_for("smart")


def test_effort_only_on_models_that_support_it():
    assert _supports_effort("claude-sonnet-5-5")
    assert not _supports_effort("claude-haiku-4-5")


def test_redteam_block_rate_meets_target():
    summary = redteam.run(write=False)
    assert summary["attacks"] >= 50
    assert summary["block_rate"] >= redteam.TARGET_BLOCK_RATE, summary["failures"]
    assert summary["false_positives"] == 0, summary["control_failures"]
