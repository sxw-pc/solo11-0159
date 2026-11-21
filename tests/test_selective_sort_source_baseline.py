import json
from pathlib import Path

from wastehandoff.__main__ import HANDLERS, import_file
from wastehandoff.report import audit, report
from wastehandoff.store import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_verified_file_replay_needs_nonlocal_point_dependency(tmp_path):
    db = connect(":memory:")
    try:
        doc = json.loads((FIXTURES / "sort-ok.json").read_text(encoding="utf-8"))
        reduced = tmp_path / "batch-with-no-source-point.json"
        reduced.write_text(json.dumps({"events": doc["events"][1:]}), encoding="utf-8")
        try:
            import_file(db, "sort-import", reduced)
        except ValueError as exc:
            assert "unknown sorting point" in str(exc)
        else:
            raise AssertionError("expected an unresolved point dependency")
        assert report(db)["event"] == []
        import_file(db, "sort-import", FIXTURES / "sort-ok.json")
        assert report(db)["batch"][0]["state"] == "qualified"
        assert audit(db, HANDLERS)["replay_ok"]
    finally:
        db.close()
