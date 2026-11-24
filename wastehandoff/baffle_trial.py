"""Partition-baffle controlled-trial capture sheet and evidence attribution.

The formal sorting ledger (sample/recompute in sorting.py) keeps deciding
batch qualification on its own. This module only answers whether the missing
miscellany between two trial readings was actually produced by swapping the
baffle. Two authorized controls exist:

* split_source - one truck's retained standard feed is halved into two
  single-use fixtures, run in the same round under the old and new baffle,
  with a witnessed two-way equal split;
* crossover - the retrofitted point alternates baffle state across adjacent
  shifts while an unmodified point keeps a same-origin feed control, which
  may only certify the retrofit when each cross-shift feed change is
  simultaneously controlled and an empty-load original proves no carryover.

No reading pair is signable unless it stands on one feed source and one
equipment history. Everything here is synthetic integer grades; no real
device is contacted.
"""
import json

from .store import one, many, log

# At most two points and two shifts, three experimental readings and one
# round of unknown unsealing per trial.
LIMITS = {"points": 2, "shifts": 2, "readings": 3, "unknown_unseals": 1, "feeds_per_shift": 2}
BAFFLES = ("old", "new")
MODES = ("split_source", "crossover")
ROLES = ("retrofitted", "control")


def _trial(db, trial_id):
    t = one(db, "SELECT * FROM baffle_trial WHERE id=?", (trial_id,))
    if not t:
        raise ValueError("unknown baffle trial")
    return t


