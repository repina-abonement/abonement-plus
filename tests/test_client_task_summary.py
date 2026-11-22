from pathlib import Path


def main():
    script = (Path(__file__).parents[1] / "crm/static/tasks.js").read_text(encoding="utf-8")
    block = script.split("function cardTasksBlock", 1)[1].split("function wireCardTasks", 1)[0]
    assert "t.kind==='renew'&&t.note" not in block
    assert "openTaskEdit(t, reload)" in script
    assert "Подробное описание задачи" in script
    print("PASS renewal card is concise while full note remains in task details")


if __name__ == "__main__":
    main()
