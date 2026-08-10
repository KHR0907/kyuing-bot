from functools import wraps
from urllib.parse import urlencode

import aiohttp
from loguru import logger as log
from quart import Quart, current_app, session, redirect, render_template, request, url_for

import database
from config import (
    DASHBOARD_ADMIN_IDS,
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SOUND_MAX_FILE_BYTES,
    WEB_SECRET_KEY,
)
from web.security import consume_oauth_state, create_oauth_state, init_web_security

DISCORD_API = "https://discord.com/api/v10"
OAUTH2_URL = "https://discord.com/oauth2/authorize"


async def get_dashboard_owner_ids(bot) -> set[int]:
    owner_ids = set(DASHBOARD_ADMIN_IDS)
    owner_ids.update(await database.get_dashboard_admin_ids())

    application_owner_id = getattr(bot, "application_owner_id", None)
    if application_owner_id:
        owner_ids.add(int(application_owner_id))
        bot.dashboard_owner_ids = owner_ids
        return owner_ids

    try:
        app_info = await bot.application_info()
    except Exception as e:
        log.warning("대시보드 소유자 조회 실패: {}", e)
        bot.dashboard_owner_ids = owner_ids
        return owner_ids

    if getattr(app_info, "owner", None):
        bot.application_owner_id = app_info.owner.id
        owner_ids.add(app_info.owner.id)

    bot.dashboard_owner_ids = owner_ids
    return owner_ids


async def is_dashboard_owner(bot, user_id: int) -> bool:
    owner_ids = await get_dashboard_owner_ids(bot)
    return bool(owner_ids) and user_id in owner_ids


def login_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        if not await is_dashboard_owner(current_app.bot, int(user_id)):
            session.clear()
            return redirect(url_for("login"))
        return await f(*args, **kwargs)
    return decorated


async def discord_api_get(endpoint: str, token: str):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(
            f"{DISCORD_API}{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status == 401:
                return None
            return await resp.json()


def create_app(bot):
    app = Quart(__name__, template_folder="templates")
    app.secret_key = WEB_SECRET_KEY
    app.config.update(
        SESSION_COOKIE_NAME="tts_dashboard_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        MAX_CONTENT_LENGTH=SOUND_MAX_FILE_BYTES,
    )
    app.bot = bot
    app.bot_process_manager = None
    init_web_security(app)

    from web.routes import register_routes
    register_routes(app)

    @app.route("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.route("/health/ready")
    async def health_ready():
        if not database.is_ready():
            return {"status": "not_ready"}, 503
        return {"status": "ok"}

    @app.route("/login")
    async def login():
        if session.get("user_id"):
            return redirect(url_for("index"))
        return await render_template("login.html")

    @app.route("/login/discord")
    async def login_discord():
        state = create_oauth_state()
        query = urlencode({
            'client_id': DISCORD_CLIENT_ID,
            'redirect_uri': DISCORD_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'identify',
            'state': state,
        })
        url = f"{OAUTH2_URL}?{query}"
        log.debug("OAuth2 redirect: {}", url)
        return redirect(url)

    @app.route("/callback")
    async def callback():
        code = request.args.get("code")
        error = request.args.get("error")
        if not consume_oauth_state(request.args.get("state")):
            log.warning("OAuth2 state 검증 실패")
            return "유효하지 않거나 만료된 로그인 요청입니다.", 400

        if error:
            log.error("OAuth2 에러: {}", error)
            return f"OAuth2 에러: {error}", 400

        if not code:
            return redirect(url_for("login"))

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(
                    f"{DISCORD_API}/oauth2/token",
                    data={
                        "client_id": DISCORD_CLIENT_ID,
                        "client_secret": DISCORD_CLIENT_SECRET,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": DISCORD_REDIRECT_URI,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as resp:
                    token_data = await resp.json()
                    log.debug("Token response status: {}", resp.status)

            access_token = token_data.get("access_token")
            if not access_token:
                log.error("토큰 발급 실패: {}", token_data)
                return "Discord 인증 토큰 발급에 실패했습니다.", 400

            user = await discord_api_get("/users/@me", access_token)
            if not user:
                return "유저 정보 조회 실패", 400

            owner_ids = await get_dashboard_owner_ids(current_app.bot)
            log.info("OAuth callback user_id={} owner_ids={}", user["id"], sorted(owner_ids))

            if not owner_ids:
                log.error("대시보드 owner 정보를 확인할 수 없습니다.")
                return "대시보드 owner 정보를 확인할 수 없습니다.", 503

            if int(user["id"]) not in owner_ids:
                log.warning("대시보드 접근 거부: {} ({})", user["username"], user["id"])
                session.clear()
                return await render_template("forbidden.html", user=user), 403

            session.permanent = True
            session["user_id"] = int(user["id"])
            session["username"] = user["username"]
            session["avatar"] = user.get("avatar", "")
            session.modified = True

            log.info("로그인 성공: {}", user["username"])
            return await render_template("login_success.html", user=user)

        except Exception as e:
            log.exception("콜백 처리 중 에러")
            return "로그인 처리 중 오류가 발생했습니다.", 500

    @app.route("/logout", methods=["POST"])
    async def logout():
        session.clear()
        return redirect(url_for("login"))

    return app