def _grade(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError(field + " must be a synthetic integer grade in 0..100")
    return value


def _worlds(e):
    worlds = e["possible_worlds"]
    for w in worlds:
        _grade(w["baffle_effect"], "baffle_effect")
        _grade(w["feed_effect"], "feed_effect")
    if not worlds:
        raise ValueError("at least one calibrated real-feed world required")
    return worlds


def trial(db, e):
    old = _grade(e["old_rate"], "old_rate")
    new = _grade(e["new_rate"], "new_rate")
    if one(db, "SELECT id FROM baffle_trial WHERE id=?", (e["id"],)):
        raise ValueError("trial id already registered")
    modes = e.get("modes", list(MODES))
    if not modes or any(m not in MODES for m in modes):
        raise ValueError("permitted modes must be a non-empty subset of the two authorized controls")
    delta = old - new
    worlds = _worlds(e)
    for w in worlds:
        if w["baffle_effect"] + w["feed_effect"] != delta:
            raise ValueError("each real-feed world must partition the observed difference")
    # The loose interval before any controlled pair exists: the whole drop
    # could be the baffle, or none of it could be.
    state, reason = "open", "no controlled pair yet; observed difference is not attributable"
    db.execute(
        "INSERT INTO baffle_trial(id,site,old_rate,new_rate,observed_delta,loose_lower,loose_upper,"
        "modes,worlds,status,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (e["id"], e["site"], old, new, delta, 0, delta, json.dumps(modes), json.dumps(worlds), state, reason),
    )
    log(db, "baffle", e["id"], state, reason, e["id"])
    return state


def shift(db, e):
    t = _trial(db, e["trial_id"])
    if one(db, "SELECT id FROM baffle_shift WHERE trial_id=? AND seq=?", (t["id"], e["seq"])):
        raise ValueError("shift sequence already used in this trial")
    used = len(many(db, "SELECT id FROM baffle_shift WHERE trial_id=?", (t["id"],)))
    if used >= LIMITS["shifts"]:
        raise ValueError("trial is limited to two shifts")
    if not isinstance(e["seq"], int) or isinstance(e["seq"], bool) or e["seq"] < 1:
        raise ValueError("shift sequence must be a positive integer")
    db.execute("INSERT INTO baffle_shift(id,trial_id,seq) VALUES(?,?,?)", (e["id"], t["id"], e["seq"]))
    log(db, "baffle", e["id"], "opened", "trial shift opened", e["id"])
    return "opened"


def feed_source(db, e):
    t = _trial(db, e["trial_id"])
    sh = one(db, "SELECT * FROM baffle_shift WHERE id=? AND trial_id=?", (e["shift_id"], t["id"]))
    if not sh:
        raise ValueError("feed source must belong to a registered shift of this trial")
    # One common feed offset per source per shift, calibrated by a real
    # original; two distinct sources may coexist precisely so that a
    # cross-source merge can be recorded and then refused at attestation.
    if len(many(db, "SELECT id FROM baffle_feed WHERE trial_id=? AND shift_id=?",
                (t["id"], sh["id"]))) >= LIMITS["feeds_per_shift"]:
        raise ValueError("a shift carries at most two calibrated feed sources")
    origin = e.get("origin_grade")
    if origin is not None:
        _grade(origin, "origin_grade")
    offset = e.get("calibrated_offset")
    if offset is not None and (not isinstance(offset, int) or isinstance(offset, bool)):
        raise ValueError("calibrated offset must be an integer")
    db.execute(
        "INSERT INTO baffle_feed(id,trial_id,shift_id,origin_grade,calibrated_offset) VALUES(?,?,?,?,?)",
        (e["id"], t["id"], sh["id"], origin, offset),
    )
    log(db, "baffle", e["id"], "registered", "feed source with calibrated common offset", e["id"])
    return "registered"


def enroll_point(db, e):
    t = _trial(db, e["trial_id"])
    if e["role"] not in ROLES or e["baffle_state"] not in BAFFLES:
        raise ValueError("point role must be retrofitted/control and state old/new")
    if len(many(db, "SELECT id FROM baffle_point WHERE trial_id=?", (t["id"],))) >= LIMITS["points"]:
        raise ValueError("trial is limited to two points")
    if one(db, "SELECT id FROM baffle_point WHERE trial_id=? AND role=?", (t["id"], e["role"])):
        raise ValueError("each trial point role can be enrolled once")
    db.execute(
        "INSERT INTO baffle_point(id,trial_id,role,baffle_state) VALUES(?,?,?,?)",
        (e["id"], t["id"], e["role"], e["baffle_state"]),
    )
    log(db, "baffle", e["id"], "enrolled", e["role"] + " point holding " + e["baffle_state"] + " baffle", e["id"])
    return "enrolled"


def retain_feed(db, e):
    """Split-source control: reserve one truck's standard feed for two fixtures."""
    t = _trial(db, e["trial_id"])
    if "split_source" not in json.loads(t["modes"]):
        raise ValueError("split_source capture is not authorized for this trial")
    feed = one(db, "SELECT * FROM baffle_feed WHERE id=? AND trial_id=?", (e["feed_id"], t["id"]))
    if not feed:
        raise ValueError("retained feed must be a registered source of this trial")
    if one(db, "SELECT id FROM baffle_fixture WHERE feed_id=?", (feed["id"],)):
        raise ValueError("this feed is already reserved for a split pair")
    if not e.get("witness"):
        raise ValueError("retained feed needs a split witness")
    for fid in (e["old_fixture_id"], e["new_fixture_id"]):
        fx = one(db, "SELECT * FROM baffle_fixture WHERE id=? AND trial_id=?", (fid, t["id"]))
        if fx:
            raise ValueError("fixture already exists: " + fid)
    for fid, side in ((e["old_fixture_id"], "old"), (e["new_fixture_id"], "new")):
        db.execute(
            "INSERT INTO baffle_fixture(id,trial_id,feed_id,state,reason,old_fraction,new_fraction,witness)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (fid, t["id"], feed["id"], "held", "retained standard feed awaiting equal split",
             None, None, e["witness"]),
        )
        log(db, "baffle", fid, "held", "standard feed reserved for " + side + " baffle", e["id"])
    return "held"


