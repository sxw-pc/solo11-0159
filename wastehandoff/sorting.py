from .store import one,many,log

def license(db,e):
    """Record a point-scoped authorization line.

    Re-issuing a license through ``supersedes`` keeps the old row as immutable
    evidence but makes it non-current, so an old associated grant cannot be
    swapped back in later.
    """
    if e['kind']!='license':raise ValueError('license kind required')
    for field in ('id','point_id','category','inspector_org','witness_org','batch_id','at'):
        if not e.get(field):raise ValueError('license needs point, class, inspector, witness, batch and time')
    old=one(db,'SELECT * FROM license WHERE id=?',(e['id'],))
    if old:
        return 'recorded'
    if e.get('supersedes'):
        prior=one(db,'SELECT * FROM license WHERE id=?',(e['supersedes'],))
        if not prior:raise ValueError('superseded license must exist')
        db.execute('UPDATE license SET revoked=1 WHERE id=?',(e['supersedes'],))
    db.execute('INSERT INTO license(id,point_id,category,inspector_org,witness_org,batch_id,at,revoked,origin) VALUES(?,?,?,?,?,?,?,?,?)',
               (e['id'],e['point_id'],e['category'],e['inspector_org'],e['witness_org'],e['batch_id'],e['at'],
                1 if e.get('revoked') else 0,e.get('origin','source')))
    log(db,'sorting',e['id'],'licensed','point-scoped authorization recorded',e['id'])
    return 'recorded'

def license_history(db,point_id,batch_id=None):
    if batch_id is None:
        return many(db,'SELECT * FROM license WHERE point_id=? ORDER BY at,id',(point_id,))
    return many(db,'SELECT * FROM license WHERE point_id=? AND batch_id=? ORDER BY at,id',(point_id,batch_id))

def current_license(db,point_id,category,batch_id,witness_org=None):
    """The current license for exactly this point, class, batch and witness.

    A shared original file never authorizes a sibling point, and a superseded
    re-issue is not current.
    """
    rows=[r for r in license_history(db,point_id,batch_id)
          if not r['revoked'] and r['category']==category]
    if witness_org is not None:
        rows=[r for r in rows if r['witness_org']==witness_org]
    return rows[-1] if rows else None

def point(db,e):
    db.execute('INSERT INTO point(id,zone,category,inspector_org) VALUES(?,?,?,?)',
               (e['id'],e['zone'],e['category'],e['inspector_org']))
    log(db,'sorting',e['id'],'registered','authorized sorting point',e['id'])
    return 'registered'

def batch(db,e):
    p=one(db,'SELECT * FROM point WHERE id=?',(e['point_id'],))
    if not p:raise ValueError('unknown sorting point')
    if int(e['bag_count'])<=0 or float(e['declared_kg'])<=0:raise ValueError('positive bags and mass required')
    # If the source declared explicit point licenses for *this batch*, the
    # batch's class must be covered by a current one.  Legacy sources without
    # license lines fall back to the point's own category grant.
    if license_history(db,e['point_id'],e['id']) and not current_license(db,e['point_id'],e['category'],e['id']):
        state='wrong_category'
        reason='no current point license for this class and batch'
    elif p['category']==e['category']:
        state='pending_sample';reason='awaiting independent classification sample'
    else:
        state='wrong_category';reason='point license does not cover category'
    db.execute('INSERT INTO batch(id,point_id,category,bag_count,declared_kg,at,state,reason) VALUES(?,?,?,?,?,?,?,?)',
               (e['id'],e['point_id'],e['category'],int(e['bag_count']),float(e['declared_kg']),e['at'],state,reason))
    log(db,'sorting',e['id'],state,reason,e['id'])
    return state

def sample(db,e):
    b=one(db,'SELECT * FROM batch WHERE id=?',(e['batch_id'],))
    if not b:raise ValueError('unknown batch')
    p=one(db,'SELECT * FROM point WHERE id=?',(b['point_id'],))
    if not p:raise ValueError('unknown sorting point')
    hist=license_history(db,b['point_id'],b['id'])
    if hist:
        lic=current_license(db,b['point_id'],b['category'],b['id'])
        if not lic or lic['inspector_org']!=p['inspector_org'] or e['inspector']!=p['inspector_org']:
            raise ValueError('sample needs current point license and authorized inspector after batch collection')
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

def record_terminal_difference(db,batch_id,weigh_id,state):
    prior=one(db,'SELECT * FROM batch_investigation WHERE batch_id=? ORDER BY id DESC LIMIT 1',(batch_id,))
    new_state='open' if state=='disputed' else 'resolved'
    if prior and prior['state']==new_state:return new_state
    reason='terminal net differs from signed loading mass' if new_state=='open' else 'independent reweigh resolved terminal difference'
    db.execute('INSERT INTO batch_investigation(batch_id,weigh_id,state,reason) VALUES(?,?,?,?)',
               (batch_id,weigh_id,new_state,reason))
    log(db,'sorting',batch_id,'investigation_'+new_state,reason,weigh_id)
    return new_state
