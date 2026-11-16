"""Phone format/fragment search in contacts and chat text without changing identity data."""
import tempfile
from pathlib import Path
from crm import db,services as sv,people,comms,smart,phonesearch


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'phone-search.sqlite3'));db.init_db(con);smart.REQUIRE=False
    u={'id':1,'role':'admin','name':'Тест'}
    sid=sv.create_student(con,{'name':'Тестовый Ребёнок','parent_name':'Анна','phone':'+7 (900) 123-45-67'},u)
    fid=con.execute('SELECT family_id FROM students WHERE id=?',(sid,)).fetchone()[0]
    def chat(key,**fields):
        cid=con.execute("INSERT INTO conversations(channel,external_id,title,phone,phone_norm,family_id,created_at) VALUES ('telegram',?,?,?,?,?,'2026-11-16')",(key,fields.get('title','Диалог'),fields.get('phone',''),fields.get('phone_norm',''),fields.get('family_id'))).lastrowid
        return cid
    c1=chat('contact',phone='+7 (900) 123-45-67',phone_norm='79001234567')
    c2=chat('linked',family_id=fid)
    c3=chat('text-only')
    con.execute("INSERT INTO messages(conversation_id,direction,text,ts) VALUES (?,'in',?,'2026-11-16')",(c3,'Заявка\nТелефон: +7 (900) 123-45-67\nГород: Москва'))
    wrong=chat('wrong',phone_norm='79000000999')
    before={table:[tuple(r) for r in con.execute('SELECT * FROM '+table)] for table in ('parents','conversations','messages')}
    cases=['+79001234567','79001234567','89001234567','8 (900) 123-45-67','9001234567','900 123','8 (900) 123','123-45-67','45 67','+7\u00a0(900)\u202f123–45—67','+７９００１２３４５６７','7/900/123/45/67']
    for q in cases:
        assert [r['id'] for r in people.listing(con,q=q)['rows']]==[sid],q
        assert [r['id'] for r in sv.search_students(con,q)]==[sid],q
        ids={r['id'] for r in comms.inbox(con,q=q)['conversations']}
        assert {c1,c2,c3}<=ids and wrong not in ids,(q,ids)
    assert all([tuple(r) for r in con.execute('SELECT * FROM '+table)]==rows for table,rows in before.items())
    date_chat=chat('only-date')
    con.execute("INSERT INTO messages(conversation_id,direction,text,ts) VALUES (?,'in','Встреча 12.11.2026, сумма 1200 рублей','2026-11-16')",(date_chat,))
    assert date_chat not in {r['id'] for r in comms.inbox(con,q='12')['conversations']}
    assert date_chat in {r['id'] for r in comms.inbox(con,q='Встреча')['conversations']}
    # Snapshot after the explicit fixture insertion, before the read-only searches.
    before={table:[tuple(r) for r in con.execute('SELECT * FROM '+table)] for table in ('parents','conversations','messages')}
    # Every two-digit combination is searchable at its actual position in the number.
    number='79001234567'
    for q in {number[i:i+2] for i in range(len(number)-1)}|{'89'}:
        assert sid in {r['id'] for r in people.listing(con,q=q)['rows']},q
        assert sid in {r['id'] for r in sv.search_students(con,q)},q
        assert {c1,c2,c3}<={r['id'] for r in comms.inbox(con,q=q)['conversations']},q
    assert not phonesearch.matches('79001237567','85') # A body fragment 75 is not 85.
    assert not phonesearch.matches('79001234567','13') # Separate digits do not form a match.
    assert all([tuple(r) for r in con.execute('SELECT * FROM '+table)]==rows for table,rows in before.items())
    # Raw legacy phone without canonical field remains searchable; identity normalizer is unchanged.
    con.execute("UPDATE parents SET phone_norm='' WHERE family_id=?",(fid,))
    assert sv.search_students(con,'89001234567')[0]['id']==sid
    assert people.listing(con,q='1234567')['rows'][0]['id']==sid
    assert comms.inbox(con,q='1234567')['conversations']
    assert [r['id'] for r in sv.search_students(con,'Тестовый')]==[sid]
    assert phonesearch.matches(None,'123')==0 and phonesearch.matches('79001234567','')==0
    assert phonesearch.matches('123 слово 456','123456')==0
    assert not phonesearch.matches('79001234567','99999999')
    assert db.normalize_phone('9001234567')=='79001234567'
    assert len(comms.inbox(con,q='Заявка')['conversations'])==1
    # Search does not rewrite records or mark conversations/messages read.
    assert con.execute('SELECT text FROM messages WHERE conversation_id=?',(c3,)).fetchone()[0].startswith('Заявка')
    print('PASS full/partial/punctuated/7/8/Unicode phone search in people, card lookup, inbox contacts/linked parents/message text; legacy phone and name search; identity normalization unchanged')

if __name__=='__main__':main()
