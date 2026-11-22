from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_workdesk_supports_status_funnel_for_regular_admin_and_grid_for_other_roles():
    script = (ROOT / "crm/static/erptasks.js").read_text(encoding="utf-8")
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert 'class="et-task-grid"' in script
    assert 'class="et-summary"' in script
    assert 'class="et-board"' in script
    assert 'class="et-col ' in script
    assert 'layout="grid"' in script
    assert '? "funnel" : "grid"' in app
    assert 'erpTasksSection(S.viewAs, taskLayout)' in app
    assert "grid-template-columns:repeat(auto-fill,minmax(270px,1fr))" in styles
    assert ".et-funnel-layout .et-col" in styles


def test_workdesk_task_cards_keep_primary_actions_and_context():
    script = (ROOT / "crm/static/erptasks.js").read_text(encoding="utf-8")

    assert 'class="et-kind"' in script
    assert 'data-etdone="${t.id}"' in script
    assert 'data-etmove="${t.id}"' in script
    assert 'data-etopen="${s.id}"' in script


def test_workdesk_retries_a_temporary_server_or_network_failure():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")

    assert 'const workdeskData = api("GET", workdeskUrl).catch(async (error)' in app
    assert "if (error.status && error.status < 500) throw error" in app
    assert 'return api("GET", workdeskUrl)' in app


def test_task_failure_does_not_replace_the_entire_workdesk():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")

    assert "const tasksHtmlRequest = tasksSection().catch" in app
    assert "Задачи временно не загрузились" in app
    assert "data-reload-workdesk" in app


def test_new_application_is_a_prominent_action_until_client_status_changes():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert "<h3>Новая заявка</h3>" in app
    assert "остаются здесь до первой обработки" in app
    assert "x.needs_processing" in app
    assert 'data-fresh-lead="${lead.id}"' in app
    assert 'Источник: ${esc(lead.source || "не указан")}' in app
    assert ".fresh-leads-alert" in styles
