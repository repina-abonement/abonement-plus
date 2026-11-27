"""Вместимость групп в дашборде по подтверждённым правилам филиалов."""
import re


def seats(branch, group, fallback):
    """Места в группе CRM или в группе, указанной только названием в ERP."""
    branch_key = " ".join(re.findall(r"[\w]+", (branch or "").casefold()))
    if branch_key in {"чоботовская", "чоботовская 4с2", "ск западный", "ск западный чоботовская", "западный"}:
        return 12
    if branch_key in {"арктика", "ск арктика"}:
        return 14
    if branch_key not in {"небо", "тц небо", "трц небо"}:
        return fallback

    group = dict(group)
    label = " ".join(str(group.get(key) or "") for key in ("name", "source_name")).casefold()
    days = group.get("weekdays")
    if days:
        days = days.split(",") if isinstance(days, str) else days
        mon_wed = {str(d).strip() for d in days} == {"0", "2"}
    else:
        mon_wed = set(re.findall(r"\b(?:пн|вт|ср|чт|пт|сб|вс)\b", label)) == {"пн", "ср"}
    if mon_wed and not group.get("program_id") and not group.get("is_makeup"):
        age = str(group.get("age") or "")
        toddlers = r"(?<!\d)1[.,]5\s*[-–—]\s*3(?!\d)|\bмалыш"
        if re.search(toddlers, age + " " + label):
            return 8
        # The morning group first ran at 10:00 and moved to 10:30 on 18 November.
        if not age and group.get("time") in {"10:00", "10:30"}:
            return 8
    return 10
