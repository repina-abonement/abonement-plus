from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "crm" / "static"


def test_task_helper_has_no_terminal_period():
    source = (STATIC / "tasks.js").read_text(encoding="utf-8")
    assert "Здесь хранится вся информация, необходимая для выполнения задачи</small>" in source
    assert "Здесь хранится вся информация, необходимая для выполнения задачи.</small>" not in source


def test_static_block_text_has_no_terminal_period():
    forbidden = re.compile(r"(?<!т\.п)\.(?:</(?:small|p|div|label|h[1-6])>)")
    violations = []
    for path in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{number}")
    assert not violations, "Финальные точки в самостоятельных фразах: " + ", ".join(violations)


if __name__ == "__main__":
    test_task_helper_has_no_terminal_period()
    test_static_block_text_has_no_terminal_period()
    print("UI terminal punctuation tests passed")
