from quart import current_app, redirect, render_template, request, session, url_for

import database
from config import DASHBOARD_ADMIN_IDS
from web.app import get_dashboard_owner_ids, is_dashboard_owner, login_required


def register_routes(app):
    def pop_notice():
        return session.pop("dashboard_notice", None)

    def set_notice(message: str, level: str = "info"):
        session["dashboard_notice"] = {"message": message, "level": level}

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
        stored_admin_ids = set(await database.get_dashboard_admin_ids())
        viewer_is_super_admin = int(user_id) in DASHBOARD_ADMIN_IDS
        all_admin_ids = set(stored_admin_ids)
        all_admin_ids.update(DASHBOARD_ADMIN_IDS)
        all_admin_ids.update(getattr(bot, "dashboard_owner_ids", set()))

        admin_entries = []
        app_info = await bot.application_info()
        owner_id = app_info.owner.id if getattr(app_info, "owner", None) else None
        for admin_id in sorted(int(admin_id) for admin_id in all_admin_ids):
            source = "admin"
            source_label = "대시보드 관리자"
            removable = admin_id in stored_admin_ids
            if owner_id == admin_id:
                source = "owner"
                source_label = "봇 owner"
                removable = False
            elif admin_id in DASHBOARD_ADMIN_IDS:
                source = "super_admin"
                source_label = "슈퍼 어드민"
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

        return await render_template(
            "dashboard.html",
            metrics=metrics,
            guilds=guilds,
            admin_entries=admin_entries,
            viewer_is_super_admin=viewer_is_super_admin,
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
            return redirect(url_for("index"))

        user_id = int(raw_user_id)
        existing_admin_ids = await database.get_dashboard_admin_ids()
        if user_id in DASHBOARD_ADMIN_IDS or user_id in existing_admin_ids:
            set_notice(f"{user_id} 는 이미 관리자입니다.", "error")
            return redirect(url_for("index"))

        added = await database.add_dashboard_admin(user_id)
        if not added:
            set_notice(f"{user_id} 관리자 추가에 실패했습니다.", "error")
            return redirect(url_for("index"))

        current_app.bot.dashboard_owner_ids = await get_dashboard_owner_ids(current_app.bot)
        set_notice(f"{user_id} 관리자를 추가했습니다.", "success")
        return redirect(url_for("index"))

    @app.route("/admins/<int:user_id>/delete", methods=["POST"])
    @login_required
    async def delete_admin(user_id: int):
        removed = await database.remove_dashboard_admin(user_id)
        if not removed:
            set_notice("삭제 가능한 수동 추가 관리자만 제거할 수 있습니다.", "error")
            return redirect(url_for("index"))

        current_app.bot.dashboard_owner_ids = await get_dashboard_owner_ids(current_app.bot)
        set_notice(f"{user_id} 관리자를 삭제했습니다.", "success")
        return redirect(url_for("index"))
