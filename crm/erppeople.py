"""Import visitor statuses and lead cards from selected ERP registers."""
from collections import Counter, defaultdict

from . import erpfull, erpimport as ei, schedule, services as sv, sources, tasks
from .db import normalize_phone


PROFILE_FIELDS = (
    ("child_name", "Имя и фамилия ребёнка"), ("birth", "Дата рождения ребёнка"),
    ("phone", "Телефон родителя"), ("parent_name", "Имя родителя"),
    ("relation", "Роль родителя"), ("email", "Электронная почта"),
    ("source", "Рекламный источник"), ("comment", "Комментарий"),
)


def _clean(value):
    return " ".join(str(value or "").split())


def _historical_lead_branch(label):
    return ei._norm(label) == "пыхтино ркш"


def _matching_branches(con, label):
    def key(value):
        value = ei._norm(value).replace('«', '').replace('»', '')
        return {'ск арктика': 'арктика', 'трц небо': 'тц небо', 'чоботовская 4с2': 'чоботовская'}.get(value, value)
    return [dict(row) for row in con.execute("SELECT id,name,active FROM branches")
            if key(row["name"]) == key(label)]


def _profile_parent(con, family_id, phone=""):
    return con.execute(
        """SELECT * FROM parents WHERE family_id=?
           ORDER BY (phone_norm = ? AND phone_norm != '') DESC, is_primary DESC, id LIMIT 1""",
        (family_id, normalize_phone(phone)),
    ).fetchone()


