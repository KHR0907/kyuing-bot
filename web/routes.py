from quart import current_app, redirect, render_template, request, session, url_for

import database
from config import DASHBOARD_ADMIN_IDS
from web.app import get_dashboard_owner_ids, is_dashboard_owner, login_required


def register_routes(app):
    valid_sections = {"overview", "admins", "keywords"}

    def pop_notice():
        return session.pop("dashboard_notice", None)

    def set_notice(message: str, level: str = "info"):
        session["dashboard_notice"] = {"message": message, "level": level}

    def redirect_keywords():
        return redirect(url_for("index", section="keywords"))

    async def resolve_user_label(bot, user_id: int) -> str:
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                return "알 수 없는 사용자"
        return f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name

    @app.route("/")
    async def index():
        section = (request.args.get("section") or "overview").strip().lower()
        if section not in valid_sections:
            section = "overview"

        user_id = session.get("user_id")
        if not user_id:
            return await render_template("login.html")
        if not await is_dashboard_owner(current_app.bot, int(user_id)):
            session.clear()
            return await render_template("login.html")

        bot = current_app.bot
        guild_count = len(bot.guilds)
        active_channel_count = await database.get_total_tts_channel_count()
        metrics = await database.get_dashboard_metrics(guild_count, active_channel_count)
        channel_counts = await database.get_tts_channel_counts_by_guild()
        global_keyword_aliases = await database.get_global_keyword_aliases()
        guild_keyword_aliases = await database.get_guild_keyword_aliases()
        stored_admin_ids = set(await database.get_dashboard_admin_ids())
        viewer_is_super_admin = int(user_id) in DASHBOARD_ADMIN_IDS
        all_admin_ids = set(stored_admin_ids)
        all_admin_ids.update(DASHBOARD_ADMIN_IDS)
        all_admin_ids.update(getattr(bot, "dashboard_owner_ids", set()))

        admin_entries = []
        owner_id = getattr(bot, "application_owner_id", None)
        if owner_id is None:
            app_info = await bot.application_info()
            owner_id = app_info.owner.id if getattr(app_info, "owner", None) else None
            if owner_id is not None:
                bot.application_owner_id = owner_id

        for admin_id in sorted(int(admin_id) for admin_id in all_admin_ids):
            source = "admin"
            source_label = "대시보드 관리자"
            removable = admin_id in stored_admin_ids
            if owner_id == admin_id:
                source = "owner"
                source_label = "앱 owner"
                removable = False
            elif admin_id in DASHBOARD_ADMIN_IDS:
                source = "super_admin"
                source_label = "슈퍼 관리자"
                removable = False
            admin_entries.append(
                {
                    "user_id": admin_id,
                    "display_name": await resolve_user_label(bot, admin_id),
                    "source": source,
                    "source_label": source_label,
                    "removable": removable,
                    "is_current_user": admin_id == int(user_id),
                }
            )

        guilds = []
        for guild in sorted(bot.guilds, key=lambda item: item.name.lower()):
            voice_client = guild.voice_client
            guilds.append(
                {
                    "id": guild.id,
                    "name": guild.name,
                    "icon_url": guild.icon.url if guild.icon else "",
                    "member_count": guild.member_count or 0,
                    "active_channels": channel_counts.get(guild.id, 0),
                    "voice_status": voice_client.channel.name if voice_client and voice_client.channel else "-",
                }
            )

        guild_name_map = {guild["id"]: guild["name"] for guild in guilds}
        guild_keyword_entries = [
            {
                "guild_id": item["guild_id"],
                "guild_name": guild_name_map.get(item["guild_id"], f"Unknown Guild ({item['guild_id']})"),
                "keyword": item["keyword"],
                "replacement": item["replacement"],
            }
            for item in guild_keyword_aliases
        ]

        guild_keywords_grouped = {}
        for entry in guild_keyword_entries:
            guild_id = entry["guild_id"]
            if guild_id not in guild_keywords_grouped:
                guild_keywords_grouped[guild_id] = {
                    "guild_id": guild_id,
                    "guild_name": entry["guild_name"],
                    "items": [],
                }
            guild_keywords_grouped[guild_id]["items"].append(entry)
        guild_keywords_by_guild = list(guild_keywords_grouped.values())

        return await render_template(
            "dashboard.html",
            metrics=metrics,
            guilds=guilds,
            admin_entries=admin_entries,
            global_keyword_aliases=global_keyword_aliases,
            guild_keyword_aliases=guild_keyword_entries,
            guild_keywords_by_guild=guild_keywords_by_guild,
            viewer_is_super_admin=viewer_is_super_admin,
            active_section=section,
            notice=pop_notice(),
        )

    @app.route("/guilds")
    @login_required
    async def guilds():
        return redirect(url_for("index"))

    @app.route("/admins", methods=["POST"])
    @login_required
    async def add_admin():
        form = await request.form
        raw_user_id = (form.get("user_id") or "").strip()
        if not raw_user_id.isdigit():
            set_notice("관리자 ID는 숫자만 입력해야 합니다.", "error")
            return redirect(url_for("index", section="admins"))

        user_id = int(raw_user_id)
        existing_admin_ids = await database.get_dashboard_admin_ids()
        owner_ids = await get_dashboard_owner_ids(current_app.bot)
        if user_id in owner_ids or user_id in existing_admin_ids:
            set_notice(f"{user_id} 는 이미 관리자입니다.", "error")
            return redirect(url_for("index", section="admins"))

        added = await database.add_dashboard_admin(user_id)
        if not added:
            set_notice(f"{user_id} 관리자 추가에 실패했습니다.", "error")
            return redirect(url_for("index", section="admins"))

        current_app.bot.dashboard_owner_ids = await get_dashboard_owner_ids(current_app.bot)
        set_notice(f"{user_id} 관리자를 추가했습니다.", "success")
        return redirect(url_for("index", section="admins"))

    @app.route("/admins/<int:user_id>/delete", methods=["POST"])
    @login_required
    async def delete_admin(user_id: int):
        protected_admin_ids = await get_dashboard_owner_ids(current_app.bot)
        if user_id in DASHBOARD_ADMIN_IDS or user_id == getattr(current_app.bot, "application_owner_id", None):
            set_notice("슈퍼 관리자와 앱 owner는 삭제할 수 없습니다.", "error")
            return redirect(url_for("index", section="admins"))
        if user_id in protected_admin_ids and user_id not in await database.get_dashboard_admin_ids():
            set_notice("삭제 가능한 수동 추가 관리자만 제거할 수 있습니다.", "error")
            return redirect(url_for("index", section="admins"))

        removed = await database.remove_dashboard_admin(user_id)
        if not removed:
            set_notice("삭제 가능한 수동 추가 관리자만 제거할 수 있습니다.", "error")
            return redirect(url_for("index", section="admins"))

        current_app.bot.dashboard_owner_ids = await get_dashboard_owner_ids(current_app.bot)
        set_notice(f"{user_id} 관리자를 삭제했습니다.", "success")
        return redirect(url_for("index", section="admins"))

    @app.route("/keyword-aliases/global", methods=["POST"])
    @login_required
    async def add_global_keyword_alias():
        form = await request.form
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not keyword or not replacement:
            set_notice("전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_keywords()

        added = await database.add_global_keyword_alias(keyword, replacement)
        if not added:
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_keywords()

        set_notice(f"전역 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_keywords()

    @app.route("/keyword-aliases/global/update", methods=["POST"])
    @login_required
    async def update_global_keyword_alias():
        form = await request.form
        original_keyword = (form.get("original_keyword") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not original_keyword or not keyword or not replacement:
            set_notice("수정할 전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_keywords()

        result = await database.update_global_keyword_alias(original_keyword, keyword, replacement)
        if result == "not_found":
            set_notice("수정할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()
        if result == "conflict":
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_keywords()

        set_notice(f"전역 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_keywords()

    @app.route("/keyword-aliases/global/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias_form():
        form = await request.form
        keyword = (form.get("keyword") or "").strip()
        if not keyword:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()
        return await delete_global_keyword_alias(keyword)

    @app.route("/keyword-aliases/global/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias(keyword: str):
        removed = await database.remove_global_keyword_alias(keyword)
        if not removed:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()

        set_notice(f"전역 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_keywords()

    @app.route("/keyword-aliases/guild", methods=["POST"])
    @login_required
    async def add_guild_keyword_alias():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not raw_guild_id.isdigit():
            set_notice("서버를 선택해야 합니다.", "error")
            return redirect_keywords()
        if not keyword or not replacement:
            set_notice("서버 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_keywords()

        guild_id = int(raw_guild_id)
        if current_app.bot.get_guild(guild_id) is None:
            set_notice("선택한 서버를 찾을 수 없습니다.", "error")
            return redirect_keywords()

        added = await database.add_guild_keyword_alias(guild_id, keyword, replacement)
        if not added:
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_keywords()

        set_notice(f"서버 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_keywords()

    @app.route("/keyword-aliases/guild/update", methods=["POST"])
    @login_required
    async def update_guild_keyword_alias():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        original_keyword = (form.get("original_keyword") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not raw_guild_id.isdigit() or not original_keyword or not keyword or not replacement:
            set_notice("수정할 서버 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_keywords()

        guild_id = int(raw_guild_id)
        result = await database.update_guild_keyword_alias(guild_id, original_keyword, keyword, replacement)
        if result == "not_found":
            set_notice("수정할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()
        if result == "conflict":
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_keywords()

        set_notice(f"서버 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_keywords()

    @app.route("/keyword-aliases/guild/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias_form():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        if not raw_guild_id.isdigit() or not keyword:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()
        return await delete_guild_keyword_alias(int(raw_guild_id), keyword)

    @app.route("/keyword-aliases/guild/<int:guild_id>/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias(guild_id: int, keyword: str):
        removed = await database.remove_guild_keyword_alias(guild_id, keyword)
        if not removed:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_keywords()

        set_notice(f"서버 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_keywords()
