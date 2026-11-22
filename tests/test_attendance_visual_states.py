from pathlib import Path


def main():
    root = Path(__file__).parents[1]
    script = (root / "crm/static/attendance.js").read_text(encoding="utf-8")
    css = (root / "crm/static/style.css").read_text(encoding="utf-8")
    assert "e.status==='planned'&&!e.future" in script
    assert "⚠ Нет отметки" in script
    assert "'По плану'" in script
    assert "att-state-missing" in script and ".attendance-cell-event.att-state-missing" in css
    assert ".attendance-cell-event.att-status-planned" in css
    assert ".attendance-cell-event.att-status-present" in css
    assert ".attendance-cell-event.att-status-absent" in css
    print("PASS attendance calendar has distinct planned, present, absent and missing-mark states")


if __name__ == "__main__":
    main()
