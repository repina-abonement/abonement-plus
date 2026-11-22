"""Краткое отображение ERP-событий; исходная запись не меняется."""
import re

_LABELS=re.compile(r'(?:^|\s)(Дата события|Дедлайн|Задача \(END\)|Задача \(NEW\)|Событие|Автор|Исполнитель|Фамилия и имя|Филиал|Группа|Комментарий|ID|№)\s*:\s*',re.I)
_ERP_ARCHIVE = re.compile(r'^Архив\s+ERP\s+([^·.\n]+)\s*·\s*([^\.\n]+)', re.I)


def present(row):
    out=dict(row);raw=out.get('text') or ''
    archive = _ERP_ARCHIVE.match(raw.strip())
    if archive:
        out.update(display_text=archive.group(2).strip(), technical_text=raw,
                   technical_only=True, display_author='ERP')
        return out
    if not re.match(r'^ERP(?:,|\s)',raw):return out
    found=list(_LABELS.finditer(raw));fields={}
    for i,m in enumerate(found):fields[m.group(1).casefold()]=raw[m.end():found[i+1].start() if i+1<len(found) else len(raw)].strip()
    title=fields.get('задача (new)') or fields.get('задача (end)') or ''
    event=fields.get('событие','');deadline=fields.get('дедлайн','')
    if 'перенес' in event.casefold() or 'перенос' in event.casefold():summary='Перенесён срок'+(f' задачи «{title}»' if title else '')
    else:summary=event or 'Событие из ERP';summary+=(f': «{title}»' if title else '')
    if deadline:summary+=' — '+deadline.split()[0]
    old=fields.get('задача (end)')
    if old and title and old!=title:summary+=f' (ранее «{old}»)'
    comment=fields.get('комментарий','')
    comment=re.sub(r'(?:#|№)\s*\d+','',comment).strip()
    if comment:summary+='\n'+comment
    out.update(display_text=summary,technical_text=raw,
               display_author=fields.get('автор') or out.get('user_name',''),technical_only=False)
    return out
