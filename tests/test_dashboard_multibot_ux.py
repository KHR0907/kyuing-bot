from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "web" / "routes.py"
TEMPLATE = ROOT / "web" / "templates" / "dashboard.html"


def test_routes_resolve_selected_bot_and_scope_dashboard_queries():
    routes = ROUTES.read_text()
    assert "selected_bot_id" in routes
    assert "selected_bot = await database.get_bot(selected_bot_id)" in routes
    assert "get_dashboard_metrics(guild_count, active_channel_count, bot_id=selected_bot_id)" in routes
    assert "get_global_keyword_aliases(bot_id=selected_bot_id)" in routes
    assert "get_guild_keyword_aliases(bot_id=selected_bot_id)" in routes
    assert "get_audit_log(limit=200, bot_id=selected_bot_id)" in routes


def test_routes_support_bot_scoped_server_detail_and_csv():
    routes = ROUTES.read_text()
    assert '@app.route("/bots/<int:bot_id>/servers/<int:guild_id>")' in routes
    assert "get_guild_keyword_aliases_for(guild_id, bot_id=bot_id)" in routes
    assert "get_global_keyword_aliases(bot_id=bot_id)" in routes
    assert "bot_id = int(request.args.get(\"bot_id\"" in routes
    assert "import_keyword_aliases_batch(valid_rows, _actor_id(), bot_id=bot_id)" in routes


def test_dashboard_template_has_project_overall_and_per_bot_ux():
    template = TEMPLATE.read_text()
    assert "프로젝트 전체" in template
    assert "선택된 봇" in template
    assert "selected_bot" in template
    assert "bot_selector_url" in template
    assert "section=pronunciation&bot_id=" in template
    assert "/bots/{{ selected_bot.id }}/servers/{{ guild.id }}" in template
    assert "pronunciation-selected-bot" in template


def test_pronunciation_js_sends_bot_id_with_rule_mutations():
    template = TEMPLATE.read_text()
    assert "const selectedBotId" in template
    assert "body.bot_id = selectedBotId" in template
    assert "bot_id: selectedBotId" in template
