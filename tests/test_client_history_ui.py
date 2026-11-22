from pathlib import Path


def main():
    root = Path(__file__).parents[1]
    app = (root / "crm/static/app.js").read_text(encoding="utf-8")
    css = (root / "crm/static/style.css").read_text(encoding="utf-8")
    assert "function clientHistoryHtml" in app
    assert "Технические данные переноса ERP" in app
    assert "Сохранены для проверки переноса" in app
    assert "clientHistoryHtml(d.history)" in app
    assert "client-history-event" in css and "client-history-technical" in css
    print("PASS concise client history and separate ERP transfer data")


if __name__ == "__main__":
    main()
