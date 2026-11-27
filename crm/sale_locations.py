"""Locations indexed by source file and Excel row in the verified ERP history."""
import json
from pathlib import Path
from functools import lru_cache

from . import erpimport, erppeople


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name("erp_sale_locations.json").read_text(encoding="utf-8"))


def branch_ids(con):
    result = {}
    for i, name in enumerate(sorted({b for b, _ in catalog()["labels"]}), 1):
        candidates = erppeople._matching_branches(con, name)
        if len(candidates) > 1:
            # Duplicate branch records can survive a schedule rename. Attribute only
            # when exactly one candidate owns groups and the others have none.
            scheduled = [b for b in candidates if con.execute(
                "SELECT 1 FROM groups WHERE branch_id=? AND is_makeup=0 LIMIT 1", (b["id"],)).fetchone()]
            if len(scheduled) == 1:
                candidates = scheduled
        result[name] = candidates[0]["id"] if len(candidates) == 1 else -i
    return result


def branch_names(con):
    names = dict(con.execute("SELECT id,name FROM branches"))
    names.update({bid: name for name, bid in branch_ids(con).items() if bid < 0})
    return names


def resolver(con):
    data = catalog()
    branches = branch_ids(con)

    @lru_cache(None)
    def resolve(index):
        branch, label = data["labels"][index]
        bid = branches[branch]
        if not label or bid < 0:
            return bid, None
        mapped = erpimport._group_id(con, branch, label)
        def norm(value):
            return erpimport._norm(value or "").removeprefix("стоп ").strip()
        matches = [gid for gid, name, source in con.execute(
            "SELECT id,name,source_name FROM groups WHERE branch_id=? AND is_makeup=0", (bid,))
            if (gid == mapped if mapped else norm(label) in (norm(name), norm(source)))]
        return bid, matches[0] if len(matches) == 1 else None

    def lookup(key):
        file, _, row = key.partition("/")
        if file in data["files"] and row.isdecimal():
            start, indexes = data["files"][file]
            offset = int(row) - start
            if 0 <= offset < len(indexes):
                return resolve(indexes[offset])
        return None, None
    return lookup
