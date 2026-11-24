import argparse
import hashlib
import json
from pathlib import Path
from .store import connect
from .source import read_file,ingest
from . import sorting,routing,weighing,report,baffle_trial

HANDLERS={
 'sort-import':{'point':sorting.point,'batch':sorting.batch,'sample':sorting.sample,'revoke_sample':sorting.revoke_sample},
 'route-import':{'vehicle':routing.vehicle,'route':routing.route,'load':routing.load,'cancel_load':routing.cancel_load},
 'weigh-import':{'station':weighing.station,'weigh':weighing.weigh},
 'baffle-import':{
   'baffle_trial':baffle_trial.trial,'baffle_shift':baffle_trial.shift,'baffle_feed':baffle_trial.feed_source,
   'baffle_point':baffle_trial.enroll_point,'baffle_retain':baffle_trial.retain_feed,
   'baffle_issue':baffle_trial.issue_fixture,'baffle_switch':baffle_trial.switch_baffle,
   'baffle_origin_link':baffle_trial.link_same_source,'baffle_reading':baffle_trial.reading,
   'baffle_unknown_unseal':baffle_trial.unknown_unseal,'baffle_attest':baffle_trial.attest_pair,
 },
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
def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('command',choices=[*HANDLERS,'demo','report','audit','baffle-sheet']);p.add_argument('--db',default='waste.sqlite');p.add_argument('--file');p.add_argument('--trial')
    args=p.parse_args(argv);db=connect(args.db)
    if args.command=='demo':
        results=[]
        for cmd,name in [('sort-import','sort-ok.json'),('route-import','route-ok.json'),('weigh-import','weigh-ok.json')]:
            results.extend(import_file(db,cmd,Path(__file__).resolve().parents[1]/'fixtures'/name))
        out={'outcomes':results,'report':report.report(db)}
    elif args.command=='report':out=report.report(db)
    elif args.command=='audit':out=report.audit(db,HANDLERS)
    elif args.command=='baffle-sheet':
        out={'capture_sheets':baffle_trial.capture_sheet(db,args.trial)}
    else:
        if not args.file:p.error('--file required')
        out={'outcomes':import_file(db,args.command,args.file),'report':report.report(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
