import json

import httpx
import pytest

from formcast.errors import FormcastError
from formcast.llm import (
    LLMConfig,
    Message,
    ToolCall,
    ToolSpec,
    make_provider,
)
from formcast.llm import base as llm_base

ECHO = ToolSpec(
    name="echo",
    description="Echo a string back.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff sleeps are recorded, never performed."""
    sleeps: list[float] = []
    monkeypatch.setattr(llm_base, "_sleep", sleeps.append)
    return sleeps


def cfg(api_format="chat_completions", **kw) -> LLMConfig:
    return LLMConfig(
        base_url="https://example.test/v1/",
        api_key="sk-secret-value",
        model="m-1",
        api_format=api_format,
        **kw,
    )


class Recorder:
    """MockTransport handler that records requests and replays canned responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def chat_reply(content="hi", tool_calls=None, usage=(5, 7)) -> httpx.Response:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
        },
    )


def chat_tool_call(args: str, id="call_1", name="echo") -> dict:
    return {"id": id, "type": "function", "function": {"name": name, "arguments": args}}


def resp_reply(output, usage=(11, 13)) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
        },
    )


def resp_call(args: str, call_id="call_1", name="echo") -> dict:
    return {
        "type": "function_call",
        "id": "fc_1",
        "call_id": call_id,
        "name": name,
        "arguments": args,
    }


# ---- config ---------------------------------------------------------------


def test_config_repr_masks_api_key():
    c = cfg(extra_headers={"X-Token": "header-secret"})
    assert "sk-secret-value" not in repr(c)
    assert "sk-secret-value" not in str(c)
    assert "header-secret" not in repr(c)
    assert "m-1" in repr(c)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("T_BASE_URL", "https://x.test/v1")
    monkeypatch.setenv("T_API_KEY", "k")
    monkeypatch.setenv("T_MODEL", "mm")
    monkeypatch.setenv("T_API_FORMAT", "responses")
    c = LLMConfig.from_env("T")
    assert (c.base_url, c.api_key, c.model, c.api_format) == (
        "https://x.test/v1",
        "k",
        "mm",
        "responses",
    )
    monkeypatch.delenv("T_API_FORMAT")
    assert LLMConfig.from_env("T").api_format == "chat_completions"
    monkeypatch.delenv("T_MODEL")
    with pytest.raises(FormcastError, match="T_MODEL"):
        LLMConfig.from_env("T")


def test_config_rejects_unknown_format():
    with pytest.raises(FormcastError, match="api_format"):
        cfg(api_format="bogus")


def test_make_provider_dispatch():
    from formcast.llm.chat_completions import ChatCompletionsProvider
    from formcast.llm.responses import ResponsesProvider

    assert isinstance(make_provider(cfg()), ChatCompletionsProvider)
    assert isinstance(make_provider(cfg("responses")), ResponsesProvider)


# ---- chat completions -------------------------------------------------------


def test_chat_request_shape():
    rec = Recorder(chat_reply())
    provider = make_provider(
        cfg(temperature=0.2, reasoning_effort="low", extra_headers={"X-Extra": "1"}),
        transport=rec.transport,
    )
    history = [
        Message(role="system", content="be brief"),
        Message(role="user", content="say hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall("call_1", "echo", {"text": "x"}, '{"text": "x"}')],
        ),
        Message(role="tool", content="x", tool_call_id="call_1"),
    ]
    provider.complete(history, [ECHO])

    req = rec.requests[0]
    assert str(req.url) == "https://example.test/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-secret-value"
    assert req.headers["x-extra"] == "1"
    body = rec.body()
    assert body["model"] == "m-1"
    assert body["tool_choice"] == "auto"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo a string back.",
                "parameters": ECHO.parameters,
            },
        }
    ]
    assert body["temperature"] == 0.2
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 8000
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["messages"][1] == {"role": "user", "content": "say hi"}
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][2]["tool_calls"] == [chat_tool_call('{"text": "x"}')]
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "x",
    }


def test_chat_omits_optional_fields_by_default():
    rec = Recorder(chat_reply())
    make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="hi")], []
    )
    body = rec.body()
    assert "temperature" not in body
    assert "reasoning_effort" not in body
    assert "tools" not in body and "tool_choice" not in body


def test_chat_max_tokens_param_is_configurable():
    rec = Recorder(chat_reply())
    make_provider(
        cfg(max_tokens_param="max_tokens", max_output_tokens=99), transport=rec.transport
    ).complete([Message(role="user", content="hi")], [])
    body = rec.body()
    assert body["max_tokens"] == 99
    assert "max_completion_tokens" not in body


def test_chat_parses_text_and_tool_call():
    rec = Recorder(
        chat_reply(
            content=None,
            tool_calls=[chat_tool_call('{"text": "hello"}')],
            usage=(21, 4),
        )
    )
    out = make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.text == ""
    assert out.tool_calls == [
        ToolCall("call_1", "echo", {"text": "hello"}, '{"text": "hello"}')
    ]
    assert (out.usage.input_tokens, out.usage.output_tokens) == (21, 4)
    assert out.raw["choices"][0]["message"]["tool_calls"]
    msg = out.to_message()
    assert msg.role == "assistant" and msg.tool_calls == out.tool_calls


def test_chat_malformed_arguments():
    rec = Recorder(chat_reply(tool_calls=[chat_tool_call("{bad")]))
    out = make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.tool_calls[0].arguments is None
    assert out.tool_calls[0].raw_arguments == "{bad"


def test_chat_empty_arguments_mean_no_arguments():
    rec = Recorder(chat_reply(tool_calls=[chat_tool_call("")]))
    out = make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.tool_calls[0].arguments == {}


def test_non_object_arguments_are_malformed():
    rec = Recorder(chat_reply(tool_calls=[chat_tool_call("[1, 2]")]))
    out = make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.tool_calls[0].arguments is None
    assert out.tool_calls[0].raw_arguments == "[1, 2]"


def test_chat_unexpected_payload_raises():
    rec = Recorder(httpx.Response(200, json={"oops": True}))
    with pytest.raises(FormcastError, match="choices"):
        make_provider(cfg(), transport=rec.transport).complete(
            [Message(role="user", content="go")], []
        )


# ---- responses --------------------------------------------------------------


def test_responses_request_shape():
    rec = Recorder(resp_reply([]))
    provider = make_provider(
        cfg("responses", reasoning_effort="low", temperature=0.5),
        transport=rec.transport,
    )
    history = [
        Message(role="system", content="be brief"),
        Message(role="user", content="say hi"),
        Message(
            role="assistant",
            content="calling",
            tool_calls=[ToolCall("call_1", "echo", {"text": "x"}, '{"text": "x"}')],
        ),
        Message(role="tool", content="x", tool_call_id="call_1"),
    ]
    provider.complete(history, [ECHO])

    req = rec.requests[0]
    assert str(req.url) == "https://example.test/v1/responses"
    assert req.headers["authorization"] == "Bearer sk-secret-value"
    body = rec.body()
    assert body["model"] == "m-1"
    assert body["instructions"] == "be brief"
    assert body["max_output_tokens"] == 8000
    assert body["reasoning"] == {"effort": "low"}
    assert body["temperature"] == 0.5
    assert body["tools"] == [
        {
            "type": "function",
            "name": "echo",
            "description": "Echo a string back.",
            "parameters": ECHO.parameters,
        }
    ]
    assert body["input"] == [
        {"role": "user", "content": "say hi"},
        {"role": "assistant", "content": "calling"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "echo",
            "arguments": '{"text": "x"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "x"},
    ]


def test_responses_omits_optional_fields_by_default():
    rec = Recorder(resp_reply([]))
    make_provider(cfg("responses"), transport=rec.transport).complete(
        [Message(role="user", content="hi")], []
    )
    body = rec.body()
    for key in ("temperature", "reasoning", "tools", "instructions"):
        assert key not in body


def test_responses_parses_function_call_and_message():
    rec = Recorder(
        resp_reply(
            [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                },
                resp_call('{"text": "hello"}'),
            ],
            usage=(30, 6),
        )
    )
    out = make_provider(cfg("responses"), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.text == "ok"
    assert out.tool_calls == [
        ToolCall("call_1", "echo", {"text": "hello"}, '{"text": "hello"}')
    ]
    assert (out.usage.input_tokens, out.usage.output_tokens) == (30, 6)
    assert len(out.raw["output"]) == 3


def test_responses_malformed_arguments():
    rec = Recorder(resp_reply([resp_call("{bad")]))
    out = make_provider(cfg("responses"), transport=rec.transport).complete(
        [Message(role="user", content="go")], [ECHO]
    )
    assert out.tool_calls[0].arguments is None
    assert out.tool_calls[0].raw_arguments == "{bad"


def test_responses_echoes_provider_items_on_next_turn():
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "abc"},
        resp_call('{"text": "hello"}'),
    ]
    rec = Recorder(resp_reply(output), resp_reply([]))
    provider = make_provider(cfg("responses"), transport=rec.transport)
    history = [Message(role="user", content="go")]
    first = provider.complete(history, [ECHO])
    assert first.provider_items == output

    history += [first.to_message(), Message(role="tool", content="hello", tool_call_id="call_1")]
    provider.complete(history, [ECHO])
    assert rec.body(1)["input"] == [
        {"role": "user", "content": "go"},
        *output,
        {"type": "function_call_output", "call_id": "call_1", "output": "hello"},
    ]


def test_responses_failed_status_raises():
    rec = Recorder(
        httpx.Response(200, json={"status": "failed", "error": {"message": "boom"}})
    )
    with pytest.raises(FormcastError, match="boom"):
        make_provider(cfg("responses"), transport=rec.transport).complete(
            [Message(role="user", content="go")], []
        )


# ---- errors and retries -----------------------------------------------------


@pytest.mark.parametrize("fmt", ["chat_completions", "responses"])
def test_429_then_200_retries_once(fmt, no_sleep):
    ok = chat_reply("done") if fmt == "chat_completions" else resp_reply([])
    rec = Recorder(httpx.Response(429, text="slow down"), ok)
    out = make_provider(cfg(fmt), transport=rec.transport).complete(
        [Message(role="user", content="go")], []
    )
    assert len(rec.requests) == 2
    assert len(no_sleep) == 1
    assert out.usage.input_tokens > 0


def test_retry_after_header_is_respected_and_capped(no_sleep):
    rec = Recorder(
        httpx.Response(429, headers={"Retry-After": "3"}, text="x"),
        httpx.Response(503, headers={"Retry-After": "100000"}, text="x"),
        chat_reply(),
    )
    make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], []
    )
    assert no_sleep[0] == 3
    assert no_sleep[1] == llm_base.MAX_BACKOFF_S


def test_backoff_is_exponential_without_retry_after(no_sleep):
    rec = Recorder(
        httpx.Response(500, text="x"),
        httpx.Response(502, text="x"),
        httpx.Response(503, text="x"),
        chat_reply(),
    )
    make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], []
    )
    assert len(no_sleep) == 3
    assert no_sleep[0] < no_sleep[1] < no_sleep[2]


def test_retries_exhausted_raises_with_status(no_sleep):
    rec = Recorder(httpx.Response(503, text="overloaded"))
    with pytest.raises(FormcastError, match="503") as exc:
        make_provider(cfg(max_retries=2), transport=rec.transport).complete(
            [Message(role="user", content="go")], []
        )
    assert "overloaded" in str(exc.value)
    assert len(rec.requests) == 3  # first try + 2 retries


def test_transport_error_is_retried(no_sleep):
    rec = Recorder(httpx.ReadTimeout("slow"), httpx.ConnectError("down"), chat_reply())
    out = make_provider(cfg(), transport=rec.transport).complete(
        [Message(role="user", content="go")], []
    )
    assert out.text == "hi"
    assert len(rec.requests) == 3


def test_transport_error_exhausted_raises(no_sleep):
    rec = Recorder(httpx.ReadTimeout("slow"))
    with pytest.raises(FormcastError, match="timeout|Timeout|slow"):
        make_provider(cfg(max_retries=1), transport=rec.transport).complete(
            [Message(role="user", content="go")], []
        )
    assert len(rec.requests) == 2


@pytest.mark.parametrize("fmt", ["chat_completions", "responses"])
def test_400_raises_with_status_and_excerpt_without_key(fmt, no_sleep):
    rec = Recorder(httpx.Response(400, text="bad request: " + "z" * 5000))
    with pytest.raises(FormcastError, match="400") as exc:
        make_provider(cfg(fmt), transport=rec.transport).complete(
            [Message(role="user", content="go")], []
        )
    assert len(rec.requests) == 1 and not no_sleep
    text = str(exc.value)
    assert "bad request" in text
    assert len(text) < 1500
    assert "sk-secret-value" not in text


# ---- real round trip --------------------------------------------------------


@pytest.mark.llm
@pytest.mark.parametrize("prefix", ["OPENAI_EVAL", "ANTHROPIC_EVAL"])
def test_real_round_trip_with_echo_tool(prefix, eval_env):
    try:
        config = LLMConfig.from_env(prefix)
    except FormcastError:
        pytest.skip(f"{prefix}_* not configured")
    provider = make_provider(config)
    history = [
        Message(
            role="system",
            content="You are a test harness. Always use the provided tools; "
            "after the tool result arrives, reply with one short sentence.",
        ),
        Message(role="user", content="Call the echo tool with text 'ping'."),
    ]
    first = provider.complete(history, [ECHO])
    assert first.tool_calls, "model made no tool call"
    call = first.tool_calls[0]
    assert call.name == "echo"
    assert call.arguments is not None and "text" in call.arguments
    assert first.usage.input_tokens > 0 and first.usage.output_tokens > 0

    history.append(first.to_message())
    history.append(Message(role="tool", content=call.arguments["text"], tool_call_id=call.id))
    second = provider.complete(history, [ECHO])
    assert second.text.strip() or second.tool_calls
