"""Личные контрольные блоки руководителя не попадают на стол команды или в «Как видит стол»."""
from pathlib import Path
import re


def main():
    source = (Path(__file__).parents[1] / "crm/static/app.js").read_text(encoding="utf-8")
    block = re.search(r"const ORDER = \{(?P<body>.*?)\n  \}\[who\];", source, re.S)
    assert block
    rows = {name: values for name, values in re.findall(r'(owner|control|operator): \[(.*?)\]', block.group("body"))}
    assert '"merch"' in rows["owner"] and '"spend"' in rows["owner"]
    assert '"merch"' not in rows["control"] and '"spend"' not in rows["control"]
    assert '"merch"' not in rows["operator"] and '"spend"' not in rows["operator"]
    print("ok: merch and payments widgets are owner-only")


if __name__ == "__main__":
    main()
