"""Import visitor statuses and lead cards from selected ERP registers."""
from collections import Counter, defaultdict

from . import erpfull, erpimport as ei, schedule, services as sv, sources, tasks
from .db import normalize_phone, now_iso


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


def _parent_name_and_role(name):
    from .smart import guess_relation

    words = _clean(name).split()
    if not words:
        return "", ""
    for index in (-1, 0):
        role = guess_relation(words[index].strip("(),:—-"))
        if role:
            return " ".join(words[:-1] if index == -1 else words[1:]).strip(" ,:—-"), role
    return " ".join(words), ""


def _profile_preview(con, sid, rec, visitor_cards=False):
    """Describe safe profile enrichment without changing an existing card."""
    from .smart import guess_relation
    source = {key: _clean(rec.get(key)) for key, _ in PROFILE_FIELDS}
    _channel, source["source"] = sources.canonical_source(rec.get("channel") or "Другое", source["source"])
    source["child_name"] = _clean(ei._split_name(rec))
    source["birth"] = (erpfull._date(rec.get("birth", "")) or "")[:10]
    source["phone"] = _clean(rec.get("phone"))
    if visitor_cards:
        source["parent_name"], source["relation"] = _parent_name_and_role(source["parent_name"])
    else:
        source["relation"] = guess_relation(source["relation"] or source["parent_name"])
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
    if visitor_cards:
        current_name, _role = _parent_name_and_role(current["parent_name"])
        if current_name:
            current["parent_name"] = current_name
    if source["comment"]:
        current["comment"] = source["comment"] if con.execute(
            "SELECT 1 FROM history WHERE student_id=? AND kind='note' AND trim(text)=? LIMIT 1", (sid, source["comment"])
        ).fetchone() else ""
    for key, label in PROFILE_FIELDS:
        if not source[key] or current[key] == source[key]:
            continue
        if visitor_cards and key == "parent_name":
            crm_name, erp_name = ei._norm(current[key]), ei._norm(source[key])
            if {crm_name, erp_name} == {"лилия", "лиля"}:
                continue
        if key == "child_name" and ei._norm(current[key]) == ei._norm(source[key]):
            continue
        if key == "phone" and normalize_phone(source[key]) and normalize_phone(current[key]) == normalize_phone(source[key]):
            continue
        if (visitor_cards and key in {"parent_name", "email", "relation"} and normalize_phone(current["phone"])
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


# Pairs confirmed by the owner. Each ERP ID remains available for later imports.
EXCLUDED_VISITOR_IDS = {"62805", "62953"}
DISTINCT_VISITOR_IDS = {"92940", "132829", "95513", "114710", "108850", "110708"}

VISITOR_PAIRS = (
    ("68349", "99699"), ("100563", "82964"), ("113503", "119677"),
    ("89227", "68726"), ("128139", "98809"), ("122798", "116222"),
)


def _confirmed_pair(con, matcher, rows):
    records = [row[0] for row in rows]
    names = [tuple(sorted(ei._norm(rec["child_name"]).split())) for rec in records]
    dates = [erpfull._date(rec.get("birth", "")) for rec in records]
    phones = [normalize_phone(rec.get("phone", "")) for rec in records]
    if names[0] != names[1] or len(names[0]) < 2 or not dates[0] or dates[0] != dates[1] or not all(phones):
        raise ValueError("Подтверждённая пара ERP изменилась")
    targets = set()
    families = set()
    ids = {rec["erp_id"].strip() for rec in records}
    for rec, phone in zip(records, phones):
        eid = rec["erp_id"].strip()
        targets.update(matcher.by_id.get(eid, ()))
        linked = matcher.identities.get(("student", eid))
        if linked:
            targets.add(linked)
        targets.update(matcher.by_np.get((names[0], phone[-10:]), ()))
        families.update(family["id"] for family in sv.find_families_by_phone(con, phone))
    if len(targets) > 1:
        raise ValueError("Подтверждённая пара уже связана с разными карточками CRM")
    if len(families) > 1:
        raise ValueError("Телефоны подтверждённой пары находятся в разных семьях CRM")
    if targets:
        sid = next(iter(targets))
        card = matcher.students[sid]
        if (card["archived"] or (card["erp_id"] and card["erp_id"] not in ids)
                or tuple(sorted(ei._norm(card["name"]).split())) != names[0]
                or (card["birth"] and card["birth"][:10] != dates[0][:10])
                or families - {card["family_id"]}):
            raise ValueError("Связь подтверждённой пары с карточкой CRM требует проверки")
        return sid, next(iter(families), card["family_id"])
    return None, next(iter(families), None)


def _plan(con, visitors, leads, erp_ids=None, visitor_cards=False):
    if not (visitors or leads) or set(visitors) & set(leads):
        raise ei.ImportError_("Выберите отдельно файлы посетителей и лидов")
    if visitor_cards and leads:
        raise ei.ImportError_("Для массового переноса выберите только файлы посетителей")
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
            if not lead and eid in EXCLUDED_VISITOR_IDS:
                counts["excluded"] += 1
                continue
            if not eid or (not status and not visitor_cards):
                issues.append([where, "Нет ID ERP" if not eid else "Нет статуса в ERP"])
                continue
            candidates[namespace, eid].append((rec, status, where))

    pairs = {}
    if visitor_cards:
        for first, second in VISITOR_PAIRS:
            keys = [("student", first), ("student", second)]
            if not all(key in candidates for key in keys):
                continue
            left, right = (candidates[key] for key in keys)
            try:
                if len(left) != 1 or len(right) != 1:
                    raise ValueError("В подтверждённой паре ERP обнаружены повторные строки")
                sid, family = _confirmed_pair(con, matcher, [left[0], right[0]])
            except ValueError as error:
                issues.extend([rows[0][2], str(error)] for rows in (left, right))
                for key in keys:
                    del candidates[key]
                continue
            pairs[first] = (sid, family, right[0][0], right[0][1])
            del candidates[keys[1]]
            counts["repeated_rows"] += 1

    actions = []
    claimed = set()
    incoming_names = Counter(
        tuple(sorted(ei._norm(ei._split_name(rows[0][0])).split()))
        for rows in candidates.values()
    ) if visitor_cards else Counter()
    incoming_name_phones = Counter(
        (tuple(sorted(ei._norm(ei._split_name(rows[0][0])).split())),
         normalize_phone(rows[0][0].get("phone", "")))
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
        pair = pairs.get(eid)
        linked = matcher.identities.get((namespace, eid))
        sid = pair[0] if pair else linked
        if not sid and not pair:
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
                phone = normalize_phone(rec.get("phone", ""))
                given_name_only = (len(name) == 1 and name[0].replace("-", "").isalpha()
                                   and name[0] not in {"лид", "уточнить", "неизвестно", "ребенок", "тест"})
                if given_name_only:
                    ambiguous = incoming_name_phones[name, phone] > 1 or any(
                        name[0] in candidate_name and phone[-10:] == candidate_phone
                        for candidate_name, candidate_phone in matcher.by_np
                    )
                else:
                    ambiguous = eid not in DISTINCT_VISITOR_IDS and (bool(names.get(name)) or incoming_names[name] > 1)
                if not name or not phone or ambiguous:
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
            if pair:
                rec["visitor_pair"] = pair[2] | {"erp_status": pair[3]}
                rec["visitor_pair_family"] = pair[1]
                rec["branch"] = "; ".join(filter(None, (rec.get("branch"), pair[2].get("branch"))))
            branch = _clean(rec.get("branch"))
            matches = _matching_branches(con, branch) if branch else []
            rec["visitor_branch_id"] = matches[0]["id"] if len(matches) == 1 else None
            student = matcher.students[sid] if sid else {}
            existing_branch = student.get("branch_id")
            if not existing_branch and student.get("group_id"):
                group = con.execute("SELECT branch_id FROM groups WHERE id=?", (student["group_id"],)).fetchone()
                existing_branch = group["branch_id"] if group else None
            labels = list(dict.fromkeys(_clean(label) for label in branch.split(";") if _clean(label)))
            rec["visitor_branch_labels"] = (
                "; ".join(labels) if len(labels) > 1 and not existing_branch and not student.get("erp_branch_labels") else ""
            )
            if len(labels) == 1:
                matches = _matching_branches(con, labels[0])
                rec["visitor_branch_id"] = matches[0]["id"] if len(matches) == 1 else None
            if branch and len(labels) <= 1 and not existing_branch and not rec["visitor_branch_id"]:
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
            fields = tuple((key, label) for key, label in fields if key not in {"source", "comment"})
            fields += (("branch", "Филиал"), ("status", "Статус"))
            preview["source"].update(branch=_clean(rec.get("branch")), status=rec["visitor_source_status"])
            if rec["visitor_branch_labels"] or (rec["visitor_branch_id"] and not rec["visitor_existing_branch"]):
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
            parent = rec.get("parent_name", "").strip()
            if visitor_cards:
                parent, _role = _parent_name_and_role(parent)
            parent = parent or "Родитель"
            family = rec.get("visitor_pair_family") or (families[0]["id"] if len(families) == 1 else None)
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
        if visitor_cards and rec.get("visitor_pair"):
            alternate = rec["visitor_pair"]
            family = sv.get_student_row(con, sid)["family_id"]
            for profile in (rec, alternate):
                phone = profile.get("phone", "")
                normalized = normalize_phone(phone)
                if not con.execute("SELECT 1 FROM parents WHERE family_id=? AND phone_norm=?", (family, normalized)).fetchone():
                    parent, role = _parent_name_and_role(profile.get("parent_name"))
                    con.execute("""INSERT INTO parents(family_id,name,phone,phone_norm,email,relation)
                        VALUES (?,?,?,?,?,?)""",
                        (family, parent or "Родитель", phone, normalized, profile.get("email", ""), role))
            _apply_profile(con, sid, {key: alternate.get(key) for key in ("child_name", "birth", "phone", "parent_name", "email")}, user, True)
            alias = alternate["erp_id"].strip()
            con.execute("INSERT OR IGNORE INTO erp_full_identities(namespace,source_id,student_id) VALUES ('student',?,?)",
                        (alias, sid))
            note = f"ERP ID {alias}: {alternate['erp_status'] or 'статус не указан'}"
            if not con.execute("SELECT 1 FROM history WHERE student_id=? AND kind='note' AND text=?", (sid, note)).fetchone():
                sv.log(con, sid, note, {"name": "ERP"}, "note")
        _apply_profile(con, sid, rec, user, visitor_cards)
        if visitor_cards and rec["visitor_branch_id"] and not rec["visitor_existing_branch"]:
            con.execute("UPDATE students SET branch_id=? WHERE id=? AND branch_id IS NULL",
                        (rec["visitor_branch_id"], sid))
        if visitor_cards and rec["visitor_branch_labels"]:
            con.execute(
                "UPDATE students SET erp_branch_labels=? WHERE id=? AND branch_id IS NULL AND erp_branch_labels=''",
                (rec["visitor_branch_labels"], sid),
            )
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
        if visitor_cards and not sv.get_student_row(con, sid)["archived"]:
            tasks.queue_erp_visitor(con, sid)
    if visitor_cards:
        con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
            result='Карточка ожидает переноса задач и абонементов ERP'
            WHERE kind='client_followup' AND source='coverage' AND status='open'
            AND student_id IN (SELECT student_id FROM tasks WHERE kind='erp_visitor_pending' AND status='open')""",
            (now_iso(),))
    sv.audit(con, user, "erp.people", erpfull._json(report["counts"]))
    return report


def apply_confirmed_visitor_pairs(con):
    """Apply only owner-confirmed pairs from the already uploaded register."""
    marker = "confirmed_visitor_pairs_20261127"
    saved = con.execute("SELECT value FROM settings WHERE key=?", (marker,)).fetchone()
    if saved:
        return saved["value"]
    upload = con.execute("""SELECT id FROM erp_uploads
        WHERE filename='Посетители (41).xlsx' AND kind='clients' ORDER BY id DESC LIMIT 1""").fetchone()
    if not upload:
        return "source-not-uploaded"
    user = {"name": "CRM", "role": "admin"}
    completed = 0
    for pair in VISITOR_PAIRS:
        report, actions = _plan(con, [upload["id"]], [], pair, visitor_cards=True)
        if report["issues"] or len(actions) != 1 or set(report["filter"]["found"]) != set(pair):
            continue
        transfer(con, [upload["id"]], [], user, report["token"], pair, visitor_cards=True)
        completed += 1
    if completed == len(VISITOR_PAIRS):
        con.execute("INSERT INTO settings(key,value) VALUES (?, 'complete')", (marker,))
        return "complete"
    return f"review-required-{completed}-of-{len(VISITOR_PAIRS)}"
