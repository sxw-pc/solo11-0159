"""Offline unload capture and per-source net adjudication.

A terminal vehicle ticket only proves the sealed whole-vehicle net; the
proportional split written into ``handoff`` by ``weighing.weigh`` is a
bookkeeping assumption, not evidence of any single source's handed-over
mass.  This module records compartment-opening captures (container code,
batch seal anchor, in/out scale readings, shift scale calibration,
independent witness, raw capture digest) and adjudicates each source
batch's provable net by mass conservation under one shared shift zero
correction.  Missed or re-scanned container codes leave attribution
intervals; if no complete explanation conserves the sealed mass the whole
vehicle is placed under investigation and no proportional value is used
to fill the gap.  Adjudications, bounds and certificates are append-only
versions: late sample revocation, load cancellation, independent reweigh
and calibration conflicts re-adjudicate without overwriting originals.
"""
import json
from .store import one,many,log

RULES_VERSION='unload-adjudication-v1'
EPS=1e-9
MAX_EXPLANATIONS=65536

def _require(cond,msg):
    if not cond:raise ValueError(msg)

def _witnessed(e):
    _require(e.get('operator'),'operator required')
    _require(e.get('witness') and e['witness']!=e['operator'],'independent witness required')
    _require(e.get('raw_digest'),'raw capture digest required')

def contract(db,e):
    _require(e.get('form') in ('whole_vehicle','per_source'),'contract form must be whole_vehicle or per_source')
    _require(e.get('contract_no') and e.get('party'),'contract number and party required')
    db.execute('INSERT INTO contract(id,contract_no,form,party,signed_at) VALUES(?,?,?,?,?)',
               (e['id'],e['contract_no'],e['form'],e['party'],e.get('signed_at','')))
    log(db,'unloading',e['id'],'registered','signed measurement contract registered',e['id'])
    return 'registered'

def shift_calibration(db,e):
    _require(one(db,'SELECT id FROM station WHERE id=?',(e['station_id'],)),'unknown station')
    zmin,zmax=float(e['zero_min']),float(e['zero_max'])
    _require(zmin<=zmax,'zero correction interval inverted')
    _witnessed(e)
    db.execute('INSERT INTO shift_calibration(id,station_id,shift,zero_min,zero_max,operator,witness,at,raw_digest) VALUES(?,?,?,?,?,?,?,?,?)',
               (e['id'],e['station_id'],e['shift'],zmin,zmax,e['operator'],e['witness'],e['at'],e['raw_digest']))
    log(db,'unloading',e['id'],'calibrated','shared shift zero correction interval recorded',e['id'])
    for s in many(db,'SELECT DISTINCT route_id,seal FROM unload_segment WHERE station_id=? AND shift=? ORDER BY route_id,seal',
                  (e['station_id'],e['shift'])):
        readjudicate_seal(db,s['route_id'],s['seal'],e['id'])
    return 'calibrated'

def unload_segment(db,e):
    _require(one(db,'SELECT id FROM station WHERE id=?',(e['station_id'],)),'unknown station')
    _require(one(db,'SELECT id FROM route WHERE id=?',(e['route_id'],)),'unknown route')
    pre,post=float(e['pre_kg']),float(e['post_kg'])
    _require(pre>=post>=0,'segment pre/post readings inconsistent')
    _require(int(e['seq'])>=1,'segment order must be a positive integer')
    _witnessed(e)
    db.execute('INSERT INTO unload_segment(id,station_id,route_id,seal,shift,seq,container_code,batch_hint,pre_kg,post_kg,operator,witness,at,raw_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (e['id'],e['station_id'],e['route_id'],e['seal'],e['shift'],int(e['seq']),e.get('container_code'),
                e.get('batch_hint'),pre,post,e['operator'],e['witness'],e['at'],e['raw_digest']))
    log(db,'unloading',e['id'],'captured','compartment opening recorded',e['id'])
    readjudicate_seal(db,e['route_id'],e['seal'],e['id'])
    return 'captured'

