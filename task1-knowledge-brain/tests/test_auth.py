"""The X-API-Key check that guards every /v1 route."""

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.deps import require_api_key
from app.config import Settings

# A throwaway app with one protected route, so the check is tested on its own
app = FastAPI()


@app.get("/protected", dependencies=[Depends(require_api_key)])
async def protected() -> dict[str, bool]:
    return {"ok": True}


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 401),
        ({"X-API-Key": "wrong-key"}, 401),
        ({"X-API-Key": "test-key-2"}, 200),
    ],
)
async def test_protected_route(headers: dict[str, str], expected_status: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/protected", headers=headers)

    assert response.status_code == expected_status


async def test_non_ascii_key_is_rejected_not_crashed() -> None:
    settings = Settings(brain_api_keys=SecretStr("test-key-1"))

    with pytest.raises(HTTPException) as error:
        await require_api_key("tést-kéy", settings)

    assert error.value.status_code == 401


async def test_no_keys_configured_rejects_everything() -> None:
    settings = Settings(brain_api_keys=SecretStr(""))

    with pytest.raises(HTTPException):
        await require_api_key("", settings)