def issue_fixture(db, e):
    """Witnessed two-way equal split; the fixture can only be used once."""
    fx = one(db, "SELECT * FROM baffle_fixture WHERE id=?", (e["fixture_id"],))
    if not fx:
        raise ValueError("unknown fixture")
    t = _trial(db, fx["trial_id"])
    if fx["state"] != "held":
        # Issued-but-unsettled or spent material cannot be re-opened, so an
        # already-consumed physical exhibit is never restarted.
        raise ValueError("fixture is " + fx["state"] + ": " + fx["reason"])
    if not isinstance(e["fraction"], int) or isinstance(e["fraction"], bool) or e["fraction"] <= 0:
        raise ValueError("split fraction must be a positive integer share")
    side = "old_fraction" if e["baffle_state"] == "old" else "new_fraction"
    if one(db, "SELECT id FROM baffle_fixture WHERE feed_id=? AND " + side + " IS NOT NULL", (fx["feed_id"],)):
        raise ValueError("a baffle side receives exactly one split portion")
    if not e.get("witness"):
        raise ValueError("split issue needs a witness")
    db.execute(
        "UPDATE baffle_fixture SET state='issued',reason='equal split portion issued, single use',"
        "old_fraction=?,new_fraction=?,witness=? WHERE id=?",
        (e["fraction"] if e["baffle_state"] == "old" else fx["old_fraction"],
         e["fraction"] if e["baffle_state"] == "new" else fx["new_fraction"],
         e["witness"], fx["id"]),
    )
    log(db, "baffle", fx["id"], "issued", "two-way equal split portion issued", e["id"])
    return "issued"


def _split_equal(db, feed_id):
    """The two portions of one retained feed must be witnessed as equal."""
    rows = many(db, "SELECT * FROM baffle_fixture WHERE feed_id=? ORDER BY id", (feed_id,))
    if len(rows) != 2:
        return False, "retained feed does not form a two-portion split"
    fracs = [r["old_fraction"] if r["old_fraction"] is not None else r["new_fraction"] for r in rows]
    if any(f is None for f in fracs) or fracs[0] != fracs[1]:
        return False, "two-way equal split witness missing or shares differ"
    return True, "equal split on one retained feed source"


def switch_baffle(db, e):
    t = _trial(db, e["trial_id"])
    pt = one(db, "SELECT * FROM baffle_point WHERE id=? AND trial_id=?", (e["point_id"], t["id"]))
    sh = one(db, "SELECT * FROM baffle_shift WHERE id=? AND trial_id=?", (e["shift_id"], t["id"]))
    if not pt or not sh:
        raise ValueError("switch needs a registered point and shift of this trial")
    if e["from_state"] not in BAFFLES or e["to_state"] not in BAFFLES or e["from_state"] == e["to_state"]:
        raise ValueError("switch must move between distinct old/new baffle states")
    db.execute(
        "INSERT INTO baffle_switch(id,trial_id,point_id,shift_id,from_state,to_state,empty_verified)"
        " VALUES(?,?,?,?,?,?,?)",
        (e["id"], t["id"], pt["id"], sh["id"], e["from_state"], e["to_state"], 1 if e.get("empty_verified") else 0),
    )
    db.execute("UPDATE baffle_point SET baffle_state=? WHERE id=?", (e["to_state"], pt["id"]))
    state = "switched_verified" if e.get("empty_verified") else "switched_unverified"
    reason = "empty-load original proves no prior-batch residue" if e.get("empty_verified") \
        else "no empty-load original: later readings may be permanently unattributable"
    log(db, "baffle", e["id"], state, reason, e["id"])
    return state


def link_same_source(db, e):
    """Certify that two shift feeds share one origin and record the offset."""
    t = _trial(db, e["trial_id"])
    sh = one(db, "SELECT * FROM baffle_shift WHERE id=? AND trial_id=?", (e["shift_id"], t["id"]))
    if not sh:
        raise ValueError("origin link belongs to a registered shift")
    retro = one(db, "SELECT * FROM baffle_feed WHERE id=? AND trial_id=?", (e["retro_feed_id"], t["id"]))
    control = one(db, "SELECT * FROM baffle_feed WHERE id=? AND trial_id=?", (e["control_feed_id"], t["id"]))
    if not retro or not control or retro["id"] == control["id"]:
        raise ValueError("origin link needs two distinct registered feeds")
    for f in (retro, control):
        if f["origin_grade"] is None:
            raise ValueError("same-origin feeds must carry a verified origin grade")
    if retro["origin_grade"] != control["origin_grade"]:
        raise ValueError("linked feeds do not share a verified origin")
    if not isinstance(e["delta"], int) or isinstance(e["delta"], bool):
        raise ValueError("controlled feed delta must be an integer")
    db.execute(
        "INSERT INTO baffle_origin_link(id,trial_id,shift_id,retro_feed_id,control_feed_id,delta)"
        " VALUES(?,?,?,?,?,?)",
        (e["id"], t["id"], sh["id"], retro["id"], control["id"], e["delta"]),
    )
    log(db, "baffle", e["id"], "linked", "cross-shift feed change simultaneously controlled", e["id"])
    return "linked"


