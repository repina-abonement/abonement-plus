"""Памятки: тексты без опечаток. Запуск: python -m tests.test_memos"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import memos  # noqa: E402
from tests.test_crm import check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    want = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    for m, w in enumerate(want, 1):
        check(memos._from(f"2026-{m:02d}") == f"с 1 {w}", f"«с 1 {w}»")
    # все строки памяток, собранные из memos.js на примерах: без длинного тире, без пустых строк и двойных пробелов
    js = open(os.path.join(ROOT, "crm/static/memos.js"), encoding="utf-8").read()
    check("—" not in js, "В памятках нет длинного тире")
    src = js[js.index("const cap ="):js.index("async function drawMemo")]
    samples = [
        {"kind": "regular", "title": "Небо", "deadline_day": 25, "fixed": 1350, "flex": 1550, "fixed8": 10800, "flex8": 12400,
         "credit": 3, "late_pct": 5, "makeups": ["Небо: Сб 14:00"], "next": {"from": memos._from("2026-12"), "fixed": 1400, "flex": 1600}},
        {"kind": "program", "title": "Арктика", "deadline_day": 25, "arena": 1500, "ofp": 1200, "arena_only": 1700, "late_pct": 0,
         "arena_sched": "", "ofp_sched": "", "makeups": [], "next": {"from": memos._from("2026-12"), "arena": 1600, "ofp": 1200, "arena_only": 1800}},
    ]
    code = src + f"\nconsole.log(JSON.stringify({json.dumps(samples, ensure_ascii=False)}.map(memoLines)));"
    try:
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        print("ok (node нет, проверка текстов пропущена)")
        return
    for blocks in json.loads(out):
        lines = [b["h"] for b in blocks] + [s for b in blocks for s in b["t"]]
        lines += [s for b in blocks for t in b.get("tariffs", []) for s in [t["name"], *t["t"]]]
        for s in lines:
            check(s and s == s.strip() and "  " not in s and "undefined" not in s and "NaN" not in s, f"Строка без мусора: {s!r}")
            check(not re.search(r"\bс (январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)\b", s, re.I),
                  f"Месяц в родительном падеже: {s!r}")
        for b in blocks:
            check(b["t"] or b.get("tariffs"), f"Блок «{b['h']}» не пустой")
    print("ok")


if __name__ == "__main__":
    main()