def _profile_preview(con, sid, rec, visitor_cards=False):
    """Describe safe profile enrichment without changing an existing card."""
    from .smart import guess_relation
    source = {key: _clean(rec.get(key)) for key, _ in PROFILE_FIELDS}
    _channel, source["source"] = sources.canonical_source(rec.get("channel") or "Другое", source["source"])
    source["child_name"] = _clean(ei._split_name(rec))
    source["birth"] = (erpfull._date(rec.get("birth", "")) or "")[:10]
    source["phone"] = _clean(rec.get("phone"))
    source["relation"] = "" if visitor_cards else guess_relation(source["relation"] or source["parent_name"])
    result = {"source": source, "fill": [], "conflicts": [], "missing": [label for key, label in PROFILE_FIELDS if not source[key]]}
    if not sid:
        result["fill"] = [label for key, label in PROFILE_FIELDS if source[key]]
        return result
    student = dict(con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
    parent_row = _profile_parent(con, student["family_id"], source["phone"] if visitor_cards else "")
    parent = dict(parent_row) if parent_row else {}
    current = {
        "child_name": _clean(student.get("name")), "birth": _clean(student.get("birth")),
        "phone": _clean(parent.get("phone")), "parent_name": _clean(parent.get("name")),
        "relation": _clean(parent.get("relation")), "email": _clean(parent.get("email")),
        "source": _clean(student.get("source")), "comment": "",
    }
    if source["comment"]:
        current["comment"] = source["comment"] if con.execute(
            "SELECT 1 FROM history WHERE student_id=? AND kind='note' AND trim(text)=? LIMIT 1", (sid, source["comment"])
        ).fetchone() else ""
    for key, label in PROFILE_FIELDS:
        if not source[key] or current[key] == source[key]:
            continue
        if (visitor_cards and key in {"parent_name", "email"} and normalize_phone(current["phone"])
                and normalize_phone(source["phone"]) and normalize_phone(current["phone"]) != normalize_phone(source["phone"])):
            continue
        if not current[key] or (key == "parent_name" and current[key] == "Родитель") or (key == "child_name" and current[key] == ei.NO_NAME):
            result["fill"].append(label)
        else:
            result["conflicts"].append({"field": label, "crm": current[key], "erp": source[key]})
    return result


def _apply_profile(con, sid, rec, user, visitor_cards=False):
    """Fill only empty profile values; preserve conflicts for explicit review."""
    preview = _profile_preview(con, sid, rec, visitor_cards)
    source = preview["source"]
    student = dict(con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
    if source["child_name"] and student["name"] == ei.NO_NAME:
        con.execute("UPDATE students SET name=? WHERE id=?", (source["child_name"], sid))
    if source["birth"] and not student["birth"]:
        con.execute("UPDATE students SET birth=? WHERE id=?", (source["birth"], sid))
    if source["source"] and not student["source"]:
        channel, name = sources.canonical_source(rec.get("channel") or "Другое", source["source"])
        source_id = sources.ensure_source(con, channel, name)
        con.execute("UPDATE students SET source=?,source_id=? WHERE id=?", (name, source_id, sid))
    parent_row = _profile_parent(con, student["family_id"], source["phone"] if visitor_cards else "")
    if not parent_row:
        con.execute("""INSERT INTO parents(family_id,name,phone,phone_norm,email,relation,is_primary)
                       VALUES (?,?,?,?,?,?,1)""", (student["family_id"], source["parent_name"] or "Родитель", source["phone"],
                                                     normalize_phone(source["phone"]), source["email"], source["relation"]))
    else:
        parent = dict(parent_row); sets, args = [], []
        for column, key in (("name", "parent_name"), ("phone", "phone"), ("email", "email"), ("relation", "relation")):
            if visitor_cards and dict(PROFILE_FIELDS)[key] not in preview["fill"]:
                continue
            if source[key] and (not _clean(parent.get(column)) or (column == "name" and parent[column] == "Родитель")):
                sets.append(column + "=?"); args.append(source[key])
                if column == "phone":
                    sets.append("phone_norm=?"); args.append(normalize_phone(source[key]))
        if sets:
            con.execute(f"UPDATE parents SET {', '.join(sets)} WHERE id=?", (*args, parent["id"]))
    if source["comment"] and not con.execute(
        "SELECT 1 FROM history WHERE student_id=? AND kind='note' AND trim(text)=? LIMIT 1", (sid, source["comment"])
    ).fetchone():
        sv.log(con, sid, source["comment"], {"name": "ERP"}, "note")
    return preview


def _plan(con, visitors, leads, erp_ids=None, visitor_cards=False):
    if not (visitors or leads) or set(visitors) & set(leads):
        raise ei.ImportError_("Выберите отдельно файлы посетителей и лидов")
    if visitor_cards and (leads or erp_ids):
        raise ei.ImportError_("Для массового переноса выберите только файлы посетителей и очистите фильтр ERP ID")
    files = erpfull._files(con, visitors + leads)
    matcher = erpfull.Matcher(con)
    names = defaultdict(set)
    if visitor_cards:
        for sid, student in matcher.students.items():
            names[tuple(sorted(ei._norm(student["name"]).split()))].add(sid)
    requested = {str(value).strip() for value in (erp_ids or []) if str(value).strip()}
    if any(not value.isdigit() for value in requested):
        raise ei.ImportError_("ERP ID должны состоять только из цифр")
    found_requested = set()
    candidates = defaultdict(list)
    counts = Counter()
    issues = []
    for file in files:
        lead = file["id"] in leads
        headers = {ei._norm(h) for h in file["headers"]}
        if file["kind"] != "clients":
            raise ei.ImportError_(f"{file['filename']}: выберите тип файла «Клиенты» в настройках загрузки")
        if lead and not headers.intersection({"дата создания лида", "id заявки", "id лида", "имя ребенка"}):
            raise ei.ImportError_(f"{file['filename']}: не найдены колонки выгрузки лидов")
        if lead and ("статус" in headers or "фио посетителя" in headers):
            raise ei.ImportError_(f"{file['filename']}: выберите выгрузку лидов")
        if not lead and "статус" not in headers:
            raise ei.ImportError_(f"{file['filename']}: в выгрузке посетителей нужна колонка «Статус»")
        status_column = None if lead else next(i for i, h in enumerate(file["headers"]) if ei._norm(h) == "статус")
        for index, row in enumerate(file["rows"], 2):
            where = f"{file['filename']}, строка {index}"
            rec = erpfull._record(file, row)
            if lead:
                fields = {ei._norm(h): v for h, v in zip(file["headers"], row)}
                rec.update(channel=fields.get("канал", ""), territory=fields.get("территория", ""),
                           created=fields.get("дата создания лида", ""), lead_due=fields.get("задача", ""))
            namespace = "lead" if lead else "student"
            if visitor_cards:
                rec["child_name"] = ei._split_name(rec)
            rec["id_namespace"] = namespace
            eid = rec.get("erp_id", "").strip()
            if requested and eid not in requested:
                continue
            if requested and eid:
                found_requested.add(eid)
            status = "Новый лид" if lead else row[status_column].strip()
            counts["leads" if lead else "visitors"] += 1
            if not eid or (not status and not visitor_cards):
                issues.append([where, "Нет ID ERP" if not eid else "Нет статуса в ERP"])
                continue
            candidates[namespace, eid].append((rec, status, where))

    actions = []
    claimed = set()
    incoming_names = Counter(
        tuple(sorted(ei._norm(ei._split_name(rows[0][0])).split()))
        for rows in candidates.values()
    ) if visitor_cards else Counter()
    known_visitors = {target for (namespace, _), target in matcher.identities.items() if namespace == "student"}
    for (namespace, eid), rows in sorted(candidates.items(), key=lambda item: item[0][0] == "lead"):
        lead = namespace == "lead"
        rec, status, where = rows[0]
        if any(other != rec or value != status for other, value, _ in rows[1:]):
            issues.append([where, f"Противоречивые строки для ID {eid}"])
            continue
        counts["repeated_rows"] += len(rows) - 1
        linked = matcher.identities.get((namespace, eid))
        sid = linked
        if not sid:
            sid, reason = matcher.find(rec, "clients")
            # Both ERP records for this visitor were verified against the same family.
            if not lead and not sid and eid == "76621" and not matcher.by_id.get(eid):
                candidates_for_alias = [
                    candidate for candidate in matcher.by_id.get("83867", ())
                    if not matcher.students[candidate]["archived"]
                    and sorted(ei._norm(matcher.students[candidate]["name"]).split()) == ["максим", "мастеренко"]
                    and sorted(ei._norm(ei._split_name(rec)).split()) == ["максим", "мастеренко"]
                    and str(matcher.students[candidate]["birth"])[:10] == "2020-03-27"
                    and erpfull._date(rec.get("birth", "")) == "2020-03-27"
                    and normalize_phone(rec.get("phone", ""))[-10:] == "9265555001"
                    and {"9030083078", "9265555001"} <= matcher.phones[candidate]
                ]
                if len(candidates_for_alias) == 1:
                    sid = candidates_for_alias[0]
            if visitor_cards and not sid and reason == "Не найдено однозначное совпадение":
                name = tuple(sorted(ei._norm(ei._split_name(rec)).split()))
                if not name or not normalize_phone(rec.get("phone", "")) or names.get(name) or incoming_names[name] > 1:
                    issues.append([where, "Нет имени или телефона либо найден тёзка. Сверьте личность"])
                    continue
            elif not sid and (not lead or reason != "Не найдено однозначное совпадение"):
                issues.append([where, reason])
                continue
        if lead and sid and not linked and (sid in claimed or sid in known_visitors or
                                             matcher.students[sid]["funnel"] == "members" or
                                             matcher.students[sid]["erp_id"]):
            sid = None
        if sid and (sid in claimed or (lead and (sid in known_visitors or matcher.students[sid]["funnel"] == "members"))):
            issues.append([where, "Карточка уже связана с другим ID в этой выгрузке или с посетителем ERP"])
            continue
        if sid:
            claimed.add(sid)
        if visitor_cards and not sid and len(sv.find_families_by_phone(con, rec.get("phone", ""))) > 1:
            issues.append([where, "Телефон есть у нескольких семей CRM"])
            continue
        current = matcher.students[sid]["erp_status"] if sid else None
        if visitor_cards:
            rec = {key: value for key, value in rec.items()
                   if key in {"child_name", "birth", "phone", "parent_name", "email", "branch"}}
            branch = _clean(rec.get("branch"))
            matches = _matching_branches(con, branch) if branch else []
            rec["visitor_branch_id"] = matches[0]["id"] if len(matches) == 1 else None
            student = matcher.students[sid] if sid else {}
            existing_branch = student.get("branch_id")
            if not existing_branch and student.get("group_id"):
                group = con.execute("SELECT branch_id FROM groups WHERE id=?", (student["group_id"],)).fetchone()
                existing_branch = group["branch_id"] if group else None
            if branch and not existing_branch and not rec["visitor_branch_id"]:
                issues.append([where, f"Филиал «{branch}» не найден однозначно. Остальные данные можно перенести"])
            rec["visitor_existing_branch"] = existing_branch
            rec["visitor_source_status"] = status
            status = current or status
        counts["new" if sid is None else "same" if current == status else "updated"] += 1
        actions.append((namespace, eid, sid, current, status, rec))
    token = erpfull._hash([[(f["id"], f["digest"]) for f in files], actions, issues])
    profile = {"source_present": Counter(), "source_missing": Counter(), "fill": Counter(), "conflicts": []}
    for namespace, eid, sid, _current, _status, rec in actions:
        preview = _profile_preview(con, sid, rec, visitor_cards)
        fields = PROFILE_FIELDS
        if visitor_cards:
            fields = tuple((key, label) for key, label in fields if key not in {"relation", "source", "comment"})
            fields += (("branch", "Филиал"), ("status", "Статус"))
            preview["source"].update(branch=_clean(rec.get("branch")), status=rec["visitor_source_status"])
            if rec["visitor_branch_id"] and not rec["visitor_existing_branch"]:
                preview["fill"].append("Филиал")
            if rec["visitor_source_status"] and not _current:
                preview["fill"].append("Статус")
        for key, label in fields:
            profile["source_present" if preview["source"][key] else "source_missing"][label] += 1
        profile["fill"].update(preview["fill"])
        profile["conflicts"].extend({"erp_id": eid, "namespace": namespace, **item} for item in preview["conflicts"])
    report = {"counts": dict(counts), "issues": issues, "filter": {
        "requested": sorted(requested), "found": sorted(found_requested),
        "missing": sorted(requested - found_requested)}, "profile": {
        "source_present": dict(profile["source_present"]), "source_missing": dict(profile["source_missing"]),
        "fill": dict(profile["fill"]), "conflicts": profile["conflicts"]}, "token": token}
    labels = sorted({rec["branch"] for namespace, _, _, _, _, rec in actions
                     if namespace == "lead" and rec.get("branch")})
    report["lead_branches"] = [
        {"label": label, "matches": _matching_branches(con, label),
         "inactive": _historical_lead_branch(label)} for label in labels]
    report["token"] = erpfull._hash([token, report["lead_branches"], bool(visitor_cards), profile if visitor_cards else None])
    return report, actions


def transfer(con, visitors, leads, user, token=None, erp_ids=None, visitor_cards=False):
    if any(not isinstance(ids, list) or any(type(uid) is not int for uid in ids) for ids in (visitors, leads)):
        raise ei.ImportError_("Выберите файлы посетителей и лидов")
    report, actions = _plan(con, visitors, leads, erp_ids, visitor_cards)
    if token is None:
        return report
    if token != report["token"]:
        raise ei.ImportError_("Данные изменились. Повторите проверку")
    for namespace, eid, sid, current, status, rec in actions:
        if sid is None:
            phone = rec.get("phone", "").strip()
            normalized = normalize_phone(phone)
            families = sv.find_families_by_phone(con, phone) if normalized else []
            parent = rec.get("parent_name", "").strip() or "Родитель"
            family = families[0]["id"] if len(families) == 1 else None
            if not family:
                family = con.execute("INSERT INTO families(name) VALUES (?)", (parent,)).lastrowid
                con.execute("INSERT INTO parents(family_id,name,phone,phone_norm) VALUES (?,?,?,?)",
                            (family, parent, phone, normalized))
            source_channel, source_name = sources.canonical_source(rec.get("channel") or "Другое", rec.get("source", ""))
            sid = sv.create_student(con, {
                "name": ei._split_name(rec) or ei.NO_NAME, "family_id": family,
                "birth": (erpfull._date(rec.get("birth", "")) or "")[:10],
                "source": source_name,
            }, user)
            if source_name:
                con.execute("UPDATE students SET source_id=? WHERE id=?", (sources.ensure_source(con, source_channel, source_name), sid))
            created = erpfull._date(rec.get("created", ""))
            if created:
                con.execute("UPDATE students SET created_at=? WHERE id=?", (created, sid))
            comment = rec.get("comment", "").strip()
            if comment:
                sv.log(con, sid, comment, user, "note")
        _apply_profile(con, sid, rec, user, visitor_cards)
        if visitor_cards and rec["visitor_branch_id"] and not rec["visitor_existing_branch"]:
            con.execute("UPDATE students SET branch_id=? WHERE id=? AND branch_id IS NULL",
                        (rec["visitor_branch_id"], sid))
        if namespace == "lead":
            con.execute("""INSERT OR IGNORE INTO erp_lead_details
                (source_id, territory, branch, channel) VALUES (?,?,?,?)""",
                (eid, rec.get("territory", ""), rec.get("branch", ""), rec.get("channel", "")))
            branch = _clean(rec.get("branch"))
            if branch:
                matches = _matching_branches(con, branch)
                if len(matches) > 1:
                    raise ei.ImportError_(f"Несколько филиалов соответствуют «{branch}». Сначала уточните соответствие.")
                branch_id = matches[0]["id"] if matches else schedule.save_branch(con, {"name": branch})
                if _historical_lead_branch(branch) and (not matches or matches[0]["active"]):
                    try:
                        schedule.set_branch_active(con, branch_id, False)
                    except schedule.SchedError as exc:
                        raise ei.ImportError_(str(exc)) from exc
                con.execute("UPDATE students SET branch_id=? WHERE id=? AND branch_id IS NULL", (branch_id, sid))
            due = erpfull._date(rec.get("lead_due", ""))
            student = sv.get_student_row(con, sid)
            if student["stage"] == "lead" and student["funnel"] == "sales" and not student["trial_at"] and not student["archived"]:
                if not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='call_lead' LIMIT 1", (sid,)).fetchone():
                    tasks.add_task(con, "call_lead", sid, (due if "T" in due else due + "T23:59") if due else "",
                                   auto_key="erp-lead:" + eid, source="erp",
                                   title="Новый лид" if due else "Новый лид. Даты задачи нет, надо создать")
        if current != status:
            con.execute("UPDATE students SET erp_status=? WHERE id=?", (status, sid))
        con.execute("INSERT OR IGNORE INTO erp_full_identities(namespace,source_id,student_id) VALUES (?,?,?)",
                    (namespace, eid, sid))
        if namespace == "student":
            con.execute("UPDATE students SET erp_id=? WHERE id=? AND erp_id=''", (eid, sid))
    sv.audit(con, user, "erp.people", erpfull._json(report["counts"]))
    return report
