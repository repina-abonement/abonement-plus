from pathlib import Path


css = (Path(__file__).parents[1] / "crm/static/style.css").read_text()

assert "html.theme-soft .week-board .col{max-height:none;overflow:hidden}" in css
assert ".week-board .pcard{max-width:100%;box-sizing:border-box}" in css
