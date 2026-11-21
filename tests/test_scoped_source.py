"""Tests for the restricted (scoped) sorting source.

All identities, organizations and digests are synthetic.  The suite covers:

* the baseline contrast: the existing ``sort-import`` entry point fails and
  rolls back when the point declaration is omitted from the original file;
* both real field-acquisition paths (reading-time source commitment and
  offline derived receipt) over old and new files;
* that two inputs whose visible target content is identical but whose hidden
  point differs in content/order produce identical receiver-visible output;
* that no ordinary license number, sibling count or raw hash prefix escapes,
  and that a whole-file digest cannot be passed off as a point commitment;
* stale/re-issued license association detection, sibling-point non-implication,
  and all-or-nothing import;
* repeat import and ``audit`` consistency.
"""

import copy
import json
import tempfile

import pytest

from wastehandoff import scoped_source as scoped
from wastehandoff.__main__ import HANDLERS, import_file, import_scoped
from wastehandoff.report import audit, report
from wastehandoff.store import connect

FIXTURES = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic common originals
# ---------------------------------------------------------------------------
def point(point_id, zone, category, org):
    return {"id": point_id, "kind": "point", "zone": zone,
            "category": category, "inspector_org": org}


def license_line(license_id, point_id, category, org, witness, batch_id, at,
                 supersedes=None):
    row = {"id": license_id, "kind": "license", "point_id": point_id,
           "category": category, "inspector_org": org, "witness_org": witness,
           "batch_id": batch_id, "at": at}
    if supersedes:
        row["supersedes"] = supersedes
    return row


def batch(batch_id, point_id, category, bags, kg, at):
    return {"id": batch_id, "kind": "batch", "point_id": point_id,
            "category": category, "bag_count": bags, "declared_kg": kg,
            "at": at}


def sample(sample_id, batch_id, inspector, at, contamination, refs):
    return {"id": sample_id, "kind": "sample", "batch_id": batch_id,
            "inspector": inspector, "at": at, "contamination": contamination,
            "bag_refs": list(refs)}


def common_original(seed=0, with_commitments=True):
    """One file carrying two independent sorting points.

    ``seed`` only perturbs the hidden point (B) and its row order, never the
    visible slice of point A.
    """
    events = [
        point("PT-A", "district-a", "recyclable", "classify-team"),
        point("PT-B", "district-b", "organic", "classify-team-b"),
        license_line("LIC-A1", "PT-A", "recyclable", "classify-team",
                     "witness-a", "B-A", "2026-04-01T07:00:00"),
        license_line("LIC-B1", "PT-B", "organic", "classify-team-b",
                     "witness-b", "B-B", "2026-04-01T07:00:00"),
        batch("B-A", "PT-A", "recyclable", 10, 100, "2026-04-01T08:00:00"),
        sample("S-A", "B-A", "classify-team", "2026-04-01T08:10:00", 4,
               ["bag-1", "bag-2"]),
        batch("B-B", "PT-B", "organic", 1 + seed, 50 + seed,
              "2026-04-01T08:05:00"),
        sample("S-B", "B-B", "classify-team-b", "2026-04-01T08:15:00",
               2 + seed, ["hidden-%d" % seed]),
    ]
    if seed:
        # Reorder the hidden point's rows and add extra hidden noise.
        hidden = [e for e in events if e["id"] in
                  ("PT-B", "LIC-B1", "B-B", "S-B")]
        visible = [e for e in events if e not in hidden]
        events = visible + list(reversed(hidden))
        events.append(license_line("LIC-B-extra", "PT-B", "organic",
                                   "classify-team-b", "witness-b", "B-B",
                                   "2026-04-01T07:05:00"))
        events.append(batch("B-B2", "PT-B", "organic", 7 + seed, 70 + seed,
                            "2026-04-01T08:20:00"))
    document = {"events": events, "file_sha256": ("deadbeef" * 8)}
    if with_commitments:
        document["field_commitments"] = {
            "PT-A": {"witness_org": "witness-a",
                     "slice_digest": scoped.slice_digest(document, "PT-A", "B-A")},
            "PT-B": {"witness_org": "witness-b",
                     "slice_digest": scoped.slice_digest(document, "PT-B", "B-B")},
        }
    return document


