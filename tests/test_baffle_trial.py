import json
import tempfile
import unittest
from pathlib import Path

from wastehandoff.__main__ import HANDLERS, import_file, main
from wastehandoff.baffle_trial import LIMITS, capture_sheet
from wastehandoff.report import audit, report
from wastehandoff.store import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def trial_from_excerpt(trial_id="T1", modes=("split_source", "crossover")):
    """Rebuild a registrable trial event from the original excerpt file."""
    excerpt = json.loads((FIXTURES / "baffle_trial_excerpt.json").read_text(encoding="utf-8"))
    worlds = [
        {"baffle_effect": w["difference_due_to_new_baffle"],
         "feed_effect": w["difference_due_to_feed"]}
        for w in excerpt["possible_actual_effects"]
    ]
    return {
        "id": trial_id, "kind": "baffle_trial", "site": excerpt["site"],
        "old_rate": excerpt["mixed_rates"]["old"], "new_rate": excerpt["mixed_rates"]["new"],
        "possible_worlds": worlds, "modes": list(modes),
    }


class BaffleTrialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name) / "trial.sqlite")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def push(self, events):
        path = Path(self.temp.name) / "events.json"
        path.write_text(json.dumps({"events": events}), encoding="utf-8")
        return import_file(self.db, "baffle-import", path)

    def expect_error(self, events, fragment):
        with self.assertRaises(ValueError) as ctx:
            self.push(events)
        self.assertIn(fragment, str(ctx.exception))

    def register(self, tid="T1", modes=("split_source", "crossover")):
        self.push([trial_from_excerpt(tid, modes)])

    def split_groundwork(self, tid="T1", feed="F1", old_fx="FX-O", new_fx="FX-N",
                         fractions=(50, 50), shift="SH1"):
        self.push([
            {"id": shift, "kind": "baffle_shift", "trial_id": tid, "seq": 1},
            {"id": feed, "kind": "baffle_feed", "trial_id": tid, "shift_id": shift,
             "origin_grade": 8, "calibrated_offset": 0},
        ])
        self.push([{"id": "RT-" + tid, "kind": "baffle_retain", "trial_id": tid, "feed_id": feed,
                    "old_fixture_id": old_fx, "new_fixture_id": new_fx, "witness": "split-witness"}])
        self.push([
            {"id": "IS-" + old_fx, "kind": "baffle_issue", "fixture_id": old_fx,
             "baffle_state": "old", "fraction": fractions[0], "witness": "issue-witness"},
            {"id": "IS-" + new_fx, "kind": "baffle_issue", "fixture_id": new_fx,
             "baffle_state": "new", "fraction": fractions[1], "witness": "issue-witness"},
        ])

    def test_excerpt_registers_loose_interval_without_attribution(self):
        self.register()
        sheet = capture_sheet(self.db, "T1")
        # The two numbers from the excerpt both remain individually valid, but
        # the missing 3 points cannot yet be assigned to the baffle.
        self.assertEqual(sheet["observed"], {"old_rate": 8, "new_rate": 5, "difference": 3})
        self.assertEqual(sheet["baffle_effect_interval"], [0, 3])
        self.assertEqual(sheet["verdict"], "inconclusive")
        worlds = {(w["baffle_effect"], w["feed_effect"]) for w in sheet["compatible_worlds"]}
        self.assertEqual(worlds, {(3, 0), (0, 3)})
        self.assertEqual(sheet["attestation"], None)
        self.assertEqual(sheet["pair_candidates"]["accepted"], [])

    def test_split_source_pair_on_one_feed_signs_baffle_effect(self):
        self.register(modes=("split_source",))
        self.split_groundwork()
        self.push([
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
        ])
        self.push([{"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
                    "old_reading_id": "R1", "new_reading_id": "R2"}])
        sheet = capture_sheet(self.db, "T1")
        self.assertEqual(sheet["verdict"], "attributable")
        self.assertEqual(sheet["baffle_effect_interval"], [3, 3])
        self.assertEqual(sheet["attestation"]["provenance_key"], "feed:F1|shift:SH1")
        # Both fixtures are spent: single use, no regrouping.
        self.assertEqual({f["state"] for f in sheet["fixtures"]}, {"spent"})
        # A signed sheet closes planning: no further trial action is offered.
        self.assertEqual(sheet["plan"]["available_actions"], [])
        self.assertIsNone(sheet["plan"]["recommended_first_test"])
        for r in sheet["readings"]:
            self.assertTrue(r["contrastable"])
            self.assertEqual(r["provenance"]["basis"], "split_source")

    def test_crossover_controls_feed_offset_and_returns_residual_effect(self):
        self.register("T2", modes=("crossover",))
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T2", "seq": 1},
            {"id": "SH2", "kind": "baffle_shift", "trial_id": "T2", "seq": 2},
            {"id": "P1", "kind": "baffle_point", "trial_id": "T2", "role": "retrofitted",
             "baffle_state": "old"},
            {"id": "P2", "kind": "baffle_point", "trial_id": "T2", "role": "control",
             "baffle_state": "old"},
        ])
        self.push([
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T2", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
            {"id": "F2", "kind": "baffle_feed", "trial_id": "T2", "shift_id": "SH2",
             "origin_grade": 8, "calibrated_offset": 1},
        ])
        # First-shift old reading is registered before any link exists; its
        # provenance records that it cannot yet serve as a control.
        self.push([{"id": "R1", "kind": "baffle_reading", "trial_id": "T2", "shift_id": "SH1",
                    "feed_id": "F1", "point_id": "P1", "baffle_state": "old", "grade": 8}])
        self.assertFalse(capture_sheet(self.db, "T2")["readings"][0]["contrastable"])
        self.push([{"id": "SW1", "kind": "baffle_switch", "trial_id": "T2", "point_id": "P1",
                    "shift_id": "SH2", "from_state": "old", "to_state": "new",
                    "empty_verified": True}])
        self.push([{"id": "L1", "kind": "baffle_origin_link", "trial_id": "T2", "shift_id": "SH2",
                    "retro_feed_id": "F2", "control_feed_id": "F1", "delta": 1}])
        self.push([{"id": "R2", "kind": "baffle_reading", "trial_id": "T2", "shift_id": "SH2",
                    "feed_id": "F2", "point_id": "P1", "baffle_state": "new", "grade": 5}])
        self.push([{"id": "A2", "kind": "baffle_attest", "trial_id": "T2", "mode": "crossover",
                    "old_reading_id": "R1", "new_reading_id": "R2"}])
        sheet = capture_sheet(self.db, "T2")
        # Observed drop 3 minus simultaneously controlled feed movement 1.
        self.assertEqual(sheet["baffle_effect_interval"], [2, 2])
        self.assertEqual(sheet["attestation"]["mode"], "crossover")
        self.assertEqual(len(sheet["readings"]), 2)

    def test_unverified_switch_blocks_crossover_and_can_be_permanent(self):
        self.register("T3", modes=("crossover",))
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T3", "seq": 1},
            {"id": "SH2", "kind": "baffle_shift", "trial_id": "T3", "seq": 2},
            {"id": "P1", "kind": "baffle_point", "trial_id": "T3", "role": "retrofitted",
             "baffle_state": "old"},
        ])
        self.push([
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T3", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
            {"id": "F2", "kind": "baffle_feed", "trial_id": "T3", "shift_id": "SH2",
             "origin_grade": 8, "calibrated_offset": 1},
        ])
        self.push([{"id": "R1", "kind": "baffle_reading", "trial_id": "T3", "shift_id": "SH1",
                    "feed_id": "F1", "point_id": "P1", "baffle_state": "old", "grade": 8}])
        # No empty-load original: carryover is unobservable across the two shifts.
        self.push([{"id": "SW1", "kind": "baffle_switch", "trial_id": "T3", "point_id": "P1",
                    "shift_id": "SH2", "from_state": "old", "to_state": "new"}])
        self.push([{"id": "L1", "kind": "baffle_origin_link", "trial_id": "T3", "shift_id": "SH2",
                    "retro_feed_id": "F2", "control_feed_id": "F1", "delta": 1}])
        self.push([{"id": "R2", "kind": "baffle_reading", "trial_id": "T3", "shift_id": "SH2",
                    "feed_id": "F2", "point_id": "P1", "baffle_state": "new", "grade": 5}])
        self.expect_error(
            [{"id": "A3", "kind": "baffle_attest", "trial_id": "T3", "mode": "crossover",
              "old_reading_id": "R1", "new_reading_id": "R2"}],
            "empty-load original")
        sheet = capture_sheet(self.db, "T3")
        # Both shifts and the point limit are spent, so no authorized move remains.
        self.assertEqual(sheet["verdict"], "permanently_inconclusive")
        self.assertEqual(sheet["plan"]["available_actions"], [])
        # And a reversing real-feed world remains compatible with the readings.
        self.assertIn({"baffle_effect": 0, "feed_effect": 3}, sheet["compatible_worlds"])

    def test_cross_feed_cherry_pick_is_refused_and_interval_stays_loose(self):
        self.register(modes=("split_source",))
        self.push([{"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1}])
        self.push([
            {"id": "FA", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
            {"id": "FB", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 7, "calibrated_offset": 1},
        ])
        self.push([
            {"id": "RTA", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "FA",
             "old_fixture_id": "OA", "new_fixture_id": "NA", "witness": "w"},
            {"id": "RTB", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "FB",
             "old_fixture_id": "OB", "new_fixture_id": "NB", "witness": "w"},
        ])
        self.push([
            {"id": "IA", "kind": "baffle_issue", "fixture_id": "OA", "baffle_state": "old",
             "fraction": 50, "witness": "w2"},
            {"id": "IB", "kind": "baffle_issue", "fixture_id": "NB", "baffle_state": "new",
             "fraction": 50, "witness": "w2"},
        ])
        # Max improvement from feed A's old reading merged with the minimum
        # miscellany of feed B's new reading - different sources.
        self.push([
            {"id": "RO", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "FA", "fixture_id": "OA", "baffle_state": "old", "grade": 8},
            {"id": "RN", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "FB", "fixture_id": "NB", "baffle_state": "new", "grade": 4},
        ])
        self.expect_error(
            [{"id": "AT", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
              "old_reading_id": "RO", "new_reading_id": "RN"}],
            "different feed sources")
        sheet = capture_sheet(self.db, "T1")
        self.assertEqual(sheet["baffle_effect_interval"], [0, 3])
        self.assertIsNone(sheet["attestation"])
        refused_modes = {r["mode"] for r in sheet["pair_candidates"]["refused"]}
        self.assertEqual(refused_modes, {"split_source", "crossover"})

    def test_unequal_split_cannot_be_certified(self):
        self.register(modes=("split_source",))
        self.split_groundwork(fractions=(60, 40))
        self.push([
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
        ])
        self.expect_error(
            [{"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
              "old_reading_id": "R1", "new_reading_id": "R2"}],
            "equal split")

    def test_plan_prefers_split_when_both_controls_are_available(self):
        # Still has split material AND a next-shift window: the recorder's
        # actual next move and the consequence of choosing it.
        self.register()
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1},
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
        ])
        self.push([{"id": "RT-manual", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "F1",
                    "old_fixture_id": "FX-O", "new_fixture_id": "FX-N", "witness": "w"}])
        sheet = capture_sheet(self.db, "T1")
        modes = {a["mode"] for a in sheet["plan"]["available_actions"]}
        self.assertEqual(modes, {"split_source", "crossover"})
        first = sheet["plan"]["recommended_first_test"]
        self.assertEqual(first["mode"], "split_source")
        self.assertEqual(first["action"], "issue_fixture")
        # Honor the recommendation: a same-round signable sheet results. The
        # fixtures already exist, so only issue the equal split portions.
        self.push([
            {"id": "IS-FX-O", "kind": "baffle_issue", "fixture_id": "FX-O",
             "baffle_state": "old", "fraction": 50, "witness": "issue-witness"},
            {"id": "IS-FX-N", "kind": "baffle_issue", "fixture_id": "FX-N",
             "baffle_state": "new", "fraction": 50, "witness": "issue-witness"},
        ])
        self.push([
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
            {"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
             "old_reading_id": "R1", "new_reading_id": "R2"},
        ])
        self.assertEqual(capture_sheet(self.db, "T1")["verdict"], "attributable")

    def test_issued_material_is_never_restarted_after_unknown_unseal(self):
        self.register()
        self.split_groundwork()
        # One portion already issued and unsettled, one unknown unseal round.
        self.push([{"id": "UN1", "kind": "baffle_unknown_unseal", "trial_id": "T1",
                    "fixture_id": "FX-N"}])
        self.expect_error(
            [{"id": "UN2", "kind": "baffle_unknown_unseal", "trial_id": "T1",
              "fixture_id": "FX-O"}], "one round of unknown unsealing")
        # The unsealed exhibit cannot be regrouped back into the old feed.
        self.expect_error(
            [{"id": "IS2", "kind": "baffle_issue", "fixture_id": "FX-N",
              "baffle_state": "new", "fraction": 50, "witness": "w2"}],
            "unsealed_unknown")
        # The same source cannot spawn a fresh retain to replace the exhibit.
        self.expect_error(
            [{"id": "RT2", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "F1",
              "old_fixture_id": "O2", "new_fixture_id": "N2", "witness": "w"}],
            "already reserved")
        self.push([{"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
                    "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8}])
        # A spent portion cannot be read a second time either.
        self.expect_error(
            [{"id": "R1b", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
              "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8}],
            "spent")
        sheet = capture_sheet(self.db, "T1")
        self.assertEqual(sheet["verdict"], "permanently_inconclusive")
        self.assertEqual(sheet["plan"]["available_actions"], [])
        self.assertEqual(sheet["baffle_effect_interval"], [0, 3])
        # With only one surviving reading no pair can be formed at all.
        self.assertEqual(sheet["pair_candidates"]["accepted"], [])
        self.assertEqual(sheet["pair_candidates"]["refused"], [])
        # The real-feed world that reverses the improvement claim stays open.
        self.assertIn({"baffle_effect": 0, "feed_effect": 3}, sheet["compatible_worlds"])

    def test_split_pair_guard_rejects_unsealed_mate_from_shared_history(self):
        # Defense in depth: even if two readings exist on the split feed, the
        # pairing predicate itself must reject the history once a mate was
        # unsealed outside witness - not merely the read-time state machine.
        self.register(modes=("split_source",))
        self.split_groundwork()
        self.push([
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
        ])
        # Force the second portion into an unsealed-then-read state directly
        # through the ledger, simulating a history the import API forbids.
        self.db.execute(
            "UPDATE baffle_fixture SET state='issued' WHERE id='FX-N'")
        self.push([
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
        ])
        self.db.execute(
            "UPDATE baffle_fixture SET state='unsealed_unknown',"
            "reason='unsealed outside witness' WHERE feed_id='F1' AND id='FX-N'")
        self.db.commit()
        self.expect_error(
            [{"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
              "old_reading_id": "R1", "new_reading_id": "R2"}],
            "unsealed")
        sheet = capture_sheet(self.db, "T1")
        self.assertTrue(any("unsealed" in r["reason"]
                            for r in sheet["pair_candidates"]["refused"]))

    def test_crossover_does_not_consume_retained_feed(self):
        self.register("T4", modes=("crossover",))
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T4", "seq": 1},
            {"id": "SH2", "kind": "baffle_shift", "trial_id": "T4", "seq": 2},
            {"id": "P1", "kind": "baffle_point", "trial_id": "T4", "role": "retrofitted",
             "baffle_state": "old"},
            {"id": "P2", "kind": "baffle_point", "trial_id": "T4", "role": "control",
             "baffle_state": "old"},
        ])
        self.push([
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T4", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
            {"id": "F2", "kind": "baffle_feed", "trial_id": "T4", "shift_id": "SH2",
             "origin_grade": 8, "calibrated_offset": 0},
        ])
        self.push([{"id": "R1", "kind": "baffle_reading", "trial_id": "T4", "shift_id": "SH1",
                    "feed_id": "F1", "point_id": "P1", "baffle_state": "old", "grade": 8}])
        self.push([{"id": "SW1", "kind": "baffle_switch", "trial_id": "T4", "point_id": "P1",
                    "shift_id": "SH2", "from_state": "old", "to_state": "new",
                    "empty_verified": True}])
        self.push([{"id": "L1", "kind": "baffle_origin_link", "trial_id": "T4", "shift_id": "SH2",
                    "retro_feed_id": "F2", "control_feed_id": "F1", "delta": 0}])
        self.push([{"id": "R2", "kind": "baffle_reading", "trial_id": "T4", "shift_id": "SH2",
                    "feed_id": "F2", "point_id": "P1", "baffle_state": "new", "grade": 5}])
        self.push([{"id": "A4", "kind": "baffle_attest", "trial_id": "T4", "mode": "crossover",
                    "old_reading_id": "R1", "new_reading_id": "R2"}])
        sheet = capture_sheet(self.db, "T4")
        self.assertEqual(sheet["baffle_effect_interval"], [3, 3])
        self.assertEqual(sheet["fixtures"], [])
        self.assertTrue(all(r["fixture_id"] is None for r in sheet["readings"]))

    def test_resource_limits_are_enforced(self):
        self.register()
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1},
            {"id": "SH2", "kind": "baffle_shift", "trial_id": "T1", "seq": 2},
        ])
        self.expect_error(
            [{"id": "SH3", "kind": "baffle_shift", "trial_id": "T1", "seq": 3}],
            "two shifts")
        self.push([
            {"id": "P1", "kind": "baffle_point", "trial_id": "T1", "role": "retrofitted",
             "baffle_state": "old"},
            {"id": "P2", "kind": "baffle_point", "trial_id": "T1", "role": "control",
             "baffle_state": "old"},
        ])
        self.expect_error(
            [{"id": "P3", "kind": "baffle_point", "trial_id": "T1", "role": "control",
              "baffle_state": "new"}], "two points")
        self.push([
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
        ])
        # Three readings is the ceiling; all without fixtures (crossover-style)
        # to avoid spending the single-use split fixtures.
        for i in range(LIMITS["readings"]):
            self.push([{"id": f"R{i}", "kind": "baffle_reading", "trial_id": "T1",
                        "shift_id": "SH1", "feed_id": "F1", "point_id": "P1",
                        "baffle_state": "old", "grade": 8}])
        self.expect_error(
            [{"id": "R9", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
              "feed_id": "F1", "point_id": "P1", "baffle_state": "old", "grade": 8}],
            "three experimental readings")

    def test_only_integer_grades_are_accepted(self):
        self.register()
        self.push([{"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1}])
        self.expect_error(
            [{"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
              "origin_grade": 8.5}], "integer grade")
        self.push([{"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
                    "origin_grade": 8, "calibrated_offset": 0}])
        self.expect_error(
            [{"id": "T1b", "kind": "baffle_trial", "site": "PT1", "old_rate": 8.0,
              "new_rate": 5,
              "possible_worlds": [{"baffle_effect": 3, "feed_effect": 0}]}],
            "integer grade")

    def test_trial_runs_independently_of_formal_classification_ledger(self):
        # Same observed 8 vs 12 as the baseline test, but the trial tables and
        # the qualified/contaminated decisions never touch each other.
        import_file(self.db, "sort-import", FIXTURES / "sort-ok.json")
        import_file(self.db, "route-import", FIXTURES / "route-ok.json")
        formal = report(self.db)
        self.assertEqual(formal["batch"][0]["state"], "qualified")
        self.register()
        # Trial-only fixtures and feed share no id with the formal ledger.
        self.push([
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1},
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 12, "calibrated_offset": 0},
        ])
        self.push([{"id": "RT1", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "F1",
                    "old_fixture_id": "FX-O", "new_fixture_id": "FX-N", "witness": "w"}])
        self.push([
            {"id": "IS-FX-O", "kind": "baffle_issue", "fixture_id": "FX-O",
             "baffle_state": "old", "fraction": 50, "witness": "w2"},
            {"id": "IS-FX-N", "kind": "baffle_issue", "fixture_id": "FX-N",
             "baffle_state": "new", "fraction": 50, "witness": "w2"},
        ])
        self.push([
            {"id": "TR1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 12},
            {"id": "TR2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
        ])
        # Trial grade 12 does not contaminate any formal batch.
        self.assertEqual(report(self.db)["batch"][0]["state"], "qualified")
        self.assertEqual(report(self.db)["load"][0]["state"], "loaded")
        sheet = capture_sheet(self.db, "T1")
        # No attestation requested, so the trial stays in its loose interval.
        self.assertEqual(sheet["verdict"], "inconclusive")

    def test_replay_from_originals_reproduces_signed_sheet(self):
        # One complete original file, imported in a single pass, so the audit
        # replay has every event that built the signed sheet.
        events = [trial_from_excerpt("T1", ("split_source",))]
        events += [
            {"id": "SH1", "kind": "baffle_shift", "trial_id": "T1", "seq": 1},
            {"id": "F1", "kind": "baffle_feed", "trial_id": "T1", "shift_id": "SH1",
             "origin_grade": 8, "calibrated_offset": 0},
            {"id": "RT-T1", "kind": "baffle_retain", "trial_id": "T1", "feed_id": "F1",
             "old_fixture_id": "FX-O", "new_fixture_id": "FX-N", "witness": "split-witness"},
            {"id": "IS-FX-O", "kind": "baffle_issue", "fixture_id": "FX-O",
             "baffle_state": "old", "fraction": 50, "witness": "issue-witness"},
            {"id": "IS-FX-N", "kind": "baffle_issue", "fixture_id": "FX-N",
             "baffle_state": "new", "fraction": 50, "witness": "issue-witness"},
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
            {"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
             "old_reading_id": "R1", "new_reading_id": "R2"},
        ]
        path = Path(self.temp.name) / "complete.json"
        path.write_text(json.dumps({"events": events}), encoding="utf-8")
        import_file(self.db, "baffle-import", path)
        self.assertEqual(capture_sheet(self.db, "T1")["baffle_effect_interval"], [3, 3])
        result = audit(self.db, HANDLERS)
        self.assertTrue(result["replay_ok"], result["mismatches"])
        self.assertEqual(result["event_count"], len(events))
        # Re-import of an already recorded file is detected by its source hash
        # and short-circuits before touching any ledger row.
        outputs = import_file(self.db, "baffle-import", path)
        self.assertTrue(all(o["result"] == "file_replayed" for o in outputs))
        # Tampering with a derived table is caught by the from-original replay.
        self.db.execute("UPDATE baffle_attestation SET effect_lower=9 WHERE id='A1'")
        self.db.commit()
        tampered = audit(self.db, HANDLERS)
        self.assertFalse(tampered["replay_ok"])
        self.assertIn("baffle_attestation", tampered["mismatches"])

    def test_cli_sheet_command_outputs_attribution(self):
        import io
        import contextlib

        db_path = Path(self.temp.name) / "cli.sqlite"
        cli_db = connect(db_path)
        cli_db.close()
        # Empty database still yields a valid, empty sheet listing.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["baffle-sheet", "--db", str(db_path)])
        self.assertEqual(json.loads(buf.getvalue())["capture_sheets"], [])
        # Populate through the import command and view the signed sheet.
        self.register()
        self.split_groundwork()
        self.push([
            {"id": "R1", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-O", "baffle_state": "old", "grade": 8},
            {"id": "R2", "kind": "baffle_reading", "trial_id": "T1", "shift_id": "SH1",
             "feed_id": "F1", "fixture_id": "FX-N", "baffle_state": "new", "grade": 5},
            {"id": "A1", "kind": "baffle_attest", "trial_id": "T1", "mode": "split_source",
             "old_reading_id": "R1", "new_reading_id": "R2"},
        ])
        sheet = capture_sheet(self.db, "T1")
        self.assertEqual(sheet["limits"]["points"], 2)
        self.assertEqual(sheet["limits"]["shifts"], 2)
        self.assertEqual(sheet["limits"]["readings"], 3)
        self.assertEqual(sheet["limits"]["unknown_unseals"], 1)
        # The same database is readable through the CLI entry point.
        main_db = connect(Path(self.temp.name) / "trial.sqlite")
        try:
            viewed = capture_sheet(main_db, "T1")
            self.assertEqual(viewed["verdict"], "attributable")
            self.assertEqual(viewed["baffle_effect_interval"], [3, 3])
        finally:
            main_db.close()


if __name__ == "__main__":
    unittest.main()
