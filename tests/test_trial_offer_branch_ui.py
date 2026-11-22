from pathlib import Path


def test_trial_offer_branch_uses_reliable_native_selector():
    script = (Path(__file__).parents[1] / "crm/static/comms.js").read_text(encoding="utf-8")
    assert '<select id="to-br" data-search-select="off">' in script
    assert '<option value="">Любой, где раньше</option>' in script
    assert '"&branch_id=" + $("#to-br").value' in script
