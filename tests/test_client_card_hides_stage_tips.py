from pathlib import Path


def test_client_cards_do_not_render_header_hints_or_task_banner():
    app = (Path(__file__).parents[1] / "crm" / "static" / "app.js").read_text(encoding="utf-8")

    assert "Подсказка на этапе" not in app
    assert 'querySelector(".stage-tip")' not in app
    assert "d.stage_tip.say" not in app
    assert "d.stage_tip.next" not in app
    assert '<div class="next-step">' not in app
    assert 'id="ns-go"' not in app
    assert "const NEXT_GO" not in app


if __name__ == "__main__":
    test_client_cards_do_not_render_header_hints_or_task_banner()
    print("ok: подсказки и дублирующий блок задачи не выводятся в шапке карточек")