def unload_close(db,e):
    _require(one(db,'SELECT id FROM route WHERE id=?',(e['route_id'],)),'unknown route')
    _require(int(e['container_count'])>=0,'container count must be non-negative')
    _witnessed(e)
    db.execute('INSERT INTO unload_close(id,route_id,seal,shift,container_count,operator,witness,at,raw_digest) VALUES(?,?,?,?,?,?,?,?,?)',
               (e['id'],e['route_id'],e['seal'],e['shift'],int(e['container_count']),e['operator'],e['witness'],e['at'],e['raw_digest']))
    log(db,'unloading',e['id'],'closed','seal exit confirmed; every opened container emptied',e['id'])
    readjudicate_seal(db,e['route_id'],e['seal'],e['id'])
    return 'closed'

def container_rescan(db,e):
    seg=one(db,'SELECT * FROM unload_segment WHERE id=?',(e['segment_id'],))
    _require(seg,'unknown unload segment')
    _require(e.get('container_code') and e.get('batch_id'),'rescan needs container code and source batch')
    _require(e.get('operator') and e['operator']!=seg['operator'],'rescan operator must differ from segment operator')
    _witnessed(e)
    db.execute('INSERT INTO container_rescan(id,segment_id,container_code,batch_id,operator,witness,at,raw_digest) VALUES(?,?,?,?,?,?,?,?)',
               (e['id'],e['segment_id'],e['container_code'],e['batch_id'],e['operator'],e['witness'],e['at'],e['raw_digest']))
    log(db,'unloading',e['id'],'rescanned','missed or duplicate container code independently supplemented',e['id'])
    readjudicate_seal(db,seg['route_id'],seg['seal'],e['id'])
    return 'rescanned'

def _effective_segments(db,route_id,seal):
    segs=many(db,'SELECT * FROM unload_segment WHERE route_id=? AND seal=? ORDER BY seq,id',(route_id,seal))
    eff=[];rescans=[]
    for s in segs:
        code,hint=s['container_code'],s['batch_hint']
        for r in many(db,'SELECT * FROM container_rescan WHERE segment_id=? ORDER BY at,id',(s['id'],)):
            code,hint=r['container_code'],r['batch_id'];rescans.append(r['id'])
        eff.append({'id':s['id'],'shift':s['shift'],'station_id':s['station_id'],'pre':s['pre_kg'],'post':s['post_kg'],
                    'raw':s['pre_kg']-s['post_kg'],'code':code,'hint':hint})
    return eff,rescans

def _candidates(db,route_id,seal):
    out={}
    for l in many(db,"SELECT * FROM load WHERE route_id=? AND seal=? AND state IN ('loaded','quarantine') ORDER BY id",(route_id,seal)):
        b=one(db,'SELECT * FROM batch WHERE id=?',(l['batch_id'],))
        out[l['batch_id']]={'load_kg':l['kg'],'qualified':b['state']=='qualified','load_id':l['id'],'load_state':l['state']}
    return out

def _primary_form(db,weigh_row):
    if not weigh_row or not weigh_row['contract_no']:return None
    c=one(db,'SELECT * FROM contract WHERE contract_no=?',(weigh_row['contract_no'],))
    if not c:return None
    return 'A' if c['form']=='whole_vehicle' else 'B'

def _prior_issued(db,route_id,seal,form,batch_id):
    rows=many(db,"""SELECT c.state FROM certificate c JOIN adjudication a ON a.id=c.adjudication_id
                    WHERE a.route_id=? AND a.seal=? AND c.form=? AND IFNULL(c.batch_id,'')=IFNULL(?,'')""",
              (route_id,seal,form,batch_id))
    return any(r['state']=='issued' for r in rows)

def _relations(groups,candidates,explanations):
    rel=[]
    for g in groups:
        if g['hints']:feasible=sorted(g['hints'])
        elif explanations:feasible=sorted({ex['assignment'][g['segments'][0]] for ex in explanations})
        else:feasible=sorted(candidates)
        rel.append({'group':g['key'],'container':g['container'],'segments':g['segments'],'sources':feasible})
    return {'groups':rel,
            'candidates':{b:{'load_kg':c['load_kg'],'qualified':c['qualified'],'load_state':c['load_state']}
                          for b,c in sorted(candidates.items())}}

def _bound_tickets(ex,groups,target,weigh,cal,close,rescans):
    ids=[]
    for g in groups:
        anchored=bool(g['hints'])
        for sid in g['segments']:
            got=ex['assignment'].get(sid)
            if got==target or (not anchored and got!=target):ids.append(sid)
    for x in (weigh,cal,close):
        if x:ids.append(x['id'])
    return sorted(set(ids+list(rescans)))

