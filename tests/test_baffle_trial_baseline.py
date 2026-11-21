import json
from pathlib import Path

from wastehandoff.__main__ import import_file
from wastehandoff.report import report
from wastehandoff.store import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_composition_is_used_for_current_sort_decisions(tmp_path):
    for contamination, status, load_state in (
        (8, "qualified", "loaded"), (12, "contaminated", "quarantine")
    ):
        source_data = json.loads((FIXTURES / "sort-ok.json").read_text(encoding="utf-8"))
        source_data["events"][-1]["contamination"] = contamination
        source = tmp_path / f"source-{contamination}.json"
        source.write_text(json.dumps(source_data), encoding="utf-8")
        db = connect(":memory:")
        try:
            import_file(db, "sort-import", source)
            import_file(db, "route-import", FIXTURES / "route-ok.json")
            data = report(db)
            assert data["batch"][0]["state"] == status
            assert data["load"][0]["state"] == load_state
        finally:
            db.close()
