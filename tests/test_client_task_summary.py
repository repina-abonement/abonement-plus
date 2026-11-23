from pathlib import Path


def main():
    script = (Path(__file__).parents[1] / "crm/static/tasks.js").read_text(encoding="utf-8")
    block = script.split("function cardTasksBlock", 1)[1].split("function wireCardTasks", 1)[0]
    assert "t.kind==='renew'&&t.note" not in block
    assert "openTaskEdit(t, reload)" in script
    assert "Подробное описание задачи" in script
    assert 'class="ct-content"' in block
    assert 'class="ct-title"' in block
    assert 'class="ct-meta"' in block
    assert 'class="ct-count"' in block
    assert "История задач" in block
    print("PASS client task card is concise, structured and keeps full note in task details")


if __name__ == "__main__":
    main()
