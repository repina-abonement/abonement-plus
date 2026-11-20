"""Личные контрольные блоки руководителя не попадают на стол команды или в «Как видит стол»."""
from pathlib import Path
import re


def main():
    source = (Path(__file__).parents[1] / "crm/static/app.js").read_text(encoding="utf-8")
    block = re.search(r"const ORDER = \{(?P<body>.*?)\n  \}\[who\];", source, re.S)
    assert block
    rows = {name: values for name, values in re.findall(r'(owner|control|operator|offline): \[(.*?)\]', block.group("body"))}
    assert '"merch"' in rows["owner"] and '"spend"' in rows["owner"]
    assert '"merch"' not in rows["control"] and '"spend"' not in rows["control"]
    assert '"merch"' not in rows["operator"] and '"spend"' not in rows["operator"]
    assert '"merch"' not in rows["offline"] and '"spend"' not in rows["offline"]
    assert 'deskUser.is_offline_admin ? "offline"' in source
    assert 'deskUser.is_senior || d.checkins ? "control"' in source
    task_source = (Path(__file__).parents[1] / "crm/static/erptasks.js").read_text(encoding="utf-8")
    assert 'as_user=' in task_source and 'encodeURIComponent(asUser.id)' in task_source
    print("ok: merch and payments widgets are owner-only")


if __name__ == "__main__":
    main()
