import argparse
import hashlib
import json
from pathlib import Path
from .store import connect
from .source import read_file,ingest
from . import sorting,routing,weighing,report,scoped_source

HANDLERS={
 'sort-import':{'point':sorting.point,'batch':sorting.batch,'sample':sorting.sample,'license':sorting.license,'revoke_sample':sorting.revoke_sample},
 'sort-scoped-import':{'point':sorting.point,'license':sorting.license,'batch':sorting.batch,'sample':sorting.sample,'revoke_sample':sorting.revoke_sample},
 'route-import':{'vehicle':routing.vehicle,'route':routing.route,'load':routing.load,'cancel_load':routing.cancel_load},
 'weigh-import':{'station':weighing.station,'weigh':weighing.weigh},
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
def import_scoped(db,path):
    """Import one scoped package as a genuine sorting source.

    Verification happens before any mutation, and the replay + registration
    share one transaction: a package that cannot be fully authorized leaves the
    ledger exactly as it was.
    """
    package,raw=scoped_source.read_package(path)
    sha=hashlib.sha256(raw).hexdigest()
    with db:
        if db.execute('SELECT id FROM source_file WHERE command=? AND sha=?',(scoped_source.SCOPED_COMMAND,sha)).fetchone():
            return {'result':'file_replayed','authorization':scoped_source.describe(package)}
        events,scope=scoped_source.verify_package(db,package)
        outputs=[]
        for e in events:
            if e['kind'] not in HANDLERS[scoped_source.SCOPED_COMMAND]:raise ValueError('event kind not allowed in command')
            if ingest(db,e):outputs.append({'id':e['id'],'result':HANDLERS[scoped_source.SCOPED_COMMAND][e['kind']](db,e)})
            else:outputs.append({'id':e['id'],'result':'event_replayed'})
        # The canonical package bytes are the source evidence; ``raw`` is what
        # audit replays.  Nothing derived from the hidden point is stored.
        db.execute('INSERT INTO source_file(command,sha,raw) VALUES(?,?,?)',(scoped_source.SCOPED_COMMAND,sha,raw))
    return {'result':'imported','authorization':scoped_source.describe(package),'outcomes':outputs}
def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('command',choices=[*HANDLERS,'sort-scoped','demo','report','audit']);p.add_argument('--db',default='waste.sqlite');p.add_argument('--file')
    args=p.parse_args(argv);db=connect(args.db)
    if args.command=='demo':
        results=[]
        for cmd,name in [('sort-import','sort-ok.json'),('route-import','route-ok.json'),('weigh-import','weigh-ok.json')]:
            results.extend(import_file(db,cmd,Path(__file__).resolve().parents[1]/'fixtures'/name))
        out={'outcomes':results,'report':report.report(db)}
    elif args.command=='report':out=report.report(db)
    elif args.command=='audit':out=report.audit(db,HANDLERS)
    elif args.command=='sort-scoped':
        if not args.file:p.error('--file required')
        out={'outcome':import_scoped(db,args.file),'report':report.report(db)}
    else:
        if not args.file:p.error('--file required')
        out={'outcomes':import_file(db,args.command,args.file),'report':report.report(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
