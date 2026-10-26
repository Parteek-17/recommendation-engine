"""The ONE place that talks to the Claude API.

    from llm.client import get_client
    client = get_client()
    reply = client.create("fast", system="...", messages=[...], mock=my_fake_reply_fn)

Mock mode: when ANTHROPIC_API_KEY is empty we never touch the network. Instead the caller's
`mock` function builds a fake reply with the same shape as a real one (a `.content` list of
text / tool_use blocks plus a `.stop_reason`). So the rest of the code — and every teammate
without a key — runs exactly the same path.

Safety:
- The API key is passed straight to the SDK and never printed or logged.
- We log only model name, stop reason and token counts — never prompts (they can hold user text).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from common.config import settings
from common.logging_utils import get_logger

log = get_logger("llm.client")

# Used when .env leaves the model names empty.
DEFAULT_MODELS = {"fast": "claude-haiku-5-5", "smart": "claude-sonnet-5-5"}
REQUEST_TIMEOUT_S = 20.0   # per API request (SDK retries timeouts too)
MAX_RETRIES = 2            # SDK retries 429 / 5xx / connection errors with backoff


class LLMError(RuntimeError):
    """Claude could not give a usable answer (network, auth, refusal...). Callers fall back."""


# ---------------------------------------------------------------------------
# Fake response objects for mock mode (same attribute names as the real SDK objects)
# ---------------------------------------------------------------------------

_ids = itertools.count(1)


@dataclass
class MockTextBlock:
    text: str
    type: str = "text"


@dataclass
class MockToolUseBlock:
    name: str
    input: dict
    id: str = field(default_factory=lambda: f"toolu_mock_{next(_ids):04d}")
    type: str = "tool_use"


@dataclass
class MockMessage:
    content: list
    stop_reason: str = "end_turn"
    model: str = "mock"


def mock_text(text: str) -> MockMessage:
    """Shortcut: a fake reply that is just text."""
    return MockMessage(content=[MockTextBlock(text)])


def mock_tool_call(name: str, tool_input: dict, preamble: str = "") -> MockMessage:
    """Shortcut: a fake reply where 'Claude' asks to run one tool."""
    blocks: list = [MockTextBlock(preamble)] if preamble else []
    blocks.append(MockToolUseBlock(name=name, input=tool_input))
    return MockMessage(content=blocks, stop_reason="tool_use")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _supports_effort(model: str) -> bool:
    """`effort` exists on current models; older ones (e.g. *-4-5, 3.x) reject it with a 400."""
    return not any(old in model for old in ("-4-5", "-3-", "claude-3"))


class LLMClient:
    """Thin wrapper around the anthropic SDK with mock mode, timeouts and retries."""

    def __init__(self, api_key: str | None = None):
        key = settings.ANTHROPIC_API_KEY if api_key is None else api_key
        self._sdk = None
        if key:
            import anthropic  # imported lazily so mock mode works even without the package

            self._sdk = anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)

    @property
    def is_mock(self) -> bool:
        return self._sdk is None

    @staticmethod
    def model_for(kind: str) -> str:
        """'fast' -> CLAUDE_MODEL_FAST, 'smart' -> CLAUDE_MODEL_SMART (with sensible defaults)."""
        configured = settings.CLAUDE_MODEL_FAST if kind == "fast" else settings.CLAUDE_MODEL_SMART
        return configured or DEFAULT_MODELS[kind]

    def create(
        self,
        kind: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        effort: str = "low",
        allow_tools: bool = True,
        mock: Callable[[list[dict], list[dict] | None], Any] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """Send one Messages API request (or build a mock reply). Raises LLMError on failure.

        kind:        'fast' (offer wording) or 'smart' (chat agent)
        effort:      how hard the model thinks — 'low' keeps short tasks fast and cheap.
        allow_tools: False = tools stay defined but Claude must answer in text (tool limit reached).
        mock:        function(messages, tools) -> fake reply, used when there is no API key.
        timeout / max_retries: override the client defaults for this one request (offer wording
                     uses a tighter budget: a template is better than a minute-long wait).
        """
        if self.is_mock:
            if mock is None:
                raise LLMError("mock mode: no mock reply provided")
            return mock(messages, tools if allow_tools else None)

        import anthropic

        model = self.model_for(kind)
        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            if not allow_tools:
                kwargs["tool_choice"] = {"type": "none"}
        if _supports_effort(model):
            kwargs["output_config"] = {"effort": effort}
        overrides = {k: v for k, v in {"timeout": timeout, "max_retries": max_retries}.items() if v is not None}
        sdk = self._sdk.with_options(**overrides) if overrides else self._sdk
        try:
            response = sdk.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise LLMError("Claude rejected the API key (check ANTHROPIC_API_KEY in .env)") from None
        except anthropic.NotFoundError:
            raise LLMError(f"Unknown model '{model}' (check CLAUDE_MODEL_* in .env)") from None
        except anthropic.RateLimitError:
            raise LLMError("Claude rate limit hit") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}") from None
        except anthropic.APIConnectionError:  # includes timeouts
            raise LLMError("Could not reach Claude (network/timeout)") from None

        usage = getattr(response, "usage", None)
        log.info("claude %s stop=%s in=%s out=%s", model, response.stop_reason,
                 getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"))
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined the request")
        return response


_client: LLMClient | None = None


def get_client() -> LLMClient:
    """Shared client (created on first use)."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def is_ready() -> bool:
    """Has the shared client been created already?"""
    return _client is not None


def warm_up() -> None:
    """Create the shared client now instead of on the first offer message.

    `import anthropic` + the SDK's HTTPS setup take ~10-15 s on a laptop (measured 2026-10-26),
    which used to be counted inside the LLM step. The pipeline calls this in a background thread
    so that time overlaps with the other steps. Never raises: the real call falls back anyway.
    """
    try:
        get_client()
    except Exception as exc:
        log.warning("LLM warm-up failed (%s); the first message will retry", type(exc).__name__)


def set_client(client: LLMClient | None) -> None:
    """Swap the shared client (tests use this to force mock mode or inject a fake)."""
    global _client
    _client = client


def text_of(response: Any) -> str:
    """Join all text blocks of a reply (thinking / tool_use blocks are skipped)."""
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
