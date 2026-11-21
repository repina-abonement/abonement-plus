from pathlib import Path


root = Path(__file__).resolve().parents[1]
dashboard_ui = (root / "crm/static/app.js").read_text(encoding="utf-8")
payments_ui = (root / "crm/static/money.js").read_text(encoding="utf-8")

assert "Поступления и чеки: требуют внимания" not in dashboard_ui
assert "Поступления и чеки" in payments_ui
assert "incoming-control" in payments_ui

print("ok: dashboard hides the payment control banner; payments keeps its details")
