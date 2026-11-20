from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_workdesk_tasks_use_responsive_grid_instead_of_erp_columns():
    script = (ROOT / "crm/static/erptasks.js").read_text(encoding="utf-8")
    styles = (ROOT / "crm/static/style.css").read_text(encoding="utf-8")

    assert 'class="et-task-grid"' in script
    assert 'class="et-summary"' in script
    assert 'class="et-board"' not in script
    assert "grid-template-columns:repeat(auto-fill,minmax(270px,1fr))" in styles


def test_workdesk_task_cards_keep_primary_actions_and_context():
    script = (ROOT / "crm/static/erptasks.js").read_text(encoding="utf-8")

    assert 'class="et-kind"' in script
    assert 'data-etdone="${t.id}"' in script
    assert 'data-etmove="${t.id}"' in script
    assert 'data-etopen="${s.id}"' in script
