"""Payment channel fidelity without guessing historical card/QR providers."""
import tempfile
import unittest
from pathlib import Path
from crm import db,money,services as sv


class PaymentChannelsTest(unittest.TestCase):
    def test_distinct_channels_and_legacy_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            con=db.connect(str(Path(tmp)/'test.sqlite3'));db.init_db(con)
            who={'id':1,'name':'Тест','role':'admin'}
            sid=sv.create_student(con,{'name':'Тест Примеров','parent_name':'Анна','phone':'+79000000001'},who)
            for key in ('cash','card_branch','sber_qr_branch','tochka_qr_link'):
                money.add_payment(con,sid,100,key,who,paid_at='2026-11-12')
            with self.assertRaises(ValueError):money.add_payment(con,sid,100,'paykeeper',who)
            money.add_payment(con,sid,100,'paykeeper',{'id':0,'name':'PayKeeper','role':'admin'},paid_at='2026-11-12')
            rows=money.payments_for_student(con,sid)
            self.assertEqual(len({r['method_label'] for r in rows}),5)
            self.assertEqual({r['display_method'] for r in rows},{'cash','card_branch','sber_qr_branch','tochka_qr_link','paykeeper'})
            legacy=money.add_payment(con,sid,200,'sbp',who,paid_at='2026-11-12')
            old_card=money.add_payment(con,sid,200,'card',who,paid_at='2026-11-12')
            online=money.add_payment(con,sid,300,'online',who,paid_at='2026-11-12')
            labels={r['id']:r['method_label'] for r in money.payments_for_student(con,sid)}
            self.assertIn('не уточнён',labels[legacy]);self.assertIn('не уточнён',labels[old_card]);self.assertIn('не уточнён',labels[online])
            con.execute("INSERT INTO pay_links(student_id,amount,orderid,status,created_at,payment_id,pk_payment_id) VALUES (?,300,'source','paid','2026-11-12',?,'pk-source')",(sid,online))
            labels={r['id']:r['method_label'] for r in sv.student_detail(con,sid)['payments']}
            self.assertEqual(labels[online],'По ссылке PayKeeper')
            reg=money.registry(con,'2026-11-01','2026-11-30','paykeeper')
            self.assertEqual(reg['total'],400);self.assertEqual(len(reg['rows']),2)
            self.assertEqual(money.registry(con,'2026-11-01','2026-11-30','online')['rows'],[])
            self.assertEqual(con.execute('SELECT method FROM payments WHERE id=?',(online,)).fetchone()[0],'online')
            for key in ('card_branch','sber_qr_branch','tochka_qr_link'):
                self.assertEqual(money.registry(con,'2026-11-01','2026-11-30',key)['total'],100)
            con.close()


if __name__=='__main__':unittest.main()