def _provenance(db, t, feed, fx, point):
    sh = one(db, "SELECT * FROM baffle_shift WHERE id=?", (feed["shift_id"],))
    switches = many(
        db, "SELECT * FROM baffle_switch WHERE trial_id=? AND point_id=? AND shift_id=?",
        (t["id"], point["id"] if point else "", sh["id"]),
    ) if point else []
    unverified = [s for s in switches if not s["empty_verified"]]
    link = one(
        db,
        "SELECT * FROM baffle_origin_link WHERE trial_id=? AND shift_id=? AND "
        "(retro_feed_id=? OR control_feed_id=?)",
        (t["id"], sh["id"], feed["id"], feed["id"]),
    )
    if fx:
        contrastable = fx["state"] in ("issued",)
        basis = "split_source"
    else:
        contrastable = bool(link) and not unverified and bool(point)
        basis = "crossover" if link else "unlinked_reading"
    return {
        "feed_id": feed["id"],
        "shift_id": sh["id"],
        "shift_seq": sh["seq"],
        "point_id": point["id"] if point else None,
        "baffle_state": point["baffle_state"] if point else None,
        "fixture_id": fx["id"] if fx else None,
        "basis": basis,
        "origin_link_id": link["id"] if link else None,
        "unverified_switches": [s["id"] for s in unverified],
        "contrastable": contrastable,
    }


def reading(db, e):
    t = _trial(db, e["trial_id"])
    if len(many(db, "SELECT id FROM baffle_reading WHERE trial_id=?", (t["id"],))) >= LIMITS["readings"]:
        raise ValueError("trial is limited to three experimental readings")
    feed = one(db, "SELECT * FROM baffle_feed WHERE id=? AND trial_id=?", (e["feed_id"], t["id"]))
    if not feed:
        raise ValueError("reading needs a registered feed source")
    fx = None
    if e.get("fixture_id"):
        fx = one(db, "SELECT * FROM baffle_fixture WHERE id=? AND trial_id=?", (e["fixture_id"], t["id"]))
        if not fx:
            raise ValueError("unknown fixture")
        if fx["feed_id"] != feed["id"]:
            raise ValueError("fixture and reading do not share the same feed source")
        if fx["state"] != "issued":
            raise ValueError("fixture is " + fx["state"] + "; a split portion is read exactly once")
    point = None
    if e.get("point_id"):
        point = one(db, "SELECT * FROM baffle_point WHERE id=? AND trial_id=?", (e["point_id"], t["id"]))
        if not point:
            raise ValueError("unknown trial point")
        if point["baffle_state"] != e["baffle_state"]:
            raise ValueError("reading baffle state does not match the point's equipment history")
    if e["baffle_state"] not in BAFFLES:
        raise ValueError("reading baffle state must be old or new")
    grade = _grade(e["grade"], "grade")
    prov = _provenance(db, t, feed, fx, point)
    db.execute(
        "INSERT INTO baffle_reading(id,trial_id,shift_id,feed_id,fixture_id,point_id,baffle_state,grade,"
        "provenance,contrastable) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (e["id"], t["id"], feed["shift_id"], feed["id"], fx["id"] if fx else None,
         point["id"] if point else None, e["baffle_state"], grade, json.dumps(prov), 1 if prov["contrastable"] else 0),
    )
    if fx:
        db.execute(
            "UPDATE baffle_fixture SET state='spent',reason='single-use portion opened and read' WHERE id=?",
            (fx["id"],),
        )
    log(db, "baffle", e["id"],
        "contrastable" if prov["contrastable"] else "uncontrastable",
        "control provenance registered before sampling: " + prov["basis"], e["id"])
    return "contrastable" if prov["contrastable"] else "uncontrastable"


