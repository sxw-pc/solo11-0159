import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from wastehandoff.store import connect
from wastehandoff.__main__ import import_file,HANDLERS,main
from wastehandoff.report import report,audit

ROOT=Path(__file__).resolve().parents[1]

class UnloadAdjudicationTest(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'test.sqlite')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def fixture(self,cmd,name):return import_file(self.db,cmd,ROOT/'fixtures'/name)
    def events(self,cmd,events):
        p=Path(self.temp.name)/'events.json';p.write_text(json.dumps({'events':events}))
        return import_file(self.db,cmd,p)
    def base(self):
        self.fixture('sort-import','sort-two.json');self.fixture('route-import','route-two.json')
        self.fixture('weigh-import','weigh-two.json')
    def unloaded(self):
        self.base();self.fixture('unload-import','unload-setup.json');self.fixture('unload-import','unload-segments.json')
    def resolved(self):
        self.unloaded();self.fixture('unload-import','unload-rescan.json')
    def latest_adjudication(self):
        return self.db.execute('SELECT * FROM adjudication ORDER BY id DESC LIMIT 1').fetchone()
    def bounds(self,adj_id):
        return {r['batch_id']:r for r in self.db.execute('SELECT * FROM batch_bound WHERE adjudication_id=?',(adj_id,))}
    def certs(self,adj_id):
        return {(c['form'],c['batch_id']):c for c in self.db.execute('SELECT * FROM certificate WHERE adjudication_id=?',(adj_id,))}
    def perf(self,batch_id):
        return self.db.execute('SELECT * FROM performance WHERE batch_id=? ORDER BY id DESC LIMIT 1',(batch_id,)).fetchone()

    def test_boundary_acceptance_does_not_prove_source_net(self):
        self.base();r=report(self.db)
        w=r['weigh'][0]
        self.assertEqual(w['state'],'accepted')          # 100 declared vs 90 net, delta 10 == tolerance 10%
        self.assertEqual(w['gross']-w['tare'],90)
        self.assertEqual(sorted(h['net_kg'] for h in r['handoff']),[36.0,54.0])  # proportional bookkeeping only
        self.assertEqual(self.perf('B1')['credited_kg'],54.0)

    def test_missed_container_code_yields_intervals_not_ratio(self):
        self.unloaded()
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'adjudicated')
        self.assertEqual(adj['z'],1.0)                   # 93-3z=90 pins the shared shift zero uniquely
        self.assertEqual(adj['vehicle_net'],90.0)
        self.assertEqual(adj['rules_version'],'unload-adjudication-v1')
        b=self.bounds(adj['id'])
        self.assertEqual((b['B1']['min_kg'],b['B1']['max_kg'],b['B1']['state']),(55.0,60.0,'interval'))
        self.assertEqual((b['B2']['min_kg'],b['B2']['max_kg'],b['B2']['state']),(30.0,35.0,'interval'))
        for batch in ('B1','B2'):                        # bounds trace back to raw tickets
            for key in ('min_tickets','max_tickets'):
                tickets=json.loads(b[batch][key])
                self.assertIn('U3',tickets);self.assertIn('W1',tickets);self.assertIn('CAL-1',tickets)
        rel=json.loads(adj['relations'])
        missed=[g for g in rel['groups'] if g['container'] is None]
        self.assertEqual(len(missed),1)
        self.assertEqual(missed[0]['sources'],['B1','B2'])   # still-feasible container->source relations
        expl=self.db.execute('SELECT totals FROM adjudication_explanation WHERE adjudication_id=? ORDER BY idx',(adj['id'],)).fetchall()
        self.assertEqual(sorted(json.loads(e['totals'])['B1'] for e in expl),[55.0,60.0])
        c=self.certs(adj['id'])
        self.assertEqual(c[('A',None)]['state'],'issued')    # contract A: 90 kg vehicle certificate
        self.assertEqual(c[('A',None)]['kg'],90.0)
        self.assertEqual(c[('A',None)]['is_primary'],1)      # import contract number CA-1 selects form A
        self.assertIsNone(c[('A',None)]['batch_id'])         # batch column: not yet attributed
        self.assertEqual(c[('B','B1')]['state'],'pending')   # contract B: both sources pending
        self.assertEqual(c[('B','B2')]['state'],'pending')
        self.assertEqual(c[('B','B1')]['is_primary'],0)
        for batch in ('B1','B2'):                            # 54/36 proportional credit must not stand
            p=self.perf(batch)
            self.assertEqual((p['credited_kg'],p['state']),(0.0,'pending_verification'))
        self.assertIn('U3',json.loads(c[('A',None)]['tickets']))

    def test_independent_rescan_makes_source_net_certain(self):
        self.resolved()
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'adjudicated')
        b=self.bounds(adj['id'])
        self.assertEqual((b['B1']['min_kg'],b['B1']['state']),(60.0,'certain'))
        self.assertEqual((b['B2']['min_kg'],b['B2']['state']),(30.0,'certain'))
        c=self.certs(adj['id'])
        self.assertEqual((c[('B','B1')]['state'],c[('B','B1')]['kg']),('issued',60.0))
        self.assertEqual((c[('B','B2')]['state'],c[('B','B2')]['kg']),('issued',30.0))
        self.assertIn('RS-1',json.loads(c[('B','B1')]['tickets']))
        self.assertEqual(self.perf('B1')['credited_kg'],60.0)
        self.assertEqual(self.perf('B2')['credited_kg'],30.0)
        r=report(self.db)                                # originals never overwritten
        self.assertEqual(sorted(h['net_kg'] for h in r['handoff']),[36.0,54.0])
        self.assertEqual([u['container_code'] for u in r['unload_segment']],['C-01','C-02',None])
        self.assertEqual(len(r['container_rescan']),1)

    def test_late_sample_revocation_revokes_vehicle_certificate(self):
        self.resolved();self.fixture('sort-import','sort-revoke-b2.json')
        r=report(self.db)
        self.assertEqual(r['batch'][1]['state'],'pending_sample')
        self.assertEqual(r['load'][1]['state'],'quarantine')     # sorting decision isolates the load
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'adjudicated')             # physical explanation still stands
        c=self.certs(adj['id'])
        self.assertEqual(c[('A',None)]['state'],'revoked')       # qualified sources no longer total 90
        self.assertIn('B2',c[('A',None)]['reason'])
        self.assertEqual((c[('B','B1')]['state'],c[('B','B1')]['kg']),('issued',60.0))
        self.assertEqual(c[('B','B2')]['state'],'revoked')
        self.assertEqual((self.perf('B1')['credited_kg'],self.perf('B1')['state']),(60.0,'credited'))
        self.assertEqual((self.perf('B2')['credited_kg'],self.perf('B2')['state']),(0.0,'excluded'))
        self.assertEqual(r['weigh'][0]['state'],'accepted')      # original vehicle ticket kept
        issued=[x for x in r['certificate'] if x['form']=='A' and x['state']=='issued']
        self.assertEqual(len(issued),2)                          # earlier issued versions preserved

    def test_independent_reweigh_sends_vehicle_to_investigation(self):
        self.resolved();self.fixture('weigh-import','weigh-reweigh.json')
        r=report(self.db)
        self.assertEqual([w['state'] for w in r['weigh']],['superseded','accepted'])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'pending_investigation')
        self.assertIn('calibration conflict',adj['reason'])      # 93-3z=95 has no shared zero in [0,2]
        c=self.certs(adj['id'])
        self.assertEqual(c[('A',None)]['state'],'revoked')
        self.assertEqual(c[('B','B1')]['state'],'pending')
        p=self.perf('B1')                                        # no proportional fill under investigation
        self.assertEqual((p['credited_kg'],p['state']),(0.0,'pending_verification'))
        self.assertEqual(len(r['weigh']),2)                      # original tickets preserved
        self.assertTrue(any(h['state']=='superseded' for h in r['handoff']))

    def test_calibration_conflict_and_resolution_readjudicate(self):
        self.resolved()
        self.events('unload-import',[{'id':'CAL-2','kind':'shift_calibration','station_id':'ST1','shift':'shift-1',
          'zero_min':1.5,'zero_max':2,'operator':'metrology-a','witness':'metrology-b','at':'2026-04-01T12:40:00','raw_digest':'cc'*32}])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'pending_investigation')   # z=1 outside [1.5,2]
        self.assertIn('calibration conflict',adj['reason'])
        self.assertEqual((self.perf('B1')['credited_kg'],self.perf('B1')['state']),(0.0,'pending_verification'))
        self.events('unload-import',[{'id':'CAL-3','kind':'shift_calibration','station_id':'ST1','shift':'shift-1',
          'zero_min':0.5,'zero_max':1.5,'operator':'metrology-a','witness':'metrology-b','at':'2026-04-01T12:50:00','raw_digest':'dd'*32}])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'adjudicated')
        self.assertEqual(self.perf('B1')['credited_kg'],60.0)
        versions=[a['version'] for a in report(self.db)['adjudication']]
        self.assertEqual(versions,sorted(versions))              # append-only version chain
        self.assertGreaterEqual(len(set(versions)),4)

    def test_cancel_load_leaves_mass_unexplained_without_ratio_fill(self):
        self.resolved();self.events('route-import',[{'id':'CL2','kind':'cancel_load','load_id':'L2'}])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'pending_investigation')   # scans still anchor B2, but its load is cancelled
        self.assertIn('B2',adj['reason'])
        p=self.perf('B1')
        self.assertEqual((p['credited_kg'],p['state']),(0.0,'pending_verification'))

    def test_conflicting_container_rescan_anchors_detected_then_fixed(self):
        self.base();self.fixture('unload-import','unload-setup.json')
        self.events('unload-import',[
          {'id':'U1','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':1,'container_code':'C-01','batch_hint':'B1','pre_kg':156,'post_kg':100,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:05:00','raw_digest':'aa'*32},
          {'id':'U2','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':2,'container_code':'C-02','batch_hint':'B2','pre_kg':100,'post_kg':69,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:08:00','raw_digest':'bb'*32},
          {'id':'U3','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':3,'container_code':'C-01','batch_hint':'B2','pre_kg':69,'post_kg':63,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:11:00','raw_digest':'cc'*32},
          {'id':'UC1','kind':'unload_close','route_id':'R1','seal':'seal-x','shift':'shift-1','container_count':3,'operator':'unload-a','witness':'unload-w2','at':'2026-04-01T11:15:00','raw_digest':'dd'*32}])
        adj=self.latest_adjudication()                   # same container scanned into two sources
        self.assertEqual(adj['state'],'pending_investigation')
        self.assertIn('anchors conflict',adj['reason'])
        self.events('unload-import',[{'id':'RS-9','kind':'container_rescan','segment_id':'U3','container_code':'C-03','batch_id':'B1','operator':'rescan-a','witness':'rescan-w','at':'2026-04-01T12:30:00','raw_digest':'ee'*32}])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'adjudicated')
        b=self.bounds(adj['id'])
        self.assertEqual((b['B1']['min_kg'],b['B2']['min_kg']),(60.0,30.0))

    def test_broken_scale_chain_blocks_adjudication(self):
        self.base();self.fixture('unload-import','unload-setup.json')
        self.events('unload-import',[
          {'id':'U1','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':1,'container_code':'C-01','batch_hint':'B1','pre_kg':156,'post_kg':100,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:05:00','raw_digest':'aa'*32},
          {'id':'U2','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':2,'container_code':'C-02','batch_hint':'B2','pre_kg':101,'post_kg':70,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:08:00','raw_digest':'bb'*32},
          {'id':'UC1','kind':'unload_close','route_id':'R1','seal':'seal-x','shift':'shift-1','container_count':2,'operator':'unload-a','witness':'unload-w2','at':'2026-04-01T11:15:00','raw_digest':'dd'*32}])
        adj=self.latest_adjudication()
        self.assertEqual(adj['state'],'pending_investigation')
        self.assertIn('do not chain',adj['reason'])

    def test_unload_capture_requires_independent_witness_and_digest(self):
        self.base();self.fixture('unload-import','unload-setup.json')
        before=len(report(self.db)['event'])
        with self.assertRaises(ValueError):
            self.events('unload-import',[{'id':'UX','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':1,'pre_kg':10,'post_kg':5,'operator':'op','witness':'op','at':'2026-04-01T11:05:00','raw_digest':'aa'*32}])
        with self.assertRaises(ValueError):
            self.events('unload-import',[{'id':'UY','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':1,'pre_kg':10,'post_kg':5,'operator':'op','witness':'w','at':'2026-04-01T11:05:00'}])
        self.assertEqual(len(report(self.db)['event']),before)

    def test_no_complete_explanation_parks_vehicle_without_ratio_fill(self):
        self.events('sort-import',[
          {'id':'PT1','kind':'point','zone':'district-a','category':'recyclable','inspector_org':'classify-team'},
          {'id':'B1','kind':'batch','point_id':'PT1','category':'recyclable','bag_count':6,'declared_kg':60,'at':'2026-04-01T08:00:00'},
          {'id':'S1','kind':'sample','batch_id':'B1','inspector':'classify-team','at':'2026-04-01T08:10:00','contamination':4,'bag_refs':['bag-1']}])
        self.events('route-import',[
          {'id':'V1','kind':'vehicle','category':'recyclable','capacity_kg':200,'zone':'district-a'},
          {'id':'R1','kind':'route','vehicle_id':'V1','starts':'2026-04-01T08:30:00','ends':'2026-04-01T12:00:00','zone':'district-a','dispatcher':'dispatch-a'},
          {'id':'L1','kind':'load','batch_id':'B1','route_id':'R1','loaded_at':'2026-04-01T09:00:00','kg':60,'seal':'seal-x','driver':'driver-a','point_witness':'point-a'}])
        self.events('weigh-import',[
          {'id':'ST1','kind':'station','zone':'district-a','category':'recyclable','tolerance_pct':50},
          {'id':'W1','kind':'weigh','station_id':'ST1','route_id':'R1','seal':'seal-x','at':'2026-04-01T11:00:00','gross':190,'tare':100,'operator':'scale-a'}])
        self.events('unload-import',[
          {'id':'CAL-1','kind':'shift_calibration','station_id':'ST1','shift':'shift-1','zero_min':0,'zero_max':2,'operator':'metrology-a','witness':'metrology-b','at':'2026-04-01T10:30:00','raw_digest':'aa'*32},
          {'id':'U1','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':1,'pre_kg':156,'post_kg':100,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:05:00','raw_digest':'bb'*32},
          {'id':'U2','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':2,'pre_kg':100,'post_kg':69,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:08:00','raw_digest':'cc'*32},
          {'id':'U3','kind':'unload_segment','station_id':'ST1','route_id':'R1','seal':'seal-x','shift':'shift-1','seq':3,'pre_kg':69,'post_kg':63,'operator':'unload-a','witness':'unload-w1','at':'2026-04-01T11:11:00','raw_digest':'dd'*32},
          {'id':'UC1','kind':'unload_close','route_id':'R1','seal':'seal-x','shift':'shift-1','container_count':3,'operator':'unload-a','witness':'unload-w2','at':'2026-04-01T11:15:00','raw_digest':'ee'*32}])
        adj=self.latest_adjudication()                   # 90 kg arrived but only 60 kg was sealed in
        self.assertEqual(adj['state'],'pending_investigation')
        self.assertIn('no complete explanation',adj['reason'])
        p=self.perf('B1')
        self.assertEqual((p['credited_kg'],p['state']),(0.0,'pending_verification'))

    def test_audit_replay_with_unload_evidence(self):
        self.resolved();self.fixture('sort-import','sort-revoke-b2.json');self.fixture('weigh-import','weigh-reweigh.json')
        self.assertTrue(audit(self.db,HANDLERS)['replay_ok'])

    def test_demo_unload_cli_runs_all_four_variations(self):
        db_path=Path(self.temp.name)/'demo.sqlite';self.db.close()
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):main(['demo-unload','--db',str(db_path)])
        out=json.loads(buf.getvalue())
        self.assertEqual(len(out['stages']),5)
        joined=json.dumps(out)
        for marker in ('missed container code','independent rescan','late sample revocation','independent reweigh'):
            self.assertIn(marker,joined)
        self.db=connect(Path(self.temp.name)/'test2.sqlite')

if __name__=='__main__':unittest.main()
