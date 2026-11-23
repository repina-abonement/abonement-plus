from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_workdesk_supports_status_funnel_for_regular_admin_and_grid_for_other_roles():
    script = (ROOT / "crm/static/erptasks.js").read_text(encoding="utf-8")
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert 'class="et-task-grid"' in script
    assert 'class="et-summary"' in script
    assert 'data-etfilter="${esc(c.key)}"' in script
    assert 'data-etkind="${esc(col)}"' in script
    assert 'applyKindFilter' in script
    assert '.et-summary-kind.active' in styles
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
    assert '["future", "Будущие"]' in script
    assert 'future: "Будущих задач пока нет"' in script


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


def test_admin_funnels_share_one_readable_pipeline_hub():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert 'class="pipeline-hub"' in app
    assert 'data-pipeline-tab="${item.key}"' in app
    assert 'data-pipeline-panel="${item.key}"' in app
    for label in ("Новые карточки", "Продажи", "Продления", "Пропуски и отработки", "Оплаты Точки", "Задачи"):
        assert label in app
    assert "S.deskPipeline=button.dataset.pipelineTab" in app
    assert ".pipeline-tabs" in styles
    assert ".pipeline-panel[hidden]" in styles
    assert "flex-basis: 82vw" in styles
    assert 'data-pipeline-today' in app
    assert 'data-pipeline-date' in app
    assert 'S.workdeskDate' in app
    assert 'filterBoardForDate' in app
    assert 'card.due || card.stage_changed_at || card.created_at' in app
    assert '.pipeline-date-filter' in styles
    date_filter_markup=app.split('class="pipeline-date-filter"',1)[1].split('</div></div>',1)[0]
    assert 'class="pipeline-hub-total"' in date_filter_markup
    assert '.pipeline-date-filter>.btn { min-height:42px' in styles
    assert '.pipeline-hub-total { display:inline-flex' in styles and 'min-height:42px' in styles
    assert 'wireBoardCardPreview(page)' in app
    assert 'item.task_note' in app
    assert '.board-card-preview-note' in styles


def test_admin_workdesk_temporarily_renders_only_the_pipeline_hub():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")

    assert "page.innerHTML = pipelineHub;" in app
    rendered_tail = app.split("page.innerHTML = pipelineHub;", 1)[1]
    assert 'class="desk-hello"' not in rendered_tail
    assert 'class="desk-side"' not in rendered_tail


def test_new_application_is_a_prominent_action_until_client_status_changes():
    app = (ROOT / "crm/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert "<h3>Новая заявка</h3>" in app
    assert "остаются здесь до первой обработки" in app
    assert "x.needs_processing" in app
    assert 'data-fresh-lead="${lead.id}"' in app
    assert 'Источник: ${esc(lead.source || "не указан")}' in app
    assert ".fresh-leads-alert" in styles