def unknown_unseal(db, e):
    """One permitted round of unknown unsealing per trial."""
    t = _trial(db, e["trial_id"])
    used = len(many(db, "SELECT id FROM baffle_fixture WHERE trial_id=? AND state='unsealed_unknown'", (t["id"],)))
    if used >= LIMITS["unknown_unseals"]:
        raise ValueError("trial already had its one round of unknown unsealing")
    fx = one(db, "SELECT * FROM baffle_fixture WHERE id=? AND trial_id=?", (e["fixture_id"], t["id"]))
    if not fx:
        raise ValueError("unknown fixture")
    if fx["state"] in ("spent", "unsealed_unknown"):
        raise ValueError("fixture is " + fx["state"] + ": " + fx["reason"])
    db.execute(
        "UPDATE baffle_fixture SET state='unsealed_unknown',"
        "reason='unsealed outside witness: cannot be regrouped to recover old feed' WHERE id=?",
        (fx["id"],),
    )
    log(db, "baffle", fx["id"], "unsealed_unknown",
        "exhibit compromised; split reuse permanently closed", e["id"])
    return "unsealed_unknown"


def _split_pair(db, t, old, new):
    if not (old["fixture_id"] and new["fixture_id"]):
        return False, "one side is not a split-source fixture reading"
    ofx = one(db, "SELECT * FROM baffle_fixture WHERE id=?", (old["fixture_id"],))
    nfx = one(db, "SELECT * FROM baffle_fixture WHERE id=?", (new["fixture_id"],))
    if ofx["feed_id"] != nfx["feed_id"] or old["feed_id"] != new["feed_id"]:
        return False, "readings come from different feed sources and cannot be merged into one baffle conclusion"
    if old["shift_id"] != new["shift_id"]:
        return False, "split pair must run in the same round/shift"
    # A compromised mate anywhere in this split destroys the two-portion
    # exhibit: the pair can never be regrouped into the old feed.
    mates = many(db, "SELECT state FROM baffle_fixture WHERE feed_id=?", (ofx["feed_id"],))
    if any(m["state"] == "unsealed_unknown" for m in mates):
        return False, "one split portion was unsealed outside witness and cannot be recovered"
    ok, why = _split_equal(db, ofx["feed_id"])
    if not ok:
        return False, why
    if not old["contrastable"] or not new["contrastable"]:
        return False, "one reading lacks contrastable control provenance"
    return True, why


def _crossover_pair(db, t, old, new):
    if old["fixture_id"] or new["fixture_id"]:
        return False, "crossover readings must not consume retained standard feed"
    if not old["point_id"] or not new["point_id"] or old["point_id"] != new["point_id"]:
        return False, "crossover pair needs the same retrofitted point across adjacent shifts"
    # Provenance is re-evaluated against the shared history at sign time: the
    # same-origin link may arrive after the first shift's reading was taken.
    unverified = many(
        db, "SELECT * FROM baffle_switch WHERE trial_id=? AND point_id=? AND empty_verified=0",
        (t["id"], old["point_id"]),
    )
    if unverified:
        return False, "a switch lacks an empty-load original: carryover makes the reading unattributable"
    link = one(
        db,
        "SELECT * FROM baffle_origin_link WHERE trial_id=? AND "
        "(retro_feed_id=? OR control_feed_id=?) AND (retro_feed_id=? OR control_feed_id=?)",
        (t["id"], old["feed_id"], old["feed_id"], new["feed_id"], new["feed_id"]),
    )
    if not link:
        return False, "cross-shift feed change is not simultaneously controlled by a same-origin link"
    return True, "same point equipment history; feed delta " + str(link["delta"]) + " controlled"


