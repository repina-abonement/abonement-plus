"""Import visitor statuses and lead cards from selected ERP registers."""
from collections import Counter, defaultdict

from . import erpfull, erpimport as ei, services as sv
from .db import normalize_phone


def _plan(con, visitors, leads):
    if not visitors or not leads or set(visitors) & set(leads):
        raise ei.ImportError_("Выберите отдельно файлы посетителей и лидов")
    files = erpfull._files(con, visitors + leads)
    matcher = erpfull.Matcher(con)
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
            namespace = "lead" if lead else "student"
            rec["id_namespace"] = namespace
            eid = rec.get("erp_id", "").strip()
            status = "Новый лид" if lead else row[status_column].strip()
            counts["leads" if lead else "visitors"] += 1
            if not eid or not status:
                issues.append([where, "Нет ID ERP" if not eid else "Нет статуса в ERP"])
                continue
            candidates[namespace, eid].append((rec, status, where))

    actions = []
    claimed = set()
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
            if not sid and (not lead or reason != "Не найдено однозначное совпадение"):
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
        current = matcher.students[sid]["erp_status"] if sid else None
        counts["new" if sid is None else "same" if current == status else "updated"] += 1
        actions.append((namespace, eid, sid, current, status, rec))
    token = erpfull._hash([[(f["id"], f["digest"]) for f in files], actions, issues])
    report = {"counts": dict(counts), "issues": issues, "token": token}
    return report, actions


def transfer(con, visitors, leads, user, token=None):
    if any(not isinstance(ids, list) or any(type(uid) is not int for uid in ids) for ids in (visitors, leads)):
        raise ei.ImportError_("Выберите файлы посетителей и лидов")
    report, actions = _plan(con, visitors, leads)
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
            sid = sv.create_student(con, {
                "name": ei._split_name(rec) or ei.NO_NAME, "family_id": family,
                "birth": (erpfull._date(rec.get("birth", "")) or "")[:10],
                "source": rec.get("source", ""),
            }, user)
            created = erpfull._date(rec.get("created", ""))
            if created:
                con.execute("UPDATE students SET created_at=? WHERE id=?", (created, sid))
            comment = rec.get("comment", "").strip()
            if comment:
                sv.log(con, sid, comment, user, "note")
        if current != status:
            con.execute("UPDATE students SET erp_status=? WHERE id=?", (status, sid))
        con.execute("INSERT OR IGNORE INTO erp_full_identities(namespace,source_id,student_id) VALUES (?,?,?)",
                    (namespace, eid, sid))
        if namespace == "student":
            con.execute("UPDATE students SET erp_id=? WHERE id=? AND erp_id=''", (eid, sid))
    sv.audit(con, user, "erp.people", erpfull._json(report["counts"]))
    return report
