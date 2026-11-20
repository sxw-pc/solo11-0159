from .store import one,many,log

def point(db,e):
    db.execute('INSERT INTO point(id,zone,category,inspector_org) VALUES(?,?,?,?)',
               (e['id'],e['zone'],e['category'],e['inspector_org']))
    log(db,'sorting',e['id'],'registered','authorized sorting point',e['id'])
    return 'registered'

def batch(db,e):
    p=one(db,'SELECT * FROM point WHERE id=?',(e['point_id'],))
    if not p:raise ValueError('unknown sorting point')
    if int(e['bag_count'])<=0 or float(e['declared_kg'])<=0:raise ValueError('positive bags and mass required')
    state='pending_sample' if p['category']==e['category'] else 'wrong_category'
    reason='awaiting independent classification sample' if state=='pending_sample' else 'point license does not cover category'
    db.execute('INSERT INTO batch(id,point_id,category,bag_count,declared_kg,at,state,reason) VALUES(?,?,?,?,?,?,?,?)',
               (e['id'],e['point_id'],e['category'],int(e['bag_count']),float(e['declared_kg']),e['at'],state,reason))
    log(db,'sorting',e['id'],state,reason,e['id'])
    return state

def sample(db,e):
    b=one(db,'SELECT * FROM batch WHERE id=?',(e['batch_id'],))
    if not b:raise ValueError('unknown batch')
    p=one(db,'SELECT * FROM point WHERE id=?',(b['point_id'],))
    if e['inspector']!=p['inspector_org'] or e['at']<b['at']:
        raise ValueError('sample needs authorized inspector after batch collection')
    if not 0<=float(e['contamination'])<=100:raise ValueError('invalid contamination percentage')
    refs=e['bag_refs']
    if not refs or len(set(refs))!=len(refs) or len(refs)>b['bag_count']:
        raise ValueError('sampled bag identities must be distinct and within batch count')
    import json
    db.execute('INSERT INTO sample(id,batch_id,inspector,at,contamination,bag_refs) VALUES(?,?,?,?,?,?)',
               (e['id'],b['id'],e['inspector'],e['at'],float(e['contamination']),json.dumps(refs)))
    return recompute(db,b['id'],e['id'])

def revoke_sample(db,e):
    sample=one(db,'SELECT * FROM sample WHERE id=?',(e['sample_id'],))
    if not sample:raise ValueError('unknown sample')
    db.execute('UPDATE sample SET revoked=1 WHERE id=?',(e['sample_id'],))
    return recompute(db,sample['batch_id'],e['id'])

def recompute(db,batch_id,event_id=None):
    b=one(db,'SELECT * FROM batch WHERE id=?',(batch_id,))
    p=one(db,'SELECT * FROM point WHERE id=?',(b['point_id'],))
    samples=many(db,'SELECT * FROM sample WHERE batch_id=? AND revoked=0 ORDER BY at,id',(batch_id,))
    latest=samples[-1] if samples else None
    if b['category']!=p['category']:state,reason='wrong_category','sorting point not licensed for this class'
    elif not latest:state,reason='pending_sample','no current independent sample'
    elif latest['contamination']>10:state,reason='contaminated','sample contamination above ten percent'
    else:state,reason='qualified','authorized sample within contamination limit'
    usable=b['declared_kg'] if state=='qualified' else 0
    old=one(db,'SELECT * FROM batch_decision WHERE batch_id=? ORDER BY id DESC LIMIT 1',(batch_id,))
    if not old or old['state']!=state or old['usable_kg']!=usable:
        db.execute('INSERT INTO batch_decision(batch_id,state,usable_kg,reason,source_id) VALUES(?,?,?,?,?)',
                   (batch_id,state,usable,reason,event_id))
        db.execute('UPDATE batch SET state=?,reason=? WHERE id=?',(state,reason,batch_id))
        log(db,'sorting',batch_id,state,reason,event_id)
        from .routing import reconcile_loads
        reconcile_loads(db,batch_id)
        from .weighing import reconcile_performance
        reconcile_performance(db,batch_id)
    return state