def write(tmp_path, name, obj):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def seed_official_source(db, events):
    """Register seed rows through the real import path so audit can replay."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(__import__("pathlib").Path(tmp), "seed.json",
                     {"events": events})
        return import_file(db, "sort-import", path)


# ---------------------------------------------------------------------------
# Baseline contrast: the existing entry point needs a local point declaration
# ---------------------------------------------------------------------------
def test_baseline_full_replay_qualifies_but_pointless_document_rolls_back(tmp_path):
    db = connect(":memory:")
    try:
        doc = json.loads((FIXTURES / "sort-ok.json").read_text(encoding="utf-8"))
        reduced = write(tmp_path, "reduced.json", {"events": doc["events"][1:]})
        with pytest.raises(ValueError, match="unknown sorting point"):
            import_file(db, "sort-import", reduced)
        assert report(db)["event"] == []
        assert report(db)["source_file"] == []
        import_file(db, "sort-import", FIXTURES / "sort-ok.json")
        assert report(db)["batch"][0]["state"] == "qualified"
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Path 1: reading-time source commitment (new, split-at-source material)
# ---------------------------------------------------------------------------
def test_source_commitment_package_replays_point_a_only(tmp_path):
    db = connect(":memory:")
    try:
        document = common_original()
        package = scoped.build_package(document, "PT-A", "B-A", "witness-a")
        path = write(tmp_path, "scoped-a.json", package)
        result = import_scoped(db, path)

        assert result["result"] == "imported"
        assert result["authorization"] == {
            "target_point": "PT-A", "batch_id": "B-A", "category": "recyclable",
            "witness_org": "witness-a",
            "credential": scoped.RECEIPT_SOURCE, "authorized": True}
        ledger = report(db)
        assert [p["id"] for p in ledger["point"]] == ["PT-A"]
        assert [b["id"] for b in ledger["batch"]] == ["B-A"]
        assert [s["id"] for s in ledger["sample"]] == ["S-A"]
        assert ledger["batch"][0]["state"] == "qualified"
        # The sibling point's identity, batch and sample are absent entirely.
        blob = json.dumps(ledger, ensure_ascii=False)
        for hidden in ("PT-B", "B-B", "S-B", "hidden-"):
            assert hidden not in blob
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


def test_source_commitment_requires_independent_witness_reveal(tmp_path):
    package = scoped.build_package(common_original(), "PT-A", "B-A",
                                   "witness-a")
    # Re-labelling the signer as the inspector (self-witness) breaks the
    # independent reveal even though every visible field looks correct.
    forged = copy.deepcopy(package)
    forged["credential"] = scoped.make_credential(
        scoped.RECEIPT_SOURCE, forged["scope"],
        forged["credential"]["visible_digest"], "classify-team")
    with pytest.raises(ValueError, match="no independent witness credential"):
        scoped.replay_events(forged)


# ---------------------------------------------------------------------------
# Path 2: offline derived receipt over an old full archive
# ---------------------------------------------------------------------------
def test_derived_receipt_over_old_archive(tmp_path):
    db = connect(":memory:")
    try:
        # Old archive: no reading-time per-point commitments at all.
        package = scoped.build_package(common_original(with_commitments=False),
                                       "PT-A", "B-A", "witness-a",
                                       credential_kind=scoped.RECEIPT_DERIVED)
        assert package["credential"]["kind"] == scoped.RECEIPT_DERIVED
        assert package["credential"]["signer_org"] == "classify-team"
        path = write(tmp_path, "derived-a.json", package)
        assert import_scoped(db, path)["result"] == "imported"
        assert report(db)["batch"][0]["state"] == "qualified"
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


def test_derived_receipt_refused_without_complete_archive(tmp_path):
    with pytest.raises(ValueError, match="complete original"):
        scoped.build_package(common_original(with_commitments=False), "PT-A",
                             "B-A", "witness-a",
                             credential_kind=scoped.RECEIPT_DERIVED,
                             full_archive_present=False)


def test_derived_receipt_cannot_vouch_for_a_later_slice(tmp_path):
    db = connect(":memory:")
    try:
        document = common_original(with_commitments=False)
        package = scoped.build_package(document, "PT-A", "B-A", "witness-a",
                                       credential_kind=scoped.RECEIPT_DERIVED)
        # Extend the visible slice after the receipt was signed: the derived
        # receipt must not bless the extra sample.
        extended = copy.deepcopy(package)
        extended["visible"]["samples"].append(
            {"id": "S-A-late", "batch_id": "B-A", "inspector": "classify-team",
             "at": "2026-04-01T09:00:00", "contamination": 1,
             "bag_refs": ["bag-9"]})
        with pytest.raises(ValueError):
            scoped.replay_events(extended)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Hidden-point invariance and non-leakage
# ---------------------------------------------------------------------------
def test_two_originals_differing_only_in_hidden_point_are_indistinguishable(
        tmp_path):
    first = scoped.build_package(common_original(seed=0), "PT-A", "B-A",
                                 "witness-a")
    second = scoped.build_package(common_original(seed=5), "PT-A", "B-A",
                                  "witness-a")
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)

    # Receiver-visible output, error behaviour and event order must match too,
    # including for a package that is rejected.
    outputs = []
    for doc in (common_original(seed=0), common_original(seed=5)):
        db = connect(":memory:")
        path = write(tmp_path, "same.json", scoped.build_package(
            doc, "PT-A", "B-A", "witness-a"))
        outputs.append(json.dumps(import_scoped(db, path), sort_keys=True))
        db.close()
    assert outputs[0] == outputs[1]

    # A rejection reason derived from the hidden side must not differ either.
    bad_a = copy.deepcopy(first)
    bad_a["credential"]["proof"] = "0" * 64
    bad_b = copy.deepcopy(second)
    bad_b["credential"]["proof"] = "0" * 64
    msg = []
    for package in (bad_a, bad_b):
        try:
            scoped.replay_events(package)
        except ValueError as exc:
            msg.append(str(exc))
    assert msg[0] == msg[1]


def test_no_license_number_sibling_count_or_raw_hash_is_exported():
    package = scoped.build_package(common_original(), "PT-A", "B-A",
                                   "witness-a")
    blob = json.dumps(package, ensure_ascii=False)
    # Ordinary license numbers from either point.
    assert "LIC-A1" not in blob and "LIC-B1" not in blob
    # The whole-file digest, and any short form of it (raw hash codes).
    assert "deadbeef" not in blob
    assert "file_sha256" not in blob
    # The sibling's batch/sample identities, counts and bag references.
    for hidden in ("PT-B", "B-B", "S-B", "hidden-"):
        assert hidden not in blob
    # The visible slice carries exactly one batch and one sample.
    assert len(package["visible"]["batches"]) == 1
    assert len(package["visible"]["samples"]) == 1


def test_whole_file_digest_is_not_a_point_commitment(tmp_path):
    document = common_original(with_commitments=False)
    document["file_sha256"] = __import__("hashlib").sha256(
        json.dumps(document["events"]).encode()).hexdigest()
    with pytest.raises(ValueError, match="whole-file digest"):
        scoped.build_package(document, "PT-A", "B-A", "witness-a")
    # No unsalted digest of all hidden fields may stand in for point proof.
    document["field_commitments"] = {
        "PT-A": {"witness_org": "witness-a",
                 "slice_digest": __import__("hashlib").sha256(
                     json.dumps(document["events"], sort_keys=True).encode()
                 ).hexdigest()}}
    with pytest.raises(ValueError, match="whole-file digest"):
        scoped.build_package(document, "PT-A", "B-A", "witness-a")


def test_another_point_license_on_a_common_original_is_not_implied():
    with pytest.raises(ValueError, match="no current authorization"):
        scoped.build_package(common_original(), "PT-A", "B-A", "witness-b")
    # Likewise the target's own category grant does not authorize point B.
    with pytest.raises(ValueError, match="no current authorization"):
        scoped.build_package(common_original(), "PT-B", "B-B", "witness-a")


# ---------------------------------------------------------------------------
# Stale association swap
# ---------------------------------------------------------------------------
def test_reissued_license_association_is_detected(tmp_path):
    db = connect(":memory:")
    try:
        package = scoped.build_package(common_original(), "PT-A", "B-A",
                                       "witness-a")
        # The ledger already saw witness-a authorized for B-A, then a re-issue
        # moved the license to witness-c.  Visible sample fields are identical.
        seed_official_source(db, [
            point("PT-A", "district-a", "recyclable", "classify-team"),
            license_line("LIC-A1", "PT-A", "recyclable", "classify-team",
                         "witness-a", "B-A", "2026-04-01T07:00:00"),
            license_line("LIC-A2", "PT-A", "recyclable", "classify-team",
                         "witness-c", "B-A", "2026-04-01T07:30:00",
                         supersedes="LIC-A1"),
        ])
        before = report(db)
        path = write(tmp_path, "stale.json", package)
        with pytest.raises(ValueError, match="no longer current"):
            import_scoped(db, path)
        assert report(db) == before
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


def test_current_reissue_package_is_accepted(tmp_path):
    db = connect(":memory:")
    try:
        document = common_original()
        document["events"].append(license_line(
            "LIC-A2", "PT-A", "recyclable", "classify-team", "witness-c",
            "B-A", "2026-04-01T07:30:00", supersedes="LIC-A1"))
        document["field_commitments"]["PT-A"] = {
            "witness_org": "witness-c",
            "slice_digest": scoped.slice_digest(document, "PT-A", "B-A")}
        seed_official_source(db, [
            point("PT-A", "district-a", "recyclable", "classify-team"),
            license_line("LIC-A1", "PT-A", "recyclable", "classify-team",
                         "witness-a", "B-A", "2026-04-01T07:00:00"),
            license_line("LIC-A2", "PT-A", "recyclable", "classify-team",
                         "witness-c", "B-A", "2026-04-01T07:30:00",
                         supersedes="LIC-A1"),
        ])
        package = scoped.build_package(document, "PT-A", "B-A", "witness-c")
        assert import_scoped(db, write(tmp_path, "current.json",
                                       package))["result"] == "imported"
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Explaining a fully-populated package that still cannot enter the ledger
# ---------------------------------------------------------------------------
def test_package_with_all_visible_fields_can_still_be_refused(tmp_path):
    package = scoped.build_package(common_original(), "PT-A", "B-A",
                                   "witness-a")
    # Every visible field is intact; only the credential signature was made by
    # the wrong organization.  explain_rejection names the missing evidence.
    package = copy.deepcopy(package)
    package["credential"] = scoped.make_credential(
        scoped.RECEIPT_SOURCE, package["scope"],
        package["credential"]["visible_digest"], "witness-z")
    db = connect(":memory:")
    try:
        verdict = scoped.explain_rejection(db, package)
        assert verdict["admitted"] is False
        assert "witness credential" in verdict["reason"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Merging two independent packages
# ---------------------------------------------------------------------------
def test_overlapping_packages_merge_only_the_common_licensed_part():
    package = scoped.build_package(common_original(), "PT-A", "B-A",
                                   "witness-a")
    merged = scoped.merge_packages(package, copy.deepcopy(package))
    assert merged["scope"] == package["scope"]
    assert merged["visible"] == package["visible"]
    assert merged["merged_from"] == 2

    # A derived receipt cannot be composed with a source commitment.
    derived = scoped.build_package(common_original(with_commitments=False),
                                   "PT-A", "B-A", "witness-a",
                                   credential_kind=scoped.RECEIPT_DERIVED)
    with pytest.raises(ValueError, match="identical licensed part"):
        scoped.merge_packages(package, derived)


# ---------------------------------------------------------------------------
# Repeat import and audit consistency
# ---------------------------------------------------------------------------
def test_repeat_import_and_audit_are_consistent(tmp_path):
    db = connect(":memory:")
    try:
        package = scoped.build_package(common_original(), "PT-A", "B-A",
                                       "witness-a")
        path = write(tmp_path, "repeat.json", package)
        assert import_scoped(db, path)["result"] == "imported"
        snapshot = report(db)
        second = import_scoped(db, path)
        assert second["result"] == "file_replayed"
        assert report(db) == snapshot
        assert [s["command"] for s in report(db)["source_file"]] == [
            scoped.SCOPED_COMMAND]
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()


def test_both_field_paths_coexist_in_one_ledger(tmp_path):
    db = connect(":memory:")
    try:
        new_package = scoped.build_package(common_original(), "PT-A", "B-A",
                                           "witness-a")
        old_package = scoped.build_package(common_original(with_commitments=False),
                                           "PT-B", "B-B", "witness-b",
                                           credential_kind=scoped.RECEIPT_DERIVED)
        assert import_scoped(db, write(tmp_path, "new.json",
                                       new_package))["result"] == "imported"
        assert import_scoped(db, write(tmp_path, "old.json",
                                       old_package))["result"] == "imported"
        ledger = report(db)
        assert {p["id"] for p in ledger["point"]} == {"PT-A", "PT-B"}
        assert {b["id"] for b in ledger["batch"]} == {"B-A", "B-B"}
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()