def attest_pair(db, e):
    """Sign a capture sheet only when a strict control stands on one history."""
    t = _trial(db, e["trial_id"])
    if e["mode"] not in MODES:
        raise ValueError("attestation mode must be one of the two authorized controls")
    if one(db, "SELECT id FROM baffle_attestation WHERE trial_id=?", (t["id"],)):
        raise ValueError("trial already has a signed capture sheet")
    old = one(db, "SELECT * FROM baffle_reading WHERE id=? AND trial_id=?", (e["old_reading_id"], t["id"]))
    new = one(db, "SELECT * FROM baffle_reading WHERE id=? AND trial_id=?", (e["new_reading_id"], t["id"]))
    if not old or not new:
        raise ValueError("attestation needs two registered trial readings")
    if old["baffle_state"] != "old" or new["baffle_state"] != "new":
        raise ValueError("pair must place the old reading against the new reading")
    if e["mode"] == "split_source":
        ok, why = _split_pair(db, t, old, new)
        effect = old["grade"] - new["grade"]
        lo = hi = effect
        key = "feed:" + old["feed_id"] + "|shift:" + old["shift_id"]
    else:
        ok, why = _crossover_pair(db, t, old, new)
        link = one(
            db,
            "SELECT * FROM baffle_origin_link WHERE trial_id=? AND "
            "(retro_feed_id=? OR control_feed_id=?) AND (retro_feed_id=? OR control_feed_id=?)",
            (t["id"], old["feed_id"], old["feed_id"], new["feed_id"], new["feed_id"]),
        )
        # Observed drop minus the simultaneously controlled feed movement.
        effect = (old["grade"] - new["grade"]) - link["delta"]
        lo = hi = effect
        key = "point:" + old["point_id"] + "|link:" + link["id"]
    if not ok:
        log(db, "baffle", t["id"], "attestation_refused", why, e["id"])
        raise ValueError("capture sheet not signable: " + why)
    db.execute(
        "INSERT INTO baffle_attestation(id,trial_id,mode,old_reading_id,new_reading_id,effect_lower,"
        "effect_upper,provenance_key,reason) VALUES(?,?,?,?,?,?,?,?,?)",
        (e["id"], t["id"], e["mode"], old["id"], new["id"], lo, hi, key, why),
    )
    db.execute(
        "UPDATE baffle_trial SET status='attested',reason=? WHERE id=?",
        ("signed " + e["mode"] + " pair on one feed source and equipment history", t["id"]),
    )
    log(db, "baffle", t["id"], "attested",
        "baffle effect signed as [" + str(lo) + "," + str(hi) + "] on " + key, e["id"])
    return "attested"


def _pair_candidates(db, t):
    rows = many(db, "SELECT * FROM baffle_reading WHERE trial_id=? ORDER BY id", (t["id"],))
    olds = [r for r in rows if r["baffle_state"] == "old"]
    news = [r for r in rows if r["baffle_state"] == "new"]
    accepted, refused = [], []
    for o in olds:
        for n in news:
            for mode, checker in (("split_source", _split_pair), ("crossover", _crossover_pair)):
                ok, why = checker(db, t, o, n)
                item = {"mode": mode, "old_reading_id": o["id"], "new_reading_id": n["id"], "reason": why}
                (accepted if ok else refused).append(item)
    return accepted, refused


