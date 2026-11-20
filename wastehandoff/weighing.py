from .store import one,many,log

def station(db,e):
    if not 0<=float(e['tolerance_pct'])<=100:raise ValueError('invalid loss tolerance')
    db.execute('INSERT INTO station(id,zone,category,tolerance_pct) VALUES(?,?,?,?)',
               (e['id'],e['zone'],e['category'],float(e['tolerance_pct'])))
    return 'registered'

def weigh(db,e):
    station=one(db,'SELECT * FROM station WHERE id=?',(e['station_id'],))
    route=one(db,'SELECT * FROM route WHERE id=?',(e['route_id'],))
    if not station or not route:raise ValueError('unknown station or route')
    vehicle=one(db,'SELECT * FROM vehicle WHERE id=?',(route['vehicle_id'],))
    net=float(e['gross'])-float(e['tare'])
    previous=one(db,"SELECT * FROM weigh WHERE route_id=? AND seal=? AND state IN ('accepted','disputed') ORDER BY at DESC,id LIMIT 1",(route['id'],e['seal']))
    superseded=None
    if previous and e.get('reweigh_of')==previous['id'] and e.get('witness') and e['witness']!=e['operator'] and e['at']>previous['at']:
        superseded=previous
    elif previous:
        state,reason='duplicate','seal already has a terminal ticket; independent supersession required'
    if net<0 or e['at']<route['starts'] or not e.get('operator'):
        state,reason='invalid','negative net, early arrival, or missing operator'
    elif previous and not superseded:
        state,reason='duplicate','seal already has a terminal ticket; independent supersession required'
    elif station['category']!=vehicle['category'] or station['zone']!=route['zone']:
        state,reason='wrong_station','station does not accept this route class or zone'
    else:
        loads=many(db,"SELECT * FROM load WHERE route_id=? AND seal=? AND state='loaded'",(route['id'],e['seal']))
        if not loads:state,reason='unmatched','vehicle seal has no confirmed loading receipt'
        else:
            declared=sum(x['kg'] for x in loads)
            delta=declared-net
            state='accepted' if abs(delta)<=declared*station['tolerance_pct']/100 else 'disputed'
            reason='station net within declared loss tolerance' if state=='accepted' else 'net mismatch exceeds contractual tolerance'
    if superseded and state in {'accepted','disputed'}:
        db.execute("UPDATE weigh SET state='superseded',reason='independently witnessed reweigh' WHERE id=?",(superseded['id'],))
        db.execute("UPDATE handoff SET state='superseded',reason='reweigh replaced old ticket' WHERE weigh_id=?",(superseded['id'],))
    elif superseded:
        superseded=None
    db.execute('INSERT INTO weigh(id,station_id,route_id,seal,at,gross,tare,operator,reweigh_of,state,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (e['id'],e['station_id'],e['route_id'],e['seal'],e['at'],float(e['gross']),float(e['tare']),e.get('operator',''),e.get('reweigh_of'),state,reason))
    if state in {'accepted','disputed'}:
        for load in loads:
            db.execute('INSERT INTO handoff(weigh_id,load_id,net_kg,delta_kg,state,reason) VALUES(?,?,?,?,?,?)',
                       (e['id'],load['id'],net*load['kg']/declared,delta*load['kg']/declared,state,reason))
            reconcile_performance(db,load['batch_id'])
            from .sorting import record_terminal_difference
            record_terminal_difference(db,load['batch_id'],e['id'],state)
    elif superseded:
        for load in many(db,'SELECT DISTINCT l.batch_id FROM load l JOIN handoff h ON h.load_id=l.id WHERE h.weigh_id=?',(superseded['id'],)):
            reconcile_performance(db,load['batch_id'])
    log(db,'weighing',e['id'],state,reason,e['id'])
    return state

def reconcile_performance(db,batch_id):
    b=one(db,'SELECT * FROM batch WHERE id=?',(batch_id,))
    if not b:return
    handoffs=many(db,"SELECT h.net_kg FROM handoff h JOIN load l ON l.id=h.load_id WHERE l.batch_id=? AND l.state='loaded' AND h.state='accepted'",(batch_id,))
    credited=sum(h['net_kg'] for h in handoffs) if b['state']=='qualified' else 0
    state='credited' if credited>0 else 'excluded'
    reason='qualified class and accepted terminal net' if credited>0 else 'no jointly qualified source and handoff'
    old=one(db,'SELECT * FROM performance WHERE batch_id=? ORDER BY id DESC LIMIT 1',(batch_id,))
    if not old or (old['credited_kg'],old['state'])!=(credited,state):
        db.execute('INSERT INTO performance(batch_id,credited_kg,state,reason) VALUES(?,?,?,?)',
                   (batch_id,credited,state,reason))
        log(db,'performance',batch_id,state,reason)
