from pathlib import Path


css = (Path(__file__).parents[1] / "crm/static/style.css").read_text()
javascript = (Path(__file__).parents[1] / "crm/static/planner.js").read_text()

assert "html.theme-soft .week-board .col{max-height:none;overflow:hidden}" in css
assert ".week-board .pcard{max-width:100%;box-sizing:border-box}" in css
assert "/api/planner/week?days=7&start=" in javascript
assert "const end = addDays(w.start, 6)" in javascript
assert "/api/planner/week?days=4&start=" not in javascript
