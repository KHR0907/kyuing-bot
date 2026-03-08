from quart import current_app, redirect, render_template, session, url_for

import database
from web.app import is_dashboard_owner, login_required


def register_routes(app):

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

        return await render_template("dashboard.html", metrics=metrics, guilds=guilds)

    @app.route("/guilds")
    @login_required
    async def guilds():
        return redirect(url_for("index"))
