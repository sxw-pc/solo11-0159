import hashlib
import json
from .store import connect,many
from .source import ingest

TABLES=['point','batch','sample','batch_decision','vehicle','route','load','station','weigh','handoff','performance','decision_log']
def report(db):
    r={table:[dict(x) for x in many(db,f'SELECT * FROM {table} ORDER BY rowid')] for table in TABLES}
    r['event']=[dict(x) for x in many(db,'SELECT id,kind,sha FROM event ORDER BY rowid')]
    r['source_file']=[dict(x) for x in many(db,'SELECT id,command,sha FROM source_file ORDER BY id')]
    return r

def audit(db,handlers):
    replay=connect(':memory:')
    for src in many(db,'SELECT command,sha,raw FROM source_file ORDER BY id'):
        if hashlib.sha256(src['raw']).hexdigest()!=src['sha']:
            return {'replay_ok':False,'mismatches':['source_file_sha']}
        data=json.loads(src['raw'])
        with replay:
            for e in data['events']:
                if e['kind'] not in handlers[src['command']]:raise ValueError('recorded command/kind mismatch')
                if ingest(replay,e):handlers[src['command']][e['kind']](replay,e)
            replay.execute('INSERT INTO source_file(command,sha,raw) VALUES(?,?,?)',(src['command'],src['sha'],src['raw']))
    left,right=report(db),report(replay)
    for r in (left,right):
        for d in r['decision_log']:d.pop('at',None)
    return {'replay_ok':left==right,'mismatches':[k for k in left if left[k]!=right[k]],'event_count':len(left['event'])}
