"""Small Discord REST-backed facade used by the standalone dashboard supervisor."""

from types import SimpleNamespace

import aiohttp

import database


DISCORD_API = "https://discord.com/api/v10"


class DashboardContext:
    def __init__(self, bot_id: int = 1):
        self.bot_id = int(bot_id)
        self.guilds = []
        self.dashboard_owner_ids: set[int] = set()
        self.application_owner_id: int | None = None
        self._users: dict[int, SimpleNamespace] = {}

    async def refresh_guilds(self) -> None:
        rows = await database.get_bot_guild_snapshots(self.bot_id)
        guilds = []
        for row in rows:
            icon = SimpleNamespace(url=row["icon_url"]) if row["icon_url"] else None
            channel = None
            if row["voice_channel_id"]:
                channel = SimpleNamespace(
                    id=row["voice_channel_id"], name=row["voice_channel_name"] or "unknown"
                )
            voice_client = SimpleNamespace(channel=channel) if channel else None
            guilds.append(SimpleNamespace(
                id=row["id"], name=row["name"], icon=icon,
                member_count=row["member_count"], voice_client=voice_client,
            ))
        self.guilds = guilds

    def get_guild(self, guild_id: int):
        return next((guild for guild in self.guilds if guild.id == int(guild_id)), None)

    def get_user(self, user_id: int):
        return self._users.get(int(user_id))

    async def _bot_api_get(self, endpoint: str):
        token = await database.get_bot_token(self.bot_id)
        if not token:
            return None
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(
                f"{DISCORD_API}{endpoint}", headers={"Authorization": f"Bot {token}"}
            ) as response:
                if response.status != 200:
                    return None
                return await response.json()

    async def fetch_user(self, user_id: int):
        data = await self._bot_api_get(f"/users/{int(user_id)}")
        if not data:
            raise LookupError(f"Discord user {user_id} not found")
        user = SimpleNamespace(
            id=int(data["id"]), name=data.get("username") or "unknown",
            discriminator=data.get("discriminator") or "0",
        )
        self._users[user.id] = user
        return user

    async def application_info(self):
        data = await self._bot_api_get("/oauth2/applications/@me")
        if not data:
            return SimpleNamespace(owner=None)
        owner_data = data.get("owner")
        owner = SimpleNamespace(id=int(owner_data["id"])) if owner_data else None
        return SimpleNamespace(owner=owner)
