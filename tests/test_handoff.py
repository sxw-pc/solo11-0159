import json
import tempfile
import unittest
from pathlib import Path
import wastehandoff
from wastehandoff.store import connect
from wastehandoff.__main__ import import_file,HANDLERS
from wastehandoff.report import report,audit

ROOT=Path(__file__).resolve().parents[1]

class HandoffTest(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'test.sqlite')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def fixture(self,cmd,name):return import_file(self.db,cmd,ROOT/'fixtures'/name)
    def events(self,cmd,events):
        p=Path(self.temp.name)/'events.json';p.write_text(json.dumps({'events':events}))
        return import_file(self.db,cmd,p)
    def sorting(self):self.fixture('sort-import','sort-ok.json')
    def routed(self):self.sorting();self.fixture('route-import','route-ok.json')
    def complete(self):self.routed();self.fixture('weigh-import','weigh-ok.json')
    def test_module_path(self):
        print('module file:',wastehandoff.__file__)
        self.assertEqual(Path(wastehandoff.__file__).resolve().parents[1],ROOT)
    def test_normal_three_ledgers_and_net_credit(self):
        self.complete();r=report(self.db)
        self.assertEqual(r['batch'][0]['state'],'qualified')
        self.assertEqual(r['load'][0]['state'],'loaded')
        self.assertEqual(r['handoff'][0]['state'],'accepted')
        self.assertEqual(r['performance'][-1]['credited_kg'],95)
        self.assertEqual(len(r['source_file']),3)
    def test_late_contamination_quarantines_load_and_credit(self):
        self.complete();self.fixture('sort-import','sort-bad.json')
        r=report(self.db)
        self.assertEqual(r['batch'][0]['state'],'contaminated')
        self.assertEqual(r['load'][0]['state'],'quarantine')
        self.assertEqual(r['performance'][-1]['credited_kg'],0)
        self.assertEqual(r['handoff'][0]['state'],'accepted')
    def test_independent_reweigh_replaces_prior_ticket(self):
        self.complete();self.fixture('weigh-import','weigh-bad.json')
        r=report(self.db)
        self.assertEqual([w['state'] for w in r['weigh']],['superseded','disputed'])
        self.assertEqual(r['performance'][-1]['credited_kg'],0)
    def test_repeat_weigh_without_witness_is_duplicate(self):
        self.complete()
        self.events('weigh-import',[{'id':'W3','kind':'weigh','station_id':'ST1','route_id':'R1','seal':'seal-a','at':'2026-04-01T11:30:00','gross':195,'tare':95,'operator':'scale-a'}])
        r=report(self.db)
        self.assertEqual(r['weigh'][-1]['state'],'duplicate')
        self.assertEqual(r['performance'][-1]['credited_kg'],95)
    def test_dual_witness_required_on_load(self):
        self.routed();self.fixture('route-import','route-bad.json')
        self.assertEqual(report(self.db)['load'][-1]['state'],'blocked')
    def test_wrong_vehicle_class_blocks_mix(self):
        self.sorting()
        self.events('route-import',[{'id':'V2','kind':'vehicle','category':'organic','capacity_kg':200,'zone':'district-a'},
          {'id':'R2','kind':'route','vehicle_id':'V2','starts':'2026-04-01T08:30:00','ends':'2026-04-01T12:00:00','zone':'district-a','dispatcher':'dispatch'},
          {'id':'L2','kind':'load','batch_id':'B1','route_id':'R2','loaded_at':'2026-04-01T09:00:00','kg':100,'seal':'s2','driver':'driver','point_witness':'point'}])
        self.assertEqual(report(self.db)['load'][0]['state'],'blocked')
    def test_wrong_route_zone_blocks_collection(self):
        self.sorting()
        self.events('route-import',[{'id':'V3','kind':'vehicle','category':'recyclable','capacity_kg':200,'zone':'district-b'},
          {'id':'R3','kind':'route','vehicle_id':'V3','starts':'2026-04-01T08:30:00','ends':'2026-04-01T12:00:00','zone':'district-b','dispatcher':'dispatch'},
          {'id':'L3','kind':'load','batch_id':'B1','route_id':'R3','loaded_at':'2026-04-01T09:00:00','kg':100,'seal':'s3','driver':'driver','point_witness':'point'}])
        self.assertEqual(report(self.db)['load'][0]['state'],'blocked')
    def test_capacity_wait_does_not_overbook(self):
        self.routed()
        self.events('route-import',[{'id':'L4','kind':'load','batch_id':'B1','route_id':'R1','loaded_at':'2026-04-01T09:20:00','kg':100,'seal':'s4','driver':'driver','point_witness':'point'}])
        self.assertEqual(report(self.db)['load'][-1]['state'],'loaded')
        self.events('route-import',[{'id':'L5','kind':'load','batch_id':'B1','route_id':'R1','loaded_at':'2026-04-01T09:30:00','kg':1,'seal':'s5','driver':'driver','point_witness':'point'}])
        self.assertEqual(report(self.db)['load'][-1]['state'],'capacity_wait')
    def test_cancel_load_releases_credit_but_keeps_handoff(self):
        self.complete();self.events('route-import',[{'id':'CL1','kind':'cancel_load','load_id':'L1'}])
        r=report(self.db)
        self.assertEqual(r['load'][0]['state'],'cancelled')
        self.assertEqual(r['performance'][-1]['credited_kg'],0)
        self.assertEqual(len(r['handoff']),1)
    def test_unmatched_seal_cannot_gain_credit(self):
        self.routed()
        self.events('weigh-import',[{'id':'ST1','kind':'station','zone':'district-a','category':'recyclable','tolerance_pct':5},
          {'id':'WU','kind':'weigh','station_id':'ST1','route_id':'R1','seal':'wrong','at':'2026-04-01T11:00:00','gross':190,'tare':95,'operator':'scale'}])
        r=report(self.db)
        self.assertEqual(r['weigh'][0]['state'],'unmatched')
        self.assertEqual(r['handoff'],[])
    def test_sample_identity_and_authority(self):
        self.sorting()
        before=len(report(self.db)['event'])
        with self.assertRaises(ValueError):
            self.events('sort-import',[{'id':'S3','kind':'sample','batch_id':'B1','inspector':'stranger','at':'2026-04-01T09:00:00','contamination':0,'bag_refs':['bag-1']}])
        self.assertEqual(len(report(self.db)['event']),before)
    def test_sample_revocation_excludes_current_eligibility(self):
        self.complete();self.events('sort-import',[{'id':'RS1','kind':'revoke_sample','sample_id':'S1'}])
        r=report(self.db)
        self.assertEqual(r['batch'][0]['state'],'pending_sample')
        self.assertEqual(r['performance'][-1]['credited_kg'],0)
    def test_file_replay_and_conflict(self):
        self.sorting();self.assertEqual(self.fixture('sort-import','sort-ok.json')[0]['result'],'file_replayed')
        with self.assertRaises(ValueError):self.events('sort-import',[{'id':'B1','kind':'batch','point_id':'PT1','category':'organic','bag_count':10,'declared_kg':100,'at':'2026-04-01T08:00:00'}])
        self.assertEqual(len(report(self.db)['source_file']),1)
    def test_replay_audit_detects_forged_credit(self):
        self.complete();self.assertTrue(audit(self.db,HANDLERS)['replay_ok'])
        self.db.execute('UPDATE performance SET credited_kg=999 WHERE id=(SELECT MAX(id) FROM performance)');self.db.commit()
        self.assertEqual(audit(self.db,HANDLERS)['mismatches'],['performance'])

if __name__=='__main__':unittest.main()
