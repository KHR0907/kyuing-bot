import csv
import io
from datetime import datetime, timedelta, timezone

from quart import Response, current_app, jsonify, redirect, render_template, request, session, url_for

import database
from config import DASHBOARD_ADMIN_IDS
from web.app import get_dashboard_owner_ids, is_dashboard_owner, login_required


KST = timezone(timedelta(hours=9))


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
    if seconds < 0:
        return "방금"
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
    valid_sections = {"overview", "admins", "pronunciation", "audit"}
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

    def _actor_id() -> int:
        raw = session.get("user_id")
        return int(raw) if raw else 0

    def _compute_health(metrics: dict, guilds: list) -> dict:
        today = metrics.get("daily_requests", 0)
        yesterday = metrics.get("daily_requests_yesterday", 0)
        signals = []
        if yesterday >= 20 and today < yesterday * 0.5:
            signals.append({
                "level": "warn",
                "label": f"오늘 요청이 어제 대비 {int((1 - today / yesterday) * 100)}% 감소",
            })
        empty_guilds = [g for g in guilds if g["active_channels"] == 0]
        if empty_guilds and len(empty_guilds) == len(guilds) and guilds:
            signals.append({"level": "warn", "label": "활성 TTS 채널이 0개"})
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
        guild_count = len(bot.guilds)
        active_channel_count = await database.get_total_tts_channel_count()
        metrics = await database.get_dashboard_metrics(guild_count, active_channel_count)

        recent = metrics.get("recent_requests", [])
        if recent:
            avg = sum(r["tts_requests"] for r in recent) / max(1, len(recent))
            for r in recent:
                r["is_anomaly"] = avg > 0 and r["tts_requests"] < avg * 0.4
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

        # 이상 신호 우선 정렬: 활성채널 0개 → 위로
        guilds.sort(key=lambda g: (g["active_channels"] > 0, g["name"].lower()))

        health = _compute_health(metrics, guilds)

        guild_name_map = {g["id"]: g["name"] for g in guilds}

        # 통합 규칙 리스트 (table 렌더링용) + 충돌 마킹
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
            raw_audit = await database.get_audit_log(limit=200)
            for entry in raw_audit:
                entry["actor_label"] = await resolve_user_label(bot, entry["actor_id"]) if entry["actor_id"] else "system"
                entry["guild_name"] = guild_name_map.get(entry["guild_id"]) if entry["guild_id"] else None
                entry["timestamp_label"] = _format_relative(entry["timestamp"])
                audit_entries.append(entry)

        return await render_template(
            "dashboard.html",
            metrics=metrics,
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
        )

    @app.route("/servers/<int:guild_id>")
    @login_required
    async def server_detail(guild_id: int):
        bot = current_app.bot
        guild = bot.get_guild(guild_id)
        if guild is None:
            set_notice("해당 서버를 찾을 수 없습니다.", "error")
            return redirect(url_for("index"))

        rules = await database.get_guild_keyword_aliases_for(guild_id)
        for r in rules:
            r["last_seen_label"] = _format_relative(r["last_seen_at"])

        global_rules = await database.get_global_keyword_aliases()
        guild_keyword_set = {r["keyword"] for r in rules}
        applicable_globals = [g for g in global_rules if g["keyword"] not in guild_keyword_set]
        for r in applicable_globals:
            r["last_seen_label"] = _format_relative(r["last_seen_at"])

        channels = await database.get_tts_channels(guild_id)
        voice_client = guild.voice_client

        return await render_template(
            "server_detail.html",
            guild={
                "id": guild.id,
                "name": guild.name,
                "icon_url": guild.icon.url if guild.icon else "",
                "member_count": guild.member_count or 0,
                "voice_status": voice_client.channel.name if voice_client and voice_client.channel else None,
            },
            tts_channel_count=len(channels),
            guild_rules=rules,
            global_rules=applicable_globals,
            notice=pop_notice(),
        )

    @app.route("/guilds")
    @login_required
    async def guilds_redirect():
        return redirect(url_for("index"))

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

    async def _validate_rule_payload(data: dict) -> tuple[dict | None, str | None]:
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
            if current_app.bot.get_guild(guild_id) is None:
                return None, "선택한 서버를 찾을 수 없습니다."

        return {"scope": scope, "guild_id": guild_id, "keyword": keyword, "replacement": replacement}, None

    @app.route("/api/pronunciation/rules", methods=["POST"])
    @login_required
    async def api_create_rule():
        data = await request.get_json(silent=True) or {}
        payload, err = await _validate_rule_payload(data)
        if err:
            return jsonify({"error": err}), 400

        if payload["scope"] == "global":
            ok = await database.add_global_keyword_alias(payload["keyword"], payload["replacement"])
        else:
            ok = await database.add_guild_keyword_alias(payload["guild_id"], payload["keyword"], payload["replacement"])
        if not ok:
            return jsonify({"error": f"이미 등록된 키워드: {payload['keyword']}"}), 409

        await database.write_audit(
            actor_id=_actor_id(),
            action="add",
            scope=payload["scope"],
            guild_id=payload["guild_id"],
            keyword=payload["keyword"],
            new_replacement=payload["replacement"],
        )
        return jsonify({"ok": True}), 201

    @app.route("/api/pronunciation/rules", methods=["PATCH"])
    @login_required
    async def api_update_rule():
        data = await request.get_json(silent=True) or {}
        original_keyword = (data.get("original_keyword") or "").strip()
        if not original_keyword:
            return jsonify({"error": "original_keyword가 필요합니다."}), 400
        payload, err = await _validate_rule_payload(data)
        if err:
            return jsonify({"error": err}), 400

        if payload["scope"] == "global":
            result = await database.update_global_keyword_alias(
                original_keyword, payload["keyword"], payload["replacement"]
            )
        else:
            result = await database.update_guild_keyword_alias(
                payload["guild_id"], original_keyword, payload["keyword"], payload["replacement"]
            )

        if result == "not_found":
            return jsonify({"error": "수정할 키워드를 찾을 수 없습니다."}), 404
        if result == "conflict":
            return jsonify({"error": f"이미 등록된 키워드: {payload['keyword']}"}), 409

        await database.write_audit(
            actor_id=_actor_id(),
            action="update",
            scope=payload["scope"],
            guild_id=payload["guild_id"],
            keyword=payload["keyword"],
            new_replacement=payload["replacement"],
        )
        return jsonify({"ok": True})

    @app.route("/api/pronunciation/rules", methods=["DELETE"])
    @login_required
    async def api_delete_rule():
        data = await request.get_json(silent=True) or {}
        scope = (data.get("scope") or "").strip()
        keyword = (data.get("keyword") or "").strip()
        if scope not in ("global", "guild") or not keyword:
            return jsonify({"error": "scope와 keyword가 필요합니다."}), 400

        guild_id = None
        if scope == "guild":
            try:
                guild_id = int(data.get("guild_id"))
            except (TypeError, ValueError):
                return jsonify({"error": "guild_id가 필요합니다."}), 400
            removed = await database.remove_guild_keyword_alias(guild_id, keyword)
        else:
            removed = await database.remove_global_keyword_alias(keyword)

        if not removed:
            return jsonify({"error": "삭제할 키워드를 찾을 수 없습니다."}), 404

        await database.write_audit(
            actor_id=_actor_id(),
            action="delete",
            scope=scope,
            guild_id=guild_id,
            keyword=keyword,
        )
        return jsonify({"ok": True})

    @app.route("/api/pronunciation/preview", methods=["POST"])
    @login_required
    async def api_preview():
        data = await request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        scope = (data.get("scope") or "global").strip()
        try:
            guild_id = int(data.get("guild_id")) if data.get("guild_id") not in (None, "") else None
        except (TypeError, ValueError):
            guild_id = None

        if not text:
            return jsonify({"original": "", "resolved": "", "scope": None})

        # 미리보기 계산: scope 가 guild면 해당 서버 우선, 아니면 global만
        if scope == "guild" and guild_id is not None:
            resolved, hit_scope = database.resolve_keyword_replacement(guild_id, text)
        else:
            # global-only 미리보기: guild override 우회
            global_repl = database._global_keyword_cache.get(text)
            if global_repl is not None:
                resolved, hit_scope = global_repl, "global"
            else:
                resolved, hit_scope = text, None

        return jsonify({
            "original": text,
            "resolved": resolved,
            "scope": hit_scope,
            "matched": hit_scope is not None,
        })

    @app.route("/api/pronunciation/audit")
    @login_required
    async def api_audit():
        try:
            limit = min(int(request.args.get("limit", 100)), 500)
        except ValueError:
            limit = 100
        entries = await database.get_audit_log(limit=limit)
        return jsonify(entries)

    # ───────────────────────── Pronunciation: CSV import/export ─────────────────────────

    @app.route("/pronunciation/export.csv")
    @login_required
    async def export_csv():
        global_rules = await database.get_global_keyword_aliases()
        guild_rules = await database.get_guild_keyword_aliases()
        guild_name_map = {g.id: g.name for g in current_app.bot.guilds}

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
        files = await request.files
        upload = files.get("file")
        if upload is None:
            set_notice("CSV 파일을 선택해주세요.", "error")
            return redirect_pronunciation()

        try:
            content = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            set_notice("CSV는 UTF-8로 인코딩되어야 합니다.", "error")
            return redirect_pronunciation()

        reader = csv.DictReader(io.StringIO(content))
        added = 0
        skipped = 0
        actor_id = _actor_id()
        for row in reader:
            scope = (row.get("scope") or "").strip()
            keyword = (row.get("keyword") or "").strip()
            replacement = (row.get("replacement") or "").strip()
            if scope not in ("global", "guild") or not keyword or not replacement:
                skipped += 1
                continue
            if scope == "guild":
                try:
                    guild_id = int(row.get("guild_id") or "")
                except ValueError:
                    skipped += 1
                    continue
                if current_app.bot.get_guild(guild_id) is None:
                    skipped += 1
                    continue
                ok = await database.add_guild_keyword_alias(guild_id, keyword, replacement)
                if ok:
                    await database.write_audit(
                        actor_id=actor_id, action="add", scope="guild",
                        guild_id=guild_id, keyword=keyword, new_replacement=replacement,
                    )
                    added += 1
                else:
                    skipped += 1
            else:
                ok = await database.add_global_keyword_alias(keyword, replacement)
                if ok:
                    await database.write_audit(
                        actor_id=actor_id, action="add", scope="global",
                        keyword=keyword, new_replacement=replacement,
                    )
                    added += 1
                else:
                    skipped += 1

        set_notice(f"CSV import 완료: {added}개 추가 / {skipped}개 건너뜀", "success")
        return redirect_pronunciation()

    # ───────────────────────── Form fallback (legacy compat) ─────────────────────────

    @app.route("/keyword-aliases/global", methods=["POST"])
    @login_required
    async def add_global_keyword_alias():
        form = await request.form
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not keyword or not replacement:
            set_notice("전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        added = await database.add_global_keyword_alias(keyword, replacement)
        if not added:
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="add", scope="global",
            keyword=keyword, new_replacement=replacement,
        )
        set_notice(f"전역 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/global/update", methods=["POST"])
    @login_required
    async def update_global_keyword_alias():
        form = await request.form
        original_keyword = (form.get("original_keyword") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        replacement = (form.get("replacement") or "").strip()

        if not original_keyword or not keyword or not replacement:
            set_notice("수정할 전역 키워드와 치환 문장을 모두 입력해야 합니다.", "error")
            return redirect_pronunciation()

        result = await database.update_global_keyword_alias(original_keyword, keyword, replacement)
        if result == "not_found":
            set_notice("수정할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        if result == "conflict":
            set_notice(f"전역 키워드 `{keyword}` 는 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="update", scope="global",
            keyword=keyword, new_replacement=replacement,
        )
        set_notice(f"전역 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/global/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias_form():
        form = await request.form
        keyword = (form.get("keyword") or "").strip()
        if not keyword:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        return await delete_global_keyword_alias(keyword)

    @app.route("/keyword-aliases/global/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_global_keyword_alias(keyword: str):
        removed = await database.remove_global_keyword_alias(keyword)
        if not removed:
            set_notice("삭제할 전역 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="delete", scope="global", keyword=keyword,
        )
        set_notice(f"전역 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/guild", methods=["POST"])
    @login_required
    async def add_guild_keyword_alias():
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

        added = await database.add_guild_keyword_alias(guild_id, keyword, replacement)
        if not added:
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="add", scope="guild",
            guild_id=guild_id, keyword=keyword, new_replacement=replacement,
        )
        set_notice(f"서버 키워드 `{keyword}` 를 추가했습니다.", "success")
        return redirect_pronunciation()

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
            return redirect_pronunciation()

        guild_id = int(raw_guild_id)
        result = await database.update_guild_keyword_alias(guild_id, original_keyword, keyword, replacement)
        if result == "not_found":
            set_notice("수정할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        if result == "conflict":
            set_notice(f"해당 서버에는 `{keyword}` 키워드가 이미 등록되어 있습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="update", scope="guild",
            guild_id=guild_id, keyword=keyword, new_replacement=replacement,
        )
        set_notice(f"서버 키워드 `{original_keyword}` 를 수정했습니다.", "success")
        return redirect_pronunciation()

    @app.route("/keyword-aliases/guild/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias_form():
        form = await request.form
        raw_guild_id = (form.get("guild_id") or "").strip()
        keyword = (form.get("keyword") or "").strip()
        if not raw_guild_id.isdigit() or not keyword:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()
        return await delete_guild_keyword_alias(int(raw_guild_id), keyword)

    @app.route("/keyword-aliases/guild/<int:guild_id>/<path:keyword>/delete", methods=["POST"])
    @login_required
    async def delete_guild_keyword_alias(guild_id: int, keyword: str):
        removed = await database.remove_guild_keyword_alias(guild_id, keyword)
        if not removed:
            set_notice("삭제할 서버 키워드를 찾을 수 없습니다.", "error")
            return redirect_pronunciation()

        await database.write_audit(
            actor_id=_actor_id(), action="delete", scope="guild",
            guild_id=guild_id, keyword=keyword,
        )
        set_notice(f"서버 키워드 `{keyword}` 를 삭제했습니다.", "success")
        return redirect_pronunciation()