def _certificates(db,route_id,seal,state,reason,weigh,net,candidates,bounds,tickets,explanation_count):
    primary=_primary_form(db,weigh)
    def void(form,batch,fallback):
        return 'revoked' if _prior_issued(db,route_id,seal,form,batch) else fallback
    certs=[]
    if state=='adjudicated':
        qualified=[b for b in candidates if candidates[b]['qualified']]
        blocking=[b for b in candidates if not candidates[b]['qualified'] and (bounds[b]['max'] or 0)>EPS]
        if qualified and not blocking:
            certs.append({'form':'A','batch_id':None,'kg':net,'state':'issued',
                          'reason':'qualified sources conserve calibrated vehicle net; batch attribution not yet assigned',
                          'primary':1 if primary=='A' else 0,'tickets':tickets})
        else:
            why='no qualified source remains under seal' if not qualified else \
                'unqualified source(s) '+','.join(blocking)+' may hold sealed mass'
            certs.append({'form':'A','batch_id':None,'kg':None,'state':void('A',None,'withheld'),
                          'reason':why,'primary':1 if primary=='A' else 0,'tickets':tickets})
    else:
        certs.append({'form':'A','batch_id':None,'kg':None,'state':void('A',None,'withheld'),
                      'reason':'vehicle under investigation: '+reason,'primary':1 if primary=='A' else 0,'tickets':tickets})
    for b in sorted(candidates):
        bt=sorted(set(tickets)|set(bounds[b]['min_tickets'])|set(bounds[b]['max_tickets']))
        if not candidates[b]['qualified']:
            certs.append({'form':'B','batch_id':b,'kg':None,'state':void('B',b,'excluded'),
                          'reason':'source classification not qualified','primary':1 if primary=='B' else 0,'tickets':bt})
        elif state!='adjudicated':
            certs.append({'form':'B','batch_id':b,'kg':None,'state':'pending',
                          'reason':'vehicle under investigation: '+reason,'primary':1 if primary=='B' else 0,'tickets':bt})
        elif bounds[b]['state']=='certain':
            certs.append({'form':'B','batch_id':b,'kg':bounds[b]['min'],'state':'issued',
                          'reason':'source net identical across all compatible explanations',
                          'primary':1 if primary=='B' else 0,'tickets':bt})
        else:
            certs.append({'form':'B','batch_id':b,'kg':None,'state':'pending',
                          'reason':'source net ranges %.9g..%.9g kg across %d compatible explanations; settlement withheld'
                                   %(bounds[b]['min'],bounds[b]['max'],explanation_count),
                          'primary':1 if primary=='B' else 0,'tickets':bt})
    return certs

