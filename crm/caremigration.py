"""Consolidated admin status for automatic care/trainer migration batches."""


def report(con):
    from . import (targetedtrainerresidual, targetedverifiedcarebatch,
                   targetedverifiedcareearly, targetedverifiedcaregaps,
                   targetedverifiedcarehistory, targetedverifiedlatesales)
    packages={
        'Продажи и ПУ':targetedverifiedcarebatch.report(con),
        'История 13–21.11':targetedverifiedcarehistory.report(con),
        'Сводка 01–12.11':targetedverifiedcareearly.report(con),
        'Продажи 22–23.11':targetedverifiedlatesales.report(con),
        'Остальные помеченные факты':targetedverifiedcaregaps.report(con),
    }
    totals={key:sum(int((value.get('counts') or {}).get(key) or 0) for value in packages.values())
            for key in ('applied','already','unmatched','ambiguous','errors')}
    unresolved=[]
    for label,value in packages.items():
        for key in ('unmatched','ambiguous','errors'):
            for row in value.get(key,[]) or []:
                unresolved.append({'package':label,'kind':key,'source_id':row.get('source_id',''),
                                   'name':row.get('name',''),'reason':row.get('error') or row.get('reason') or ''})
    trainer=targetedtrainerresidual.report(con)
    states=[value.get('state','pending') for value in packages.values()]
    return {'state':'complete' if states and all(x=='complete' for x in states) and trainer.get('state')=='complete' else 'partial',
            'totals':totals,'packages':packages,'unresolved':unresolved,'trainer':trainer}
