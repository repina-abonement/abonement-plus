from pathlib import Path


def main():
    root = Path(__file__).parents[1]
    app = (root / "crm/static/app.js").read_text(encoding="utf-8")
    profile = (root / "crm/static/clientprofile.js").read_text(encoding="utf-8")
    assert "<label>Статус клиента</label>" in app
    assert "<label>Статус клиента</label>" in profile
    assert "Статус ERP не перенесён" not in app
    assert "Статус ERP не перенесён" not in profile
    assert "Статус не указан" in app and "Статус не указан" in profile
    print("PASS client-facing status wording is source-neutral")


if __name__ == "__main__":
    main()
