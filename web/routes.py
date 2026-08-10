import asyncio
import csv
import io
from datetime import datetime

from quart import Response, current_app, jsonify, redirect, render_template, request, session, url_for

import database
import sound_storage
from config import DASHBOARD_ADMIN_IDS, SOUND_MAX_KEYWORD_LENGTH, SOUND_MAX_PER_GUILD
from database import KST
from web.app import get_dashboard_owner_ids, is_dashboard_owner, login_required


MAX_CSV_BYTES = 5 * 1024 * 1024  # 5MB


def _format_relative(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_str)
    except ValueError:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)
    now = datetime.now(KST)
    diff = now - ts
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}일 전"
    return ts.strftime("%Y-%m-%d")


def register_routes(app):
    valid_sections = {"overview", "bots", "admins", "pronunciation", "audit", "sounds"}
    section_aliases = {"keywords": "pronunciation"}

    def pop_notice():
        return session.pop("dashboard_notice", None)

    def set_notice(message: str, level: str = "info"):
        session["dashboard_notice"] = {"message": message, "level": level}

    def redirect_pronunciation():
        return redirect(url_for("index", section="pronunciation"))

    async def resolve_user_label(bot, user_id: int) -> str:
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                return "알 수 없는 사용자"
        return f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name

    async def resolve_user_labels_bulk(bot, user_ids: set[int]) -> dict[int, str]:
        """unique user_ids만 병렬로 resolve. fetch_user의 N+1 회피."""
        ids = list(user_ids)
        labels = await asyncio.gather(
            *(resolve_user_label(bot, uid) for uid in ids),
            return_exceptions=False,
        )
        return dict(zip(ids, labels))

    def _actor_id() -> int:
        raw = session.get("user_id")
        return int(raw) if raw else 0

    async def _validate_discord_bot_token(token: str) -> tuple[dict | None, str | None]:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
            ) as resp:
                if resp.status != 200:
                    return None, f"Discord 봇 토큰 검증 실패(status={resp.status})"
                data = await resp.json()
        if not data.get("bot"):
            return None, "입력한 토큰은 Discord 봇 토큰이 아닙니다."
        return data, None

    def _compute_health(metrics: dict, guilds: list) -> dict:
        today = metrics.get("daily_requests", 0)
        yesterday = metrics.get("daily_requests_yesterday", 0)
        signals = []

        if yesterday >= 20 and today < yesterday * 0.5:
            drop_pct = int((1 - today / yesterday) * 100)
            signals.append({
                "level": "warn",
                "label": f"오늘 요청이 어제 대비 {drop_pct}% 감소",
            })

        if guilds:
            empty_count = sum(1 for g in guilds if g["active_channels"] == 0)
            empty_ratio = empty_count / len(guilds)
            if empty_ratio >= 0.5:
                signals.append({
                    "level": "warn",
                    "label": f"{empty_count}/{len(guilds)} 서버 미설정 ({int(empty_ratio * 100)}%)",
                })
            elif empty_ratio >= 0.3:
                signals.append({
                    "level": "warn",
                    "label": f"{empty_count}개 서버 미설정",
                })

        if not signals:
            return {"level": "ok", "label": "정상 운영 중", "signals": []}
        worst = "error" if any(s["level"] == "error" for s in signals) else "warn"
        return {
            "level": worst,
            "label": f"이상 신호 {len(signals)}건",
            "signals": signals,
        }

    @app.route("/")
    async def index():
        raw_section = (request.args.get("section") or "overview").strip().lower()
        section = section_aliases.get(raw_section, raw_section)
        if section not in valid_sections:
            section = "overview"

        user_id = session.get("user_id")
        if not user_id:
            return await render_template("login.html")
        if not await is_dashboard_owner(current_app.bot, int(user_id)):
            session.clear()
            return await render_template("login.html")

        bot = current_app.bot
        bot_records = await database.get_bots()
        try:
            selected_bot_id = int(request.args.get("bot_id") or getattr(bot, "bot_id", 1))
        except ValueError:
            selected_bot_id = getattr(bot, "bot_id", 1)
        if not any(item["id"] == selected_bot_id for item in bot_records):
            selected_bot_id = bot_records[0]["id"] if bot_records else getattr(bot, "bot_id", 1)
        selected_bot = await database.get_bot(selected_bot_id)
        guild_snapshot_rows = await database.get_bot_guild_snapshots(selected_bot_id)
        selected_bot_is_live = (selected_bot or {}).get("status") == "running"

        guild_count = len(guild_snapshot_rows) or (selected_bot or {}).get("guild_count", 0)
        active_channel_count = await database.get_total_tts_channel_count(bot_id=selected_bot_id)
        metrics = await database.get_dashboard_metrics(guild_count, active_channel_count, bot_id=selected_bot_id)

        recent = metrics.get("recent_requests", [])
        if recent:
            avg = sum(r["tts_requests"] for r in recent) / max(1, len(recent))
            for r in recent:
                r["is_anomaly"] = avg > 0 and r["tts_requests"] < avg * 0.4
        channel_counts = await database.get_tts_channel_counts_by_guild(bot_id=selected_bot_id)
        global_keyword_aliases = await database.get_global_keyword_aliases(bot_id=selected_bot_id)
        guild_keyword_aliases = await database.get_guild_keyword_aliases(bot_id=selected_bot_id)
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

        admin_id_list = sorted(int(admin_id) for admin_id in all_admin_ids)
        admin_labels = await resolve_user_labels_bulk(bot, set(admin_id_list))
        for admin_id in admin_id_list:
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
                    "display_name": admin_labels.get(admin_id, "알 수 없는 사용자"),
                    "source": source,
                    "source_label": source_label,
                    "removable": removable,
                    "is_current_user": admin_id == int(user_id),
                }
            )

        guilds = []
        for guild in guild_snapshot_rows:
            guilds.append(
                {
                    "id": guild["id"],
                    "name": guild["name"],
                    "icon_url": guild["icon_url"],
                    "member_count": guild["member_count"],
                    "active_channels": channel_counts.get(guild["id"], 0),
                    "voice_status": guild["voice_channel_name"] or "-",
                }
            )

        # 이상 신호 우선 정렬: 활성채널 0개 → 위로
        guilds.sort(key=lambda g: (g["active_channels"] > 0, g["name"].lower()))

        health = _compute_health(metrics, guilds)

        guild_name_map = {g["id"]: g["name"] for g in guilds}

        global_sounds = await database.get_global_sounds(bot_id=selected_bot_id)
        guild_sounds = await database.get_guild_sounds(bot_id=selected_bot_id)
        for s in guild_sounds:
            s["guild_name"] = guild_name_map.get(s["guild_id"], f"Unknown ({s['guild_id']})")
        for s in (*global_sounds, *guild_sounds):
            s["created_label"] = _format_relative(s["created_at"])

        # 통합 규칙 리스트 + 충돌 마킹
        global_keyword_set = {item["keyword"] for item in global_keyword_aliases}
        guild_keyword_set = {(it["guild_id"], it["keyword"]) for it in guild_keyword_aliases}

        unified_rules = []
        for item in global_keyword_aliases:
            overridden_in = [
                guild_name_map.get(g_id, str(g_id))
                for (g_id, kw) in guild_keyword_set
                if kw == item["keyword"]
            ]
            unified_rules.append({
                "scope": "global",
                "guild_id": None,
                "guild_name": None,
                "keyword": item["keyword"],
                "replacement": item["replacement"],
                "hit_count": item["hit_count"],
                "last_seen_at": item["last_seen_at"],
                "last_seen_label": _format_relative(item["last_seen_at"]),
                "overridden_in": overridden_in,
                "overrides_global": False,
            })
        for item in guild_keyword_aliases:
            unified_rules.append({
                "scope": "guild",
                "guild_id": item["guild_id"],
                "guild_name": guild_name_map.get(item["guild_id"], f"Unknown ({item['guild_id']})"),
                "keyword": item["keyword"],
                "replacement": item["replacement"],
                "hit_count": item["hit_count"],
                "last_seen_at": item["last_seen_at"],
                "last_seen_label": _format_relative(item["last_seen_at"]),
                "overridden_in": [],
                "overrides_global": item["keyword"] in global_keyword_set,
            })

        audit_entries = []
        if section == "audit":
            raw_audit = await database.get_audit_log(limit=200, bot_id=selected_bot_id)
            unique_actors = {e["actor_id"] for e in raw_audit if e["actor_id"]}
            actor_labels = await resolve_user_labels_bulk(bot, unique_actors)
            for entry in raw_audit:
                aid = entry["actor_id"]
                entry["actor_label"] = actor_labels.get(aid, "system") if aid else "system"
                entry["guild_name"] = guild_name_map.get(entry["guild_id"]) if entry["guild_id"] else None
                entry["timestamp_label"] = _format_relative(entry["timestamp"])
                audit_entries.append(entry)

        # 서버 상세 → 대시보드 진입 시 guildFilter 자동 적용용
        initial_guild_filter = (request.args.get("guild") or "").strip()

        project_metrics = await database.get_project_metrics()
        return await render_template(
            "dashboard.html",
            metrics=metrics,
            project_metrics=project_metrics,
            selected_bot=selected_bot,
            selected_bot_id=selected_bot_id,
            selected_bot_is_live=selected_bot_is_live,
            primary_bot_id=getattr(bot, "bot_id", 1),
            protected_bot_ids=(getattr(current_app, "bot_process_manager", None).protected_bot_ids
                               if getattr(current_app, "bot_process_manager", None) else set()),
            bot_selector_url=url_for("index", section=section),
            bot_records=bot_records,
            guilds=guilds,
            admin_entries=admin_entries,
            unified_rules=unified_rules,
            global_keyword_aliases=global_keyword_aliases,
            guild_keyword_aliases=guild_keyword_aliases,
            viewer_is_super_admin=viewer_is_super_admin,
            active_section=section,
            notice=pop_notice(),
            health=health,
            audit_entries=audit_entries,
            initial_guild_filter=initial_guild_filter,
            global_sounds=global_sounds,
            guild_sounds=guild_sounds,
        )

    @app.route("/servers/<int:guild_id>")
    @login_required
    async def server_detail_legacy(guild_id: int):
        return redirect(url_for("server_detail", bot_id=getattr(current_app.bot, "bot_id", 1), guild_id=guild_id))

    @app.route("/bots/<int:bot_id>/servers/<int:guild_id>")
    @login_required
    async def server_detail(bot_id: int, guild_id: int):
        bot = current_app.bot
        bot_record = await database.get_bot(bot_id)
        if bot_record is None:
            set_notice("해당 봇을 찾을 수 없습니다.", "error")
            return redirect(url_for("index"))
        guild = await database.get_bot_guild_snapshot(bot_id, guild_id)
        if guild is None:
            set_notice("해당 봇의 서버 정보를 찾을 수 없습니다.", "error")
            return redirect(url_for("index", section="overview", bot_id=bot_id))

        rules = await database.get_guild_keyword_aliases_for(guild_id, bot_id=bot_id)
        for r in rules:
            r["last_seen_label"] = _format_relative(r["last_seen_at"])

        global_rules = await database.get_global_keyword_aliases(bot_id=bot_id)
        guild_keyword_set = {r["keyword"] for r in rules}
        applicable_globals = [g for g in global_rules if g["keyword"] not in guild_keyword_set]
        for r in applicable_globals:
            r["last_seen_label"] = _format_relative(r["last_seen_at"])

        channels = await database.get_tts_channels(guild_id, bot_id=bot_id)
        return await render_template(
            "server_detail.html",
            guild={
                "id": guild["id"],
                "name": guild["name"],
                "icon_url": guild["icon_url"],
                "member_count": guild["member_count"],
                "voice_status": guild["voice_channel_name"],
            },
            bot_record=bot_record,
            tts_channel_count=len(channels),
            guild_rules=rules,
            global_rules=applicable_globals,
            notice=pop_notice(),
        )

    @app.route("/guilds")
    @login_required
    async def guilds_redirect():
        return redirect(url_for("index"))

    # ───────────────────────── Bots ─────────────────────────

    @app.route("/bots", methods=["POST"])
    @login_required
    async def add_bot():
        form = await request.form
        name = (form.get("name") or "").strip()
        token = (form.get("token") or "").strip()
        if not token:
            set_notice("Discord Bot Token을 입력해야 합니다.", "error")
            return redirect(url_for("index", section="bots"))

        bot_user, err = await _validate_discord_bot_token(token)
        if err:
            set_notice(err, "error")
            return redirect(url_for("index", section="bots"))

        username = bot_user.get("username") or "KYUING Bot"
        created = await database.create_bot(
            name or username,
            token,
            created_by=_actor_id(),
            discord_bot_user_id=int(bot_user["id"]),
            discord_username=username,
        )
        if created is None:
            set_notice("이미 등록된 Discord 봇입니다.", "error")
            return redirect(url_for("index", section="bots"))

        manager = getattr(current_app, "bot_process_manager", None)
        if manager is not None:
            started = await manager.start_bot(created["id"])
            if not started:
                set_notice(f"봇 `{created['name']}` 을 추가했지만 시작하지 못했습니다.", "error")
                return redirect(url_for("index", section="bots"))
        set_notice(f"봇 `{created['name']}` 을 추가하고 시작했습니다.", "success")
        return redirect(url_for("index", section="bots"))

    @app.route("/bots/<int:bot_id>/<action>", methods=["POST"])
    @login_required
    async def bot_action(bot_id: int, action: str):
        manager = getattr(current_app, "bot_process_manager", None)
        if action not in {"start", "stop", "restart", "disable", "enable"}:
            set_notice("지원하지 않는 봇 작업입니다.", "error")
            return redirect(url_for("index", section="bots"))
        if await database.get_bot(bot_id) is None:
            set_notice("해당 봇을 찾을 수 없습니다.", "error")
            return redirect(url_for("index", section="bots"))
        if manager is not None and manager.is_protected(bot_id):
            set_notice(
                "기본 봇은 대시보드와 같은 프로세스에서 실행되므로 여기서 제어할 수 없습니다. "
                "컨테이너를 재시작해주세요.",
                "error",
            )
            return redirect(url_for("index", section="bots"))
        if action == "enable":
            await database.set_bot_enabled(bot_id, True)
            await database.set_bot_desired_state(bot_id, "running")
            if manager is not None:
                operation_ok = await manager.start_bot(bot_id)
            else:
                operation_ok = False
        elif action == "disable":
            await database.set_bot_desired_state(bot_id, "stopped")
            await database.set_bot_enabled(bot_id, False)
            if manager is not None:
                operation_ok = await manager.stop_bot(bot_id)
            else:
                operation_ok = False
        elif manager is None:
            set_notice("봇 프로세스 매니저가 초기화되지 않았습니다.", "error")
            return redirect(url_for("index", section="bots"))
        elif action == "start":
            await database.set_bot_desired_state(bot_id, "running")
            operation_ok = await manager.start_bot(bot_id)
        elif action == "stop":
            await database.set_bot_desired_state(bot_id, "stopped")
            operation_ok = await manager.stop_bot(bot_id)
        elif action == "restart":
            await database.set_bot_desired_state(bot_id, "running")
            operation_ok = await manager.restart_bot(bot_id)
        if operation_ok:
            set_notice(f"봇 {bot_id} 작업 `{action}` 요청 완료", "success")
        else:
            set_notice(f"봇 {bot_id} 작업 `{action}` 을 완료하지 못했습니다.", "error")
        return redirect(url_for("index", section="bots"))

    # ───────────────────────── Sounds (사운드보드) ─────────────────────────

    def redirect_sounds():
        return redirect(url_for("index", section="sounds"))

    @app.route("/sounds/upload", methods=["POST"])
    @login_required
    async def upload_sound():
        form = await request.form
        try:
            bot_id = int(form.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        except (TypeError, ValueError):
            set_notice("봇 ID가 올바르지 않습니다.", "error")
            return redirect_sounds()
        scope = (form.get("scope") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        raw_guild_id = (form.get("guild_id") or "").strip()

        if scope not in ("global", "guild"):
            set_notice("scope는 global 또는 guild여야 합니다.", "error")
            return redirect_sounds()
        if not keyword or len(keyword) > SOUND_MAX_KEYWORD_LENGTH:
            set_notice(f"키워드는 1~{SOUND_MAX_KEYWORD_LENGTH}자여야 합니다.", "error")
            return redirect_sounds()

        guild_id = None
        if scope == "guild":
            if not raw_guild_id.isdigit():
                set_notice("서버를 선택해야 합니다.", "error")
                return redirect_sounds()
            guild_id = int(raw_guild_id)
            if await database.get_guild_sound_count(guild_id, bot_id=bot_id) >= SOUND_MAX_PER_GUILD:
                set_notice(f"서버당 음원은 최대 {SOUND_MAX_PER_GUILD}개까지 등록할 수 있습니다.", "error")
                return redirect_sounds()

        files = await request.files
        upload = files.get("file")
        if upload is None or not upload.filename:
            set_notice("음원 파일을 선택해주세요.", "error")
            return redirect_sounds()

        data = upload.read()
        try:
            filename, duration = await sound_storage.save_sound_file(data, bot_id=bot_id)
        except sound_storage.SoundValidationError as e:
            set_notice(str(e), "error")
            return redirect_sounds()

        try:
            created = await database.add_sound(
                scope, keyword, filename, duration,
                guild_id=guild_id, original_filename=upload.filename,
                created_by=_actor_id(), bot_id=bot_id,
            )
        except Exception:
            sound_storage.delete_sound_file(filename, bot_id=bot_id)
            raise
        if created is None:
            sound_storage.delete_sound_file(filename, bot_id=bot_id)
            set_notice(f"이미 등록된 키워드: {keyword}", "error")
            return redirect_sounds()
        set_notice(f"음원 `{keyword}` 를 등록했습니다. ({duration:.1f}초)", "success")
        return redirect_sounds()

    @app.route("/sounds/<int:sound_id>/delete", methods=["POST"])
    @login_required
    async def delete_sound(sound_id: int):
        removed = await database.remove_sound_by_id(sound_id)
        if removed is None:
            set_notice("삭제할 음원을 찾을 수 없습니다.", "error")
            return redirect_sounds()
        sound_storage.delete_sound_file(removed["filename"], bot_id=removed["bot_id"])
        set_notice(f"음원 `{removed['keyword']}` 를 삭제했습니다.", "success")
        return redirect_sounds()

    # ───────────────────────── Admins ─────────────────────────

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

    # ───────────────────────── Pronunciation: JSON API ─────────────────────────

    def _validate_rule_payload(data: dict) -> tuple[dict | None, str | None]:
        try:
            bot_id = int(data.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        except (TypeError, ValueError):
            return None, "봇 ID가 올바르지 않습니다."
        scope = (data.get("scope") or "").strip()
        keyword = (data.get("keyword") or "").strip()
        replacement = (data.get("replacement") or "").strip()
        if scope not in ("global", "guild"):
            return None, "scope는 global 또는 guild여야 합니다."
        if not keyword or not replacement:
            return None, "키워드와 치환 문장을 모두 입력해야 합니다."

        guild_id = None
        if scope == "guild":
            raw = data.get("guild_id")
            try:
                guild_id = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None, "서버 ID가 올바르지 않습니다."
            if guild_id is None:
                return None, "서버를 선택해야 합니다."
            if bot_id == getattr(current_app.bot, "bot_id", 1) and current_app.bot.get_guild(guild_id) is None:
                return None, "선택한 서버를 찾을 수 없습니다."

        return {"bot_id": bot_id, "scope": scope, "guild_id": guild_id, "keyword": keyword, "replacement": replacement}, None

    @app.route("/api/pronunciation/rules", methods=["POST"])
    @login_required
    async def api_create_rule():
        data = await request.get_json(silent=True) or {}
        payload, err = _validate_rule_payload(data)
        if err:
            return jsonify({"error": err}), 400

        actor = _actor_id()
        if payload["scope"] == "global":
            ok = await database.add_global_keyword_alias(
                payload["keyword"], payload["replacement"], audit_actor=actor, bot_id=payload["bot_id"],
            )
        else:
            ok = await database.add_guild_keyword_alias(
                payload["guild_id"], payload["keyword"], payload["replacement"],
                audit_actor=actor, bot_id=payload["bot_id"],
            )
        if not ok:
            return jsonify({"error": f"이미 등록된 키워드: {payload['keyword']}"}), 409
        return jsonify({"ok": True}), 201

    @app.route("/api/pronunciation/rules", methods=["PATCH"])
    @login_required
    async def api_update_rule():
        data = await request.get_json(silent=True) or {}
        original_keyword = (data.get("original_keyword") or "").strip()
        if not original_keyword:
            return jsonify({"error": "original_keyword가 필요합니다."}), 400
        payload, err = _validate_rule_payload(data)
        if err:
            return jsonify({"error": err}), 400

        actor = _actor_id()
        if payload["scope"] == "global":
            result = await database.update_global_keyword_alias(
                original_keyword, payload["keyword"], payload["replacement"],
                audit_actor=actor, bot_id=payload["bot_id"],
            )
        else:
            result = await database.update_guild_keyword_alias(
                payload["guild_id"], original_keyword,
                payload["keyword"], payload["replacement"],
                audit_actor=actor, bot_id=payload["bot_id"],
            )

        if result == "not_found":
            return jsonify({"error": "수정할 키워드를 찾을 수 없습니다."}), 404
        if result == "conflict":
            return jsonify({"error": f"이미 등록된 키워드: {payload['keyword']}"}), 409
        return jsonify({"ok": True})

    @app.route("/api/pronunciation/rules", methods=["DELETE"])
    @login_required
    async def api_delete_rule():
        data = await request.get_json(silent=True) or {}
        scope = (data.get("scope") or "").strip()
        keyword = (data.get("keyword") or "").strip()
        if scope not in ("global", "guild") or not keyword:
            return jsonify({"error": "scope와 keyword가 필요합니다."}), 400

        actor = _actor_id()
        if scope == "guild":
            try:
                guild_id = int(data.get("guild_id"))
            except (TypeError, ValueError):
                return jsonify({"error": "guild_id가 필요합니다."}), 400
            removed = await database.remove_guild_keyword_alias(
                guild_id, keyword, audit_actor=actor, bot_id=int(data.get("bot_id") or getattr(current_app.bot, "bot_id", 1)),
            )
        else:
            removed = await database.remove_global_keyword_alias(
                keyword, audit_actor=actor, bot_id=int(data.get("bot_id") or getattr(current_app.bot, "bot_id", 1)),
            )

        if not removed:
            return jsonify({"error": "삭제할 키워드를 찾을 수 없습니다."}), 404
        return jsonify({"ok": True})

    @app.route("/api/pronunciation/audit")
    @login_required
    async def api_audit():
        try:
            limit = min(int(request.args.get("limit", 100)), 500)
        except ValueError:
            limit = 100
        bot_id = int(request.args.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        entries = await database.get_audit_log(limit=limit, bot_id=bot_id)
        return jsonify(entries)

    # ───────────────────────── Pronunciation: CSV import/export ─────────────────────────

    @app.route("/pronunciation/export.csv")
    @login_required
    async def export_csv():
        bot_id = int(request.args.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        global_rules = await database.get_global_keyword_aliases(bot_id=bot_id)
        guild_rules = await database.get_guild_keyword_aliases(bot_id=bot_id)
        guild_name_map = {
            guild["id"]: guild["name"]
            for guild in await database.get_bot_guild_snapshots(bot_id)
        }

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["scope", "guild_id", "guild_name", "keyword", "replacement", "hit_count", "last_seen_at"])
        for r in global_rules:
            writer.writerow(["global", "", "", r["keyword"], r["replacement"], r["hit_count"], r["last_seen_at"] or ""])
        for r in guild_rules:
            writer.writerow([
                "guild",
                r["guild_id"],
                guild_name_map.get(r["guild_id"], ""),
                r["keyword"],
                r["replacement"],
                r["hit_count"],
                r["last_seen_at"] or "",
            ])

        timestamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=pronunciation_{timestamp}.csv",
            },
        )

    @app.route("/pronunciation/import", methods=["POST"])
    @login_required
    async def import_csv():
        bot_id = int((await request.form).get("bot_id") or request.args.get("bot_id") or getattr(current_app.bot, "bot_id", 1))
        files = await request.files
        upload = files.get("file")
        if upload is None:
            set_notice("CSV 파일을 선택해주세요.", "error")
            return redirect_pronunciation()

        raw = upload.read()
        if len(raw) > MAX_CSV_BYTES:
            mb = MAX_CSV_BYTES // (1024 * 1024)
            set_notice(f"CSV는 최대 {mb}MB까지 업로드 가능합니다.", "error")
            return redirect_pronunciation()

        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            set_notice("CSV는 UTF-8로 인코딩되어야 합니다.", "error")
            return redirect_pronunciation()

        reader = csv.DictReader(io.StringIO(content))
        valid_rows = []
        skipped_pre = 0
        valid_guild_ids = {
            guild["id"] for guild in await database.get_bot_guild_snapshots(bot_id)
        }
        for row in reader:
            scope = (row.get("scope") or "").strip()
            keyword = (row.get("keyword") or "").strip()
            replacement = (row.get("replacement") or "").strip()
            if scope not in ("global", "guild") or not keyword or not replacement:
                skipped_pre += 1
                continue
            if scope == "guild":
                try:
                    guild_id = int(row.get("guild_id") or "")
                except ValueError:
                    skipped_pre += 1
                    continue
                if guild_id not in valid_guild_ids:
                    skipped_pre += 1
                    continue
                valid_rows.append({"scope": "guild", "guild_id": guild_id, "keyword": keyword, "replacement": replacement})
            else:
                valid_rows.append({"scope": "global", "guild_id": None, "keyword": keyword, "replacement": replacement})

        added, skipped_db = await database.import_keyword_aliases_batch(valid_rows, _actor_id(), bot_id=bot_id)
        total_skipped = skipped_pre + skipped_db
        set_notice(f"CSV import 완료: {added}개 추가 / {total_skipped}개 건너뜀", "success")
        return redirect_pronunciation()

    # ───────────────────────── Form fallback (legacy compat) ─────────────────────────

    async def _form_delete_global(keyword: str):
        if not keyword:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        removed = await database.remove_global_keyword_alias(keyword, audit_actor=_actor_id())
        if not removed:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        set_notice(f"전역 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_pronunciation()

    async def _form_delete_guild(guild_id: int, keyword: str):
        if not keyword:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        removed = await database.remove_guild_keyword_alias(guild_id, keyword, audit_actor=_actor_id())
        if not removed:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        set_notice(f"서버 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/global", methods=["POST"])
    @login_required
    async def add_global_keyword_alias_route():
        form = await request.form
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not keyword or not replacement:
            set_notice("전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        added = await database.add_global_keyword_alias(keyword, replacement, audit_actor=_actor_id())
        if not added:
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        set_notice(f"전역 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/global/update", methods=["POST"])
    @login_required
    async def update_global_keyword_alias_route():
        form = await request.form
        original_keyword = (form.get("original_keyword") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not original_keyword or not keyword or not replacement:
            set_notice("수정할 전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        result = await database.update_global_keyword_alias(
            original_keyword, keyword, replacement, audit_actor=_actor_id(),
        )
        if result == "not_found":
            set_notice("수정할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        if result == "conflict":
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        set_notice(f"전역 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/global/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias_form():
        form = await request.form
        return await _form_delete_global((form.get("keyword") or "").strip())

    @app.route("/keyword-aliases/global/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias_path(keyword: str):
        return await _form_delete_global(keyword)

    @app.route("/keyword-aliases/guild", methods=["POST"])
    @login_required
    async def add_guild_keyword_alias_route():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not raw_guild_id.isdigit():
            set_notice("서버를 선택해야 합니다.", "error")
            return redirect_pronunciation()
        if not keyword or not replacement:
            set_notice("서버 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        guild_id = int(raw_guild_id)
        if current_app.bot.get_guild(guild_id) is None:
            set_notice("선택한 서버를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()

        added = await database.add_guild_keyword_alias(
            guild_id, keyword, replacement, audit_actor=_actor_id(),
        )
        if not added:
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        set_notice(f"서버 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/guild/update", methods=["POST"])
    @login_required
    async def update_guild_keyword_alias_route():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        original_keyword = (form.get("original_keyword") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not raw_guild_id.isdigit() or not original_keyword or not keyword or not replacement:
            set_notice("수정할 서버 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        guild_id = int(raw_guild_id)
        result = await database.update_guild_keyword_alias(
            guild_id, original_keyword, keyword, replacement, audit_actor=_actor_id(),
        )
        if result == "not_found":
            set_notice("수정할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        if result == "conflict":
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        set_notice(f"서버 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/guild/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias_form():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        if not raw_guild_id.isdigit():
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        return await _form_delete_guild(int(raw_guild_id), keyword)

    @app.route("/keyword-aliases/guild/<int:guild_id>/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias_path(guild_id: int, keyword: str):
        return await _form_delete_guild(guild_id, keyword)
