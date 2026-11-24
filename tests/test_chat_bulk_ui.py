from pathlib import Path


js = (Path(__file__).parents[1] / "crm/static/app.js").read_text()
assert "Массовый перенос СЗ и ТРЕНЕРЫ с 1 ноября" in js
assert "/api/import/chat-bulk/plan" in js
assert "/api/import/chat-bulk/apply" in js
assert "Неразобранные сообщения СЗ не помечаются завершёнными" in js
print("ok")
