"""The MCP tools, called through the SDK's own client against a fake Brain API."""

import json
from typing import Any

import httpx2
from mcp import Client
from mcp_types import TextContent

from app.mcp_server import make_server


class FakeBrain:
    """Answers every API call with canned JSON, or with an error, and records each request."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[tuple[str, str, Any, str | None]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        key = request.headers.get("X-API-Key")
        self.requests.append((request.method, request.url.path, body, key))
        if self.status != 200:
            return httpx2.Response(self.status, json={"detail": "ANTHROPIC_API_KEY isn't set"})
        return httpx2.Response(200, json={"path": request.url.path, "answered": True})


def brain(fake: FakeBrain) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(fake),
        base_url="http://brain.test",
        headers={"X-API-Key": "test-key-1"},
    )


async def test_each_tool_is_one_call_to_the_brain_api() -> None:
    fake = FakeBrain()

    async with Client(make_server(brain(fake))) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        asked = await client.call_tool("kb_ask", {"question": "How do I add a logo?"})
        await client.call_tool("kb_search", {"query": "logo", "limit": 3})
        await client.call_tool("kb_get_article", {"article_id": 217})
        await client.call_tool("check_capability", {"requirement": "Approve new members"})

    assert tools == {"kb_ask", "kb_search", "kb_get_article", "check_capability"}
    assert fake.requests == [
        ("POST", "/v1/ask", {"question": "How do I add a logo?", "history": []}, "test-key-1"),
        ("POST", "/v1/search", {"query": "logo", "limit": 3}, "test-key-1"),
        ("GET", "/v1/articles/217", None, "test-key-1"),
        ("POST", "/v1/capabilities/check", {"requirement": "Approve new members"}, "test-key-1"),
    ]
    assert not asked.is_error
    assert asked.structured_content == {"path": "/v1/ask", "answered": True}


async def test_an_api_refusal_is_an_error_the_agent_can_read() -> None:
    async with Client(make_server(brain(FakeBrain(status=503)))) as client:
        result = await client.call_tool("kb_ask", {"question": "How do I add a logo?"})

    assert result.is_error
    [block] = result.content
    assert isinstance(block, TextContent)
    assert "HTTP 503: ANTHROPIC_API_KEY isn't set" in block.text
