from .store import one,many,log

def vehicle(db,e):
    if float(e['capacity_kg'])<=0:raise ValueError('vehicle compartment capacity must be positive')
    db.execute('INSERT INTO vehicle(id,category,capacity_kg,zone) VALUES(?,?,?,?)',
               (e['id'],e['category'],float(e['capacity_kg']),e['zone']))
    return 'registered'

def route(db,e):
    v=one(db,'SELECT * FROM vehicle WHERE id=?',(e['vehicle_id'],))
    if not v:raise ValueError('unknown vehicle')
    if e['starts']>=e['ends'] or v['zone']!=e['zone'] or not e.get('dispatcher'):
        raise ValueError('route needs covered zone, ordered window and dispatcher')
    db.execute('INSERT INTO route(id,vehicle_id,starts,ends,zone,dispatcher) VALUES(?,?,?,?,?,?)',
               (e['id'],e['vehicle_id'],e['starts'],e['ends'],e['zone'],e['dispatcher']))
    log(db,'routing',e['id'],'planned','route and vehicle assigned',e['id'])
    return 'planned'

def load(db,e):
    b=one(db,'SELECT * FROM batch WHERE id=?',(e['batch_id'],))
    r=one(db,'SELECT * FROM route WHERE id=?',(e['route_id'],))
    if not b or not r:raise ValueError('batch and route must exist')
    v=one(db,'SELECT * FROM vehicle WHERE id=?',(r['vehicle_id'],))
    p=one(db,'SELECT * FROM point WHERE id=?',(b['point_id'],))
    kg=float(e['kg'])
    if kg<=0 or kg>b['declared_kg']:state,reason='blocked','loaded mass outside batch declaration'
    elif v['category']!=b['category']:state,reason='blocked','compartment class would mix waste categories'
    elif p['zone']!=r['zone'] or not r['starts']<=e['loaded_at']<r['ends']:
        state,reason='blocked','route does not cover point or loading time'
    elif not e.get('driver') or not e.get('point_witness') or e['driver']==e['point_witness']:
        state,reason='blocked','dual independent loading receipt absent'
    elif b['state']!='qualified':state,reason='quarantine','classification sample not qualified'
    else:
        total=sum(x['kg'] for x in many(db,"SELECT kg FROM load WHERE route_id=? AND state='loaded'",(r['id'],)))
        if total+kg>v['capacity_kg']:state,reason='capacity_wait','compartment capacity exhausted'
        else:state,reason='loaded','qualified batch sealed in compatible compartment'
    db.execute('INSERT INTO load(id,batch_id,route_id,loaded_at,kg,seal,driver,point_witness,state,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (e['id'],b['id'],r['id'],e['loaded_at'],kg,e['seal'],e.get('driver',''),e.get('point_witness',''),state,reason))
    log(db,'routing',e['id'],state,reason,e['id'])
    return state

def cancel_load(db,e):
    load=one(db,'SELECT * FROM load WHERE id=?',(e['load_id'],))
    if not load:raise ValueError('unknown load')
    db.execute("UPDATE load SET state='cancelled',reason='signed loading cancellation' WHERE id=?",(e['load_id'],))
    log(db,'routing',e['load_id'],'cancelled','compartment capacity released',e['id'])
    from .weighing import reconcile_performance
    reconcile_performance(db,load['batch_id'])
    return 'cancelled'

def reconcile_loads(db,batch_id):
    b=one(db,'SELECT state FROM batch WHERE id=?',(batch_id,))
    if b['state']!='qualified':
        for load in many(db,"SELECT id FROM load WHERE batch_id=? AND state='loaded'",(batch_id,)):
            db.execute("UPDATE load SET state='quarantine',reason='late sample removed classification' WHERE id=?",(load['id'],))
            log(db,'routing',load['id'],'quarantine','late source sample changes loading eligibility')
