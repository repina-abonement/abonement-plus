from pathlib import Path


def test_client_cards_do_not_render_stage_tips():
    app = (Path(__file__).parents[1] / "crm" / "static" / "app.js").read_text(encoding="utf-8")

    assert "Подсказка на этапе" not in app
    assert 'querySelector(".stage-tip")' not in app
    assert "d.stage_tip.say" not in app
    assert "d.stage_tip.next" not in app


if __name__ == "__main__":
    test_client_cards_do_not_render_stage_tips()
    print("ok: подсказки этапа не выводятся в карточках клиентов")
