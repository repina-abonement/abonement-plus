"""Dashboard month selector includes December while the current month is November."""
from pathlib import Path


source = (Path(__file__).parents[1] / "crm" / "static" / "app.js").read_text()
assert "function dashboardMonths()" in source
assert "for (let offset = 3; offset >= -17; offset--)" in source
assert source.count("dashboardMonths();") == 2
assert "dt.setMonth(dt.getMonth() + offset)" in source
print("ok: dashboard includes future months in overview and analytics tabs")
