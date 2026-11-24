from pathlib import Path


js = (Path(__file__).parents[1] / "crm/static/app.js").read_text()
server = (Path(__file__).parents[1] / "crm/server.py").read_text()
assert "Массовый перенос СЗ и ТРЕНЕРЫ с 1 ноября" in js
assert "/api/import/chat-bulk/plan" in js
assert "/api/import/chat-bulk/apply" in js
assert "Что именно будет перенесено" in js
assert "новая карточка из ERP" in js
assert "Скачать отчёт проверки" in js
assert "Проверка-переноса-" in js
assert "Неразобранные сообщения СЗ не помечаются завершёнными" in js
assert 'url.path.startswith("/api/import/chat-bulk")' in server
print("ok")
