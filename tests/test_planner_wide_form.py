"""Quick planner actions must use the wide form above the weekly board."""
from pathlib import Path


root = Path(__file__).resolve().parents[1]
javascript = (root / "crm/static/planner.js").read_text()
styles = (root / "crm/static/style.css").read_text()

quick = javascript[javascript.index('// Быстрое добавление'):javascript.index("const eventForm=", javascript.index('// Быстрое добавление'))]
assert "const form=$('#pl-form')" in quick
assert "button.after(form)" not in quick
assert "document.createElement('div')" not in quick
assert ".planner-day-form .row" not in styles
print("ok")