def _plan(db, t):
    # A signed capture sheet closes the trial: no further first test or next
    # step can alter an attribution already fixed on one history.
    if one(db, "SELECT id FROM baffle_attestation WHERE trial_id=?", (t["id"],)):
        return {"available_actions": [], "recommended_first_test": None}
    modes = json.loads(t["modes"])
    fixtures = many(db, "SELECT * FROM baffle_fixture WHERE trial_id=?", (t["id"],))
    shifts = many(db, "SELECT id FROM baffle_shift WHERE trial_id=? ORDER BY seq", (t["id"],))
    readings = many(db, "SELECT id FROM baffle_reading WHERE trial_id=?", (t["id"],))
    points = many(db, "SELECT id FROM baffle_point WHERE trial_id=?", (t["id"],))
    actions = []
    held = [f for f in fixtures if f["state"] == "held"]
    issued = [f for f in fixtures if f["state"] == "issued"]
    unsealed = [f for f in fixtures if f["state"] == "unsealed_unknown"]
    if "split_source" in modes:
        if held:
            actions.append({
                "action": "issue_fixture", "mode": "split_source",
                "fixture_id": held[0]["id"],
                "effect": "witness the equal split now; the round is certifiable without waiting for another shift",
            })
        elif issued:
            actions.append({
                "action": "reading", "mode": "split_source",
                "fixture_id": issued[0]["id"],
                "effect": "read the issued portion once; its mate then completes a same-feed signable pair",
            })
        elif not unsealed and len(readings) < LIMITS["readings"]:
            actions.append({
                "action": "retain_feed", "mode": "split_source",
                "effect": "reserve one truck's standard feed; consumes the single-use fixtures for this trial",
            })
    if "crossover" in modes and not unsealed and len(shifts) < LIMITS["shifts"] and len(points) < LIMITS["points"]:
        actions.append({
            "action": "enroll_point/switch_baffle", "mode": "crossover",
            "effect": "alternate on the next shift window; needs an empty-load original and a same-origin control, "
                      "and keeps the retained standard feed unspent",
        })
    # First test preference: a split that can be honored in the current round
    # beats waiting on an alternating shift, because an unverified switch can
    # make current readings permanently unattributable.
    first = next((a for a in actions if a["mode"] == "split_source"), actions[0] if actions else None)
    return {"available_actions": actions, "recommended_first_test": first}


def capture_sheet(db, trial_id=None):
    """Assemble the executable capture sheet and evidence-attribution output."""
    ids = [r["id"] for r in db.execute("SELECT id FROM baffle_trial ORDER BY rowid").fetchall()] \
        if trial_id is None else [trial_id]
    out = []
    for tid in ids:
        t = one(db, "SELECT * FROM baffle_trial WHERE id=?", (tid,))
        if not t:
            raise ValueError("unknown baffle trial")
        readings = [dict(r) for r in many(
            db, "SELECT * FROM baffle_reading WHERE trial_id=? ORDER BY rowid", (t["id"],))]
        for r in readings:
            r["provenance"] = json.loads(r["provenance"])
        accepted, refused = _pair_candidates(db, t)
        signed = one(db, "SELECT * FROM baffle_attestation WHERE trial_id=?", (t["id"],))
        attested = dict(signed) if signed else None
        plan = _plan(db, t)
        if attested:
            verdict = "attributable"
            interval = [attested["effect_lower"], attested["effect_upper"]]
            note = attested["reason"]
        else:
            interval = [t["loose_lower"], t["loose_upper"]]
            note = t["reason"]
            if not plan["available_actions"]:
                # No honor-capable move remains: state the real-feed worlds
                # still compatible, including the one that reverses the claim.
                reversing = [w for w in json.loads(t["worlds"]) if w["baffle_effect"] <= 0]
                verdict = "permanently_inconclusive" if reversing else "inconclusive"
            else:
                verdict = "inconclusive"
        sheet = {
            "trial_id": t["id"],
            "site": t["site"],
            "observed": {"old_rate": t["old_rate"], "new_rate": t["new_rate"],
                         "difference": t["observed_delta"]},
            "permitted_modes": json.loads(t["modes"]),
            "compatible_worlds": json.loads(t["worlds"]),
            "readings": readings,
            "fixtures": [dict(f) for f in many(
                db, "SELECT * FROM baffle_fixture WHERE trial_id=? ORDER BY rowid", (t["id"],))],
            "switches": [dict(s) for s in many(
                db, "SELECT * FROM baffle_switch WHERE trial_id=? ORDER BY rowid", (t["id"],))],
            "origin_links": [dict(l) for l in many(
                db, "SELECT * FROM baffle_origin_link WHERE trial_id=? ORDER BY rowid", (t["id"],))],
            "pair_candidates": {"accepted": accepted, "refused": refused},
            "attestation": attested,
            "verdict": verdict,
            "baffle_effect_interval": interval,
            "attribution_note": note,
            "limits": dict(LIMITS),
            "plan": plan,
        }
        out.append(sheet)
    return out if trial_id is None else out[0]
