import argparse
import hashlib
import json
from pathlib import Path
from .store import connect
from .source import read_file,ingest
from . import sorting,routing,weighing,unloading,report

HANDLERS={
 'sort-import':{'point':sorting.point,'batch':sorting.batch,'sample':sorting.sample,'revoke_sample':sorting.revoke_sample},
 'route-import':{'vehicle':routing.vehicle,'route':routing.route,'load':routing.load,'cancel_load':routing.cancel_load},
 'weigh-import':{'station':weighing.station,'weigh':weighing.weigh},
 'unload-import':{'contract':unloading.contract,'shift_calibration':unloading.shift_calibration,
                  'unload_segment':unloading.unload_segment,'unload_close':unloading.unload_close,
                  'container_rescan':unloading.container_rescan},
}
def import_file(db,command,path):
    raw,events=read_file(path);outputs=[]
    with db:
        sha=hashlib.sha256(raw).hexdigest()
        if db.execute('SELECT id FROM source_file WHERE command=? AND sha=?',(command,sha)).fetchone():return [{'result':'file_replayed'}]
        for e in events:
            if e['kind'] not in HANDLERS[command]:raise ValueError('event kind not allowed in command')
            if ingest(db,e):outputs.append({'id':e['id'],'result':HANDLERS[command][e['kind']](db,e)})
            else:outputs.append({'id':e['id'],'result':'event_replayed'})
        db.execute('INSERT INTO source_file(command,sha,raw) VALUES(?,?,?)',(command,sha,raw))
    return outputs

def seal_snapshot(db):
    snap={'weigh':[{'id':w['id'],'state':w['state'],'net_kg':w['gross']-w['tare'],'reason':w['reason']}
                   for w in db.execute('SELECT * FROM weigh ORDER BY rowid')],
          'load':[{'id':l['id'],'batch_id':l['batch_id'],'state':l['state']} for l in db.execute('SELECT * FROM load ORDER BY rowid')],
          'batch':[{'id':b['id'],'state':b['state']} for b in db.execute('SELECT * FROM batch ORDER BY rowid')]}
    adj=db.execute('SELECT * FROM adjudication ORDER BY id DESC LIMIT 1').fetchone()
    if adj:
        snap['adjudication']={'route_id':adj['route_id'],'seal':adj['seal'],'version':adj['version'],'state':adj['state'],
                              'reason':adj['reason'],'shared_zero':adj['z'],'vehicle_net':adj['vehicle_net'],
                              'rules_version':adj['rules_version'],'relations':json.loads(adj['relations'])}
        snap['bounds']=[{'batch_id':b['batch_id'],'min_kg':b['min_kg'],'max_kg':b['max_kg'],'state':b['state'],
                         'min_tickets':json.loads(b['min_tickets']),'max_tickets':json.loads(b['max_tickets'])}
                        for b in db.execute('SELECT * FROM batch_bound WHERE adjudication_id=? ORDER BY batch_id',(adj['id'],))]
        snap['certificates']=[{'form':c['form'],'batch_id':c['batch_id'],'kg':c['kg'],'state':c['state'],'is_primary':c['is_primary'],
                               'reason':c['reason'],'rules_version':c['rules_version'],'tickets':json.loads(c['tickets'])}
                              for c in db.execute('SELECT * FROM certificate WHERE adjudication_id=? ORDER BY form,batch_id',(adj['id'],))]
    perf={}
    for row in db.execute('SELECT DISTINCT batch_id FROM performance ORDER BY batch_id'):
        p=db.execute('SELECT * FROM performance WHERE batch_id=? ORDER BY id DESC LIMIT 1',(row['batch_id'],)).fetchone()
        perf[row['batch_id']]={'credited_kg':p['credited_kg'],'state':p['state'],'reason':p['reason']}
    snap['performance']=perf
    return snap

def demo_unload(db):
    fixtures=Path(__file__).resolve().parents[1]/'fixtures'
    stages=[('baseline: vehicle ticket accepted at tolerance boundary; proportional split is bookkeeping only',
             [('sort-import','sort-two.json'),('route-import','route-two.json'),('weigh-import','weigh-two.json')]),
            ('missed container code: per-source net becomes an interval, contract A issues, contract B pending',
             [('unload-import','unload-setup.json'),('unload-import','unload-segments.json')]),
            ('independent rescan: third container attributed, source nets become certain',
             [('unload-import','unload-rescan.json')]),
            ('late sample revocation: load quarantined, vehicle certificate revoked, credit re-adjudicated',
             [('sort-import','sort-revoke-b2.json')]),
            ('independent reweigh: original ticket kept, vehicle under investigation, no proportional fill',
             [('weigh-import','weigh-reweigh.json')])]
    out=[]
    for title,files in stages:
        outcomes=[]
        for cmd,name in files:outcomes.extend(import_file(db,cmd,fixtures/name))
        out.append({'stage':title,'outcomes':outcomes,'snapshot':seal_snapshot(db)})
    return {'stages':out}

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('command',choices=[*HANDLERS,'demo','demo-unload','report','audit']);p.add_argument('--db',default='waste.sqlite');p.add_argument('--file')
    args=p.parse_args(argv);db=connect(args.db)
    if args.command=='demo':
        results=[]
        for cmd,name in [('sort-import','sort-ok.json'),('route-import','route-ok.json'),('weigh-import','weigh-ok.json')]:
            results.extend(import_file(db,cmd,Path(__file__).resolve().parents[1]/'fixtures'/name))
        out={'outcomes':results,'report':report.report(db)}
    elif args.command=='demo-unload':out=demo_unload(db)
    elif args.command=='report':out=report.report(db)
    elif args.command=='audit':out=report.audit(db,HANDLERS)
    else:
        if not args.file:p.error('--file required')
        out={'outcomes':import_file(db,args.command,args.file),'report':report.report(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
