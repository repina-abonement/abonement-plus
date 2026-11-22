from pathlib import Path


def main():
    app = (Path(__file__).parents[1] / "crm/static/app.js").read_text(encoding="utf-8")
    assert "consentHtml(d.family)" not in app
    assert "wireConsent(d.family" not in app
    print("PASS consent block is hidden in every client card")


if __name__ == "__main__":
    main()