def _adjudicate(db,route_id,seal):
    eff,rescans=_effective_segments(db,route_id,seal)
    candidates=_candidates(db,route_id,seal)
    weigh=one(db,"SELECT * FROM weigh WHERE route_id=? AND seal=? AND state='accepted' ORDER BY at DESC,id DESC LIMIT 1",(route_id,seal))
    close=one(db,'SELECT * FROM unload_close WHERE route_id=? AND seal=? ORDER BY at DESC,id DESC LIMIT 1',(route_id,seal))
    shifts=sorted({s['shift'] for s in eff})
    cal=None
    if len(shifts)==1:
        cal=one(db,'SELECT * FROM shift_calibration WHERE station_id=? AND shift=? ORDER BY at DESC,id DESC LIMIT 1',
                (eff[0]['station_id'],shifts[0]))
    gmap={}
    for s in eff:
        key=s['code'] if s['code'] else 'missed:'+s['id']
        g=gmap.setdefault(key,{'key':key,'container':s['code'],'segments':[],'hints':set(),'kg':0.0})
        g['segments'].append(s['id'])
        if s['hint']:g['hints'].add(s['hint'])
    groups=[gmap[k] for k in sorted(gmap)]
    net=round(weigh['gross']-weigh['tare'],9) if weigh else None
    z=None
    def context():
        ids=[s['id'] for s in eff]+list(rescans)
        for x in (weigh,cal,close):
            if x:ids.append(x['id'])
        return sorted(set(ids))
    def fail(reason):
        bounds={b:{'min':None,'max':None,'state':'undetermined','min_tickets':[],'max_tickets':[]} for b in sorted(candidates)}
        return {'state':'pending_investigation','reason':reason,'z':z,'vehicle_net':net,
                'weigh_id':weigh['id'] if weigh else None,'calibration_id':cal['id'] if cal else None,
                'close_id':close['id'] if close else None,
                'relations':_relations(groups,candidates,[]),'explanations':[],'bounds':bounds,
                'certificates':_certificates(db,route_id,seal,'pending_investigation',reason,weigh,net,candidates,bounds,context(),0),
                'batches':sorted(candidates)}
    if not candidates:return fail('no active loading receipt under seal')
    if not weigh:return fail('no accepted terminal ticket anchors the sealed net')
    if len(shifts)>1:return fail('segments span shifts; one shared zero correction cannot be fixed')
    if not cal:return fail('missing shift scale calibration')
    if not close:return fail('seal exit not confirmed; unweighed containers possible')
    if close['shift'] not in shifts:return fail('closure shift does not match segment shift')
    if close['container_count']!=len(eff):return fail('container count mismatch; possible unweighed container')
    for a,b in zip(eff,eff[1:]):
        if abs(a['post']-b['pre'])>EPS:return fail('scale readings do not chain between segments')
    z=round((sum(s['raw'] for s in eff)-net)/len(eff),9)
    if z<cal['zero_min']-EPS or z>cal['zero_max']+EPS:
        return fail('calibration conflict: shared zero %.9g outside [%g,%g]'%(z,cal['zero_min'],cal['zero_max']))
    for s in eff:s['net']=s['raw']-z
    for s in eff:gmap[s['code'] if s['code'] else 'missed:'+s['id']]['kg']+=s['net']
    for g in groups:
        if len(g['hints'])>1:return fail('container re-scan anchors conflict on '+','.join(sorted(g['hints'])))
        if g['hints'] and next(iter(g['hints'])) not in candidates:
            return fail('scan anchor %s outside sealed sources'%next(iter(g['hints'])))
    free=[g for g in groups if not g['hints']]
    srcs=sorted(candidates)
    if len(srcs)**len(free)>MAX_EXPLANATIONS:return fail('explanation space too large to enumerate')
    explanations=[]
    def rec(i,assign):
        if i==len(free):
            totals={s:0.0 for s in srcs}
            for g in groups:
                tgt=next(iter(g['hints'])) if g['hints'] else assign[g['key']]
                totals[tgt]=round(totals[tgt]+g['kg'],9)
            if all(totals[s]<=candidates[s]['load_kg']+EPS for s in srcs):
                full={}
                for g in groups:
                    tgt=next(iter(g['hints'])) if g['hints'] else assign[g['key']]
                    for sid in g['segments']:full[sid]=tgt
                explanations.append({'assignment':full,'totals':totals})
            return
        for s in srcs:
            assign[free[i]['key']]=s;rec(i+1,assign);del assign[free[i]['key']]
    rec(0,{})
    if not explanations:return fail('no complete explanation conserves the sealed mass within loaded quantities')
    bounds={}
    for b in srcs:
        vals=[ex['totals'][b] for ex in explanations]
        mn,mx=min(vals),max(vals)
        mn_ex=next(ex for ex in explanations if abs(ex['totals'][b]-mn)<EPS)
        mx_ex=next(ex for ex in explanations if abs(ex['totals'][b]-mx)<EPS)
        bounds[b]={'min':mn,'max':mx,'state':'certain' if abs(mn-mx)<EPS else 'interval',
                   'min_tickets':_bound_tickets(mn_ex,groups,b,weigh,cal,close,rescans),
                   'max_tickets':_bound_tickets(mx_ex,groups,b,weigh,cal,close,rescans)}
    return {'state':'adjudicated','reason':'%d complete explanation(s) conserve the sealed mass'%len(explanations),
            'z':z,'vehicle_net':net,'weigh_id':weigh['id'],'calibration_id':cal['id'],'close_id':close['id'],
            'relations':_relations(groups,candidates,explanations),'explanations':explanations,'bounds':bounds,
            'certificates':_certificates(db,route_id,seal,'adjudicated','',weigh,net,candidates,bounds,context(),len(explanations)),
            'batches':sorted(candidates)}

