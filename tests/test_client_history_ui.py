from pathlib import Path


def main():
    root = Path(__file__).parents[1]
    app = (root / "crm/static/app.js").read_text(encoding="utf-8")
    css = (root / "crm/static/style.css").read_text(encoding="utf-8")
    assert "function clientHistoryHtml" in app
    assert "Технические данные переноса ERP" not in app
    assert "Исходная запись ERP</summary>" not in app
    from crm.historyview import present
    assert present({"kind":"note","text":"Выгрузка ERP 123 · Счета"})["technical_only"]
    assert present({"kind":"task","text":"Следующий шаг ERP: закрыть; перенос истории"})["technical_only"]
    assert not present({"kind":"note","text":"Семья сообщила о пропуске"}).get("technical_only")
    assert "clientHistoryHtml(d.history)" in app
    assert "client-history-event" in css and "client-history-technical" in css
    print("PASS concise client history and separate ERP transfer data")


if __name__ == "__main__":
    main()
