"""Dashboard seats in the CRM schedule, scoped to the month and sales locations."""


def group_places(con, month, sale_groups):
    """Include empty scheduled groups and archived groups with activity in the month."""
    places = {}
    for gid, bid, active, started, capacity in con.execute(
            "SELECT id,branch_id,active,started_on,capacity FROM groups WHERE is_makeup=0"):
        if started and started[:7] > month:
            continue
        if active or gid in sale_groups:
            places[gid] = (bid, capacity)
    return places


def total(places, branch_id, group_id, branches_with_sales):
    selected = [(bid, cap) for gid, (bid, cap) in places.items()
                if (not branch_id or bid == branch_id) and (not group_id or gid == group_id)]
    if not selected and branches_with_sales:
        return None
    return sum(cap for _, cap in selected)