def readjudicate_seal(db,route_id,seal,cause):
    if not one(db,'SELECT id FROM unload_segment WHERE route_id=? AND seal=? LIMIT 1',(route_id,seal)):return None
    result=_adjudicate(db,route_id,seal)
    fp=json.dumps({'state':result['state'],'reason':result['reason'],'z':result['z'],'vehicle_net':result['vehicle_net'],
                   'weigh_id':result['weigh_id'],'calibration_id':result['calibration_id'],'close_id':result['close_id'],
                   'relations':result['relations'],
                   'bounds':{b:[v['min'],v['max'],v['state']] for b,v in sorted(result['bounds'].items())},
                   'certificates':[[c['form'],c['batch_id'],c['kg'],c['state'],c['primary']] for c in result['certificates']],
                   'explanations':[ex['totals'] for ex in result['explanations']]},sort_keys=True)
    latest=one(db,'SELECT * FROM adjudication WHERE route_id=? AND seal=? ORDER BY version DESC LIMIT 1',(route_id,seal))
    if latest and latest['fingerprint']==fp:return latest['id']
    version=latest['version']+1 if latest else 1
    cur=db.execute('INSERT INTO adjudication(route_id,seal,version,state,reason,z,vehicle_net,weigh_id,calibration_id,close_id,rules_version,cause_event,relations,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (route_id,seal,version,result['state'],result['reason'],result['z'],result['vehicle_net'],
                    result['weigh_id'],result['calibration_id'],result['close_id'],RULES_VERSION,cause,
                    json.dumps(result['relations'],sort_keys=True),fp))
    adj_id=cur.lastrowid
    for i,ex in enumerate(result['explanations']):
        db.execute('INSERT INTO adjudication_explanation(adjudication_id,idx,z,assignment,totals) VALUES(?,?,?,?,?)',
                   (adj_id,i,result['z'],json.dumps(ex['assignment'],sort_keys=True),json.dumps(ex['totals'],sort_keys=True)))
    for b in sorted(result['bounds']):
        v=result['bounds'][b]
        db.execute('INSERT INTO batch_bound(adjudication_id,batch_id,min_kg,max_kg,state,min_tickets,max_tickets) VALUES(?,?,?,?,?,?,?)',
                   (adj_id,b,v['min'],v['max'],v['state'],json.dumps(v['min_tickets']),json.dumps(v['max_tickets'])))
    for c in result['certificates']:
        db.execute('INSERT INTO certificate(adjudication_id,form,batch_id,kg,state,reason,is_primary,rules_version,tickets) VALUES(?,?,?,?,?,?,?,?,?)',
                   (adj_id,c['form'],c['batch_id'],c['kg'],c['state'],c['reason'],c['primary'],RULES_VERSION,json.dumps(c['tickets'])))
    log(db,'unloading',route_id+'/'+seal,result['state'],result['reason'],cause)
    from .weighing import reconcile_performance
    for b in result['batches']:reconcile_performance(db,b)
    return adj_id

def readjudicate_for_batch(db,batch_id,cause):
    for s in many(db,"SELECT DISTINCT route_id,seal FROM load WHERE batch_id=? AND state IN ('loaded','quarantine') ORDER BY route_id,seal",(batch_id,)):
        readjudicate_seal(db,s['route_id'],s['seal'],cause)

def adjudicated_credit(db,batch_id):
    """Certain per-source net from the latest adjudication of each seal the
    batch rides on, or None when no unload adjudication covers the batch."""
    found=False;total=0.0;pending=False
    for s in many(db,"SELECT DISTINCT route_id,seal FROM load WHERE batch_id=? AND state IN ('loaded','quarantine') ORDER BY route_id,seal",(batch_id,)):
        adj=one(db,'SELECT * FROM adjudication WHERE route_id=? AND seal=? ORDER BY version DESC LIMIT 1',(s['route_id'],s['seal']))
        if not adj:continue
        bound=one(db,'SELECT * FROM batch_bound WHERE adjudication_id=? AND batch_id=?',(adj['id'],batch_id))
        if not bound:continue
        found=True
        if adj['state']=='adjudicated' and bound['state']=='certain':total+=bound['min_kg']
        else:pending=True
    if not found:return None
    return round(total,9),pending
