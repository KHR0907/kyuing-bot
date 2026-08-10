import pytest
from quart import Quart, session
from urllib.parse import parse_qs, urlparse

from web.security import (
    CSRF_SESSION_KEY,
    consume_oauth_state,
    create_oauth_state,
    init_web_security,
)


@pytest.mark.asyncio
async def test_unsafe_request_requires_session_csrf_token():
    app = Quart(__name__)
    app.secret_key = "test-secret"
    init_web_security(app)

    @app.post("/mutate")
    async def mutate():
        return {"ok": True}

    client = app.test_client()
    async with client.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = "known-token"

    assert (await client.post("/mutate")).status_code == 403
    response = await client.post("/mutate", headers={"X-CSRF-Token": "known-token"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_oauth_state_is_single_use_and_expires():
    app = Quart(__name__)
    app.secret_key = "test-secret"
    async with app.test_request_context("/"):
        state = create_oauth_state()
        created_at = session["_oauth_state_created_at"]
        assert consume_oauth_state(state, now=created_at + 10) is True
        assert consume_oauth_state(state, now=created_at + 11) is False

        expired = create_oauth_state()
        created_at = session["_oauth_state_created_at"]
        assert consume_oauth_state(expired, now=created_at + 301) is False


@pytest.mark.asyncio
async def test_discord_login_redirect_contains_session_bound_state():
    from web.app import create_app

    fake_bot = type("FakeBot", (), {"bot_id": 1, "guilds": []})()
    app = create_app(fake_bot)
    client = app.test_client()
    response = await client.get("/login/discord")
    assert response.status_code == 302
    redirect_state = parse_qs(urlparse(response.headers["Location"]).query)["state"][0]
    async with client.session_transaction() as sess:
        assert sess["_oauth_state"] == redirect_state

    invalid = await client.get("/callback?code=unused&state=wrong")
    assert invalid.status_code == 400
