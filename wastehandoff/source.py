import hashlib
import json
from pathlib import Path
from .store import one

def read_file(path):
    raw=Path(path).read_bytes();data=json.loads(raw)
    if not isinstance(data,dict) or not isinstance(data.get('events'),list):raise ValueError('events list required')
    return raw,data['events']
def ingest(db,event):
    raw=json.dumps(event,sort_keys=True,separators=(',',':')).encode();sha=hashlib.sha256(raw).hexdigest()
    old=one(db,'SELECT kind,sha FROM event WHERE id=?',(event['id'],))
    if old:
        if old['kind']!=event['kind'] or old['sha']!=sha:raise ValueError('event id has conflicting immutable source')
        return False
    db.execute('INSERT INTO event(id,kind,sha,raw) VALUES(?,?,?,?)',(event['id'],event['kind'],sha,raw))
    return True
