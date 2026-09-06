"""The telegram channel of `push_outputs`.

Telegram addresses a chat rather than a URL, so unlike the webhook and slack channels the
endpoint is fixed and only the bot token and the destination chat are configurable. That is what
keeps a repository from redirecting a message: there is no URL for it to set.
"""
import json
from unittest.mock import MagicMock

import pytest

import pr_agent.algo.utils as utils
from pr_agent.config_security import REPO_OVERRIDABLE_KEYS_BY_HOST_SECTION


@pytest.fixture
def post(monkeypatch):
    call = MagicMock()
    monkeypatch.setattr(utils.requests, "post", call)
    return call


class _Settings:
    """A settings stand-in: `push_outputs` is the only section `push_outputs` reads."""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, key, default=None):
        return self._cfg if key == "push_outputs" else default


@pytest.fixture
def outputs(monkeypatch):
    def _set(**overrides):
        cfg = {"enable": True, "channels": [], "telegram_bot_token": "", "telegram_chat_id": "",
               "webhook_url": "", "slack_webhook_url": "", "file_path": "pr-agent-outputs/reviews.jsonl"}
        cfg.update(overrides)
        monkeypatch.setattr(utils, "get_settings", lambda: _Settings(cfg))
        return cfg
    return _set


def test_nothing_is_sent_until_the_channel_is_listed(outputs, post):
    outputs(channels=[], telegram_bot_token="t", telegram_chat_id="1")

    utils.push_outputs("review", payload={"a": 1}, markdown="body")

    post.assert_not_called()


def test_nothing_is_sent_while_push_outputs_is_disabled(outputs, post):
    outputs(enable=False, channels=["telegram"], telegram_bot_token="t", telegram_chat_id="1")

    utils.push_outputs("review", payload={"a": 1}, markdown="body")

    post.assert_not_called()


def test_the_chat_is_addressed_on_the_fixed_endpoint(outputs, post):
    outputs(channels=["telegram"], telegram_bot_token="123:ABC", telegram_chat_id="-100999")

    utils.push_outputs("review", payload={"a": 1}, markdown="the review body")

    assert post.call_args.args[0] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert post.call_args.kwargs["json"]["chat_id"] == "-100999"
    assert post.call_args.kwargs["json"]["text"] == "the review body"
    assert post.call_args.kwargs["json"]["disable_web_page_preview"] is True
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["timeout"] == 5


def test_the_payload_is_sent_when_there_is_no_markdown(outputs, post):
    outputs(channels=["telegram"], telegram_bot_token="t", telegram_chat_id="1")

    utils.push_outputs("improve", payload={"code_suggestions": []}, markdown=None)

    assert json.loads(post.call_args.kwargs["json"]["text"]) == {"code_suggestions": []}


def test_a_long_message_is_truncated_to_the_api_limit(outputs, post):
    outputs(channels=["telegram"], telegram_bot_token="t", telegram_chat_id="1")

    utils.push_outputs("review", payload={}, markdown="x" * (utils.TELEGRAM_MAX_MESSAGE_CHARS + 500))

    assert len(post.call_args.kwargs["json"]["text"]) == utils.TELEGRAM_MAX_MESSAGE_CHARS


@pytest.mark.parametrize("token, chat_id", [("", "1"), ("t", ""), ("", ""), ("  ", "  ")])
def test_a_half_configured_channel_sends_nothing(outputs, post, token, chat_id):
    """Sending to the wrong chat is worse than not sending, so an incomplete pair is refused."""
    outputs(channels=["telegram"], telegram_bot_token=token, telegram_chat_id=chat_id)

    utils.push_outputs("review", payload={}, markdown="body")

    post.assert_not_called()


def test_a_failing_send_is_not_fatal(outputs, post):
    """`push_outputs` must never turn a published review into a failed command."""
    outputs(channels=["telegram"], telegram_bot_token="t", telegram_chat_id="1")
    post.side_effect = RuntimeError("connection reset")

    utils.push_outputs("review", payload={}, markdown="body")


def test_a_local_channel_still_runs_when_telegram_fails(outputs, post, tmp_path, monkeypatch):
    """Control: the network channel is last, so a failed send cannot lose the file write."""
    path = tmp_path / "outputs" / "reviews.jsonl"
    outputs(channels=["file", "telegram"], file_path=str(path),
            telegram_bot_token="t", telegram_chat_id="1")
    post.side_effect = RuntimeError("connection reset")

    utils.push_outputs("review", payload={"a": 1}, markdown="body")

    assert json.loads(path.read_text())["markdown"] == "body"


def test_the_slack_channel_is_unchanged(outputs, post):
    """Control: the channel that existed before this one behaves exactly as it did."""
    outputs(channels=["slack"], slack_webhook_url="https://hooks.example.test/abc")

    utils.push_outputs("review", payload={}, markdown="body")

    assert post.call_args.args[0] == "https://hooks.example.test/abc"
    assert post.call_args.kwargs["json"] == {"text": "body"}


def test_a_repository_cannot_configure_the_bot_or_the_chat():
    """The token and chat id are host-only, like every other sink setting."""
    assert REPO_OVERRIDABLE_KEYS_BY_HOST_SECTION["push_outputs"] == frozenset()
