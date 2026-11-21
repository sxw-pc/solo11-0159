"""Scoped, point-level evidence packages for the sorting import.

A joint collector holds one original file that carries two independent sorting
points.  A receiver is allowed one sample of exactly one point and must be able
to replay that slice as a genuine ``sort-import`` source, while being unable to
infer anything about the other point from what the tool emits.

Design rules enforced here
--------------------------
* A package is a pure function of the *authorized target slice* and the
  witness credential.  The hidden point is never read into the package, so two
  originals that differ only in the sibling point's content or order produce
  byte-identical packages.
* Only the target point's declaration, batches and samples, plus the boolean
  ``authorized`` relation, are exported.  Ordinary license numbers, sibling row
  counts and every short prefix of a raw file/event hash are never emitted.
* Two independent field paths exist:

  ``source_commitment``
      an independent witness makes a per-point commitment *at reading time*
      and separately reveals the authorization relation.  The receiver can
      re-check the legal relation without seeing the whole original and can
      later accept other independently licensed slices from the same source.

  ``derived_receipt``
      an inspector who still holds the old full archive verifies it offline
      and signs a fresh receipt over a minimal visible set.  The complete
      original must be present; the receipt is tagged as derived and can never
      stand in for a source commitment of a later slice.

* Only the *current* license for exactly this point, category, witness and
  batch can be issued.  A shared original that also licenses the sibling point
  never implies the sibling, and swapping an old associated license back in is
  detected.
* Import is all-or-nothing: packages are verified before any mutation and the
  whole replay runs inside the caller's single transaction.

The cryptography is synthetic: HMAC-SHA256 with module-local keys.  There is
no network, no real signature service and no zero-knowledge protocol.
"""

import hashlib
import hmac
import json
from pathlib import Path

from .store import one


FORMAT = "scoped-sort-source"
VERSION = 1
PURPOSE = "sorting-sample-release"
SCOPED_COMMAND = "sort-scoped-import"

RECEIPT_SOURCE = "source_commitment"
RECEIPT_DERIVED = "derived_receipt"

# Visible rejection reasons.  Each names the missing evidence and never
# mentions the sibling point or a hidden count.
ERR_MALFORMED = "scoped package malformed"
ERR_UNKNOWN_POINT = "scoped target point is not declared"
ERR_OTHER_POINT = "scoped package carries events of another point"
ERR_NO_CREDENTIAL = "no independent witness credential for this exact operation"
ERR_NO_AUTHORIZATION = "no current authorization for this exact point, class, witness and batch"
ERR_STALE = "authorization for this batch is no longer current"
ERR_NO_POINT_COMMITMENT = "whole-file digest is not a point-level source commitment"
ERR_DERIVED_SCOPE = "derived receipt needs the complete original and cannot vouch for a later slice"
ERR_OVERLAP = "overlapping packages may only share an identical licensed part"
ERR_ORIGIN = "scoped package does not descend from the declared source"


# ---------------------------------------------------------------------------
# Canonical encoding and synthetic credentials
# ---------------------------------------------------------------------------
def canonical(value):
    """Deterministic JSON used for every digest input and exported body."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _opaque(kind, *parts):
    """Opaque label derived from public scope data only.

    Deliberately not a hash: the contract forbids emitting any raw hash code,
    so references are plain, deterministic scope readings instead.
    """
    rendered = []
    for part in parts:
        if isinstance(part, dict):
            rendered.append(";".join(f"{key}={part[key]}" for key in sorted(part)))
        else:
            rendered.append(str(part))
    return kind + ":" + "|".join(rendered)


def _scope_key(scope):
    return tuple(sorted((key, scope[key]) for key in
                        ("point_id", "zone", "category", "batch_id",
                         "witness_org", "inspector_org")))


def _key(org):
    # Synthetic shared secret: the exercise forbids a real signing service.
    return hashlib.sha256(
        ("wastehandoff-synthetic-key:" + org).encode()).digest()


def _sign(org, body):
    return hmac.new(_key(org), canonical(body), hashlib.sha256).hexdigest()


def _signature_ok(org, body, proof):
    return hmac.compare_digest(_sign(org, body), str(proof))


def _credential_body(kind, scope, visible_digest):
    return {"purpose": PURPOSE, "kind": kind, "scope": scope,
            "visible_digest": visible_digest}


def make_credential(kind, scope, visible_digest, signer_org):
    if kind not in (RECEIPT_SOURCE, RECEIPT_DERIVED):
        raise ValueError(ERR_NO_CREDENTIAL)
    body = _credential_body(kind, scope, visible_digest)
    return {"kind": kind, "purpose": PURPOSE, "signer_org": signer_org,
            "scope": scope, "visible_digest": visible_digest,
            "proof": _sign(signer_org, body)}


def verify_credential(credential, scope, visible_digest):
    """Return the credential kind after checking signature and binding."""
    if not isinstance(credential, dict):
        raise ValueError(ERR_NO_CREDENTIAL)
    kind = credential.get("kind")
    if kind not in (RECEIPT_SOURCE, RECEIPT_DERIVED):
        raise ValueError(ERR_NO_CREDENTIAL)
    if credential.get("purpose") != PURPOSE:
        raise ValueError(ERR_NO_CREDENTIAL)
    signer = credential.get("signer_org")
    if not signer:
        raise ValueError(ERR_NO_CREDENTIAL)
    if credential.get("scope") != scope:
        # Bound to a different operation, e.g. another batch, class or witness.
        raise ValueError(ERR_NO_CREDENTIAL)
    if credential.get("visible_digest") != visible_digest:
        raise ValueError(ERR_NO_CREDENTIAL)
    body = _credential_body(kind, scope, visible_digest)
    if not _signature_ok(signer, body, credential.get("proof")):
        raise ValueError(ERR_NO_CREDENTIAL)
    return kind


# ---------------------------------------------------------------------------
# Reading a common original document
# ---------------------------------------------------------------------------
def _events_of(document):
    events = document.get("events") if isinstance(document, dict) else document
    if not isinstance(events, list):
        raise ValueError(ERR_MALFORMED)
    return events


def declared_points(events):
    return {event["id"] for event in events if event.get("kind") == "point"}


def point_declaration(events, point_id):
    for event in events:
        if event.get("kind") == "point" and event["id"] == point_id:
            return {"id": event["id"], "zone": event["zone"],
                    "category": event["category"],
                    "inspector_org": event["inspector_org"]}
    raise ValueError(ERR_UNKNOWN_POINT)


def batch_point_map(events):
    return {event["id"]: event.get("point_id") for event in events
            if event.get("kind") == "batch"}


def event_point(event, batch_points):
    kind = event.get("kind")
    if kind == "point":
        return event.get("id")
    if kind in ("batch", "sample", "license"):
        if event.get("point_id"):
            return event["point_id"]
        if kind == "sample":
            return batch_points.get(event.get("batch_id"))
    return None


def canonical_batch(event):
    return {"id": event["id"], "category": event["category"],
            "bag_count": int(event["bag_count"]),
            "declared_kg": float(event["declared_kg"]), "at": event["at"]}


def canonical_sample(event, inspector):
    return {"id": event["id"], "batch_id": event["batch_id"],
            "inspector": inspector, "at": event["at"],
            "contamination": float(event["contamination"]),
            "bag_refs": sorted(event["bag_refs"])}


def target_visible(events, point_id, batch_id):
    """The target-only public slice: deterministic and order independent.

    The exported contract is exactly the target point's batches and samples
    plus the boolean authorization relation.  Nothing from the sibling point
    can enter this body, so there is no count, hash or ordering residue that
    could reveal it.
    """
    batch_points = batch_point_map(events)
    batch_event = None
    for event in events:
        if (event.get("kind") == "batch" and event["id"] == batch_id
                and event.get("point_id") == point_id):
            batch_event = event
            break
    if batch_event is None:
        raise ValueError(ERR_UNKNOWN_POINT)
    samples = []
    for event in events:
        if event.get("kind") != "sample" or event.get("batch_id") != batch_id:
            continue
        if event_point(event, batch_points) != point_id:
            raise ValueError(ERR_OTHER_POINT)
        samples.append(canonical_sample(event, event.get("inspector")))
    if not samples:
        raise ValueError(ERR_MALFORMED)
    samples.sort(key=lambda row: (row["at"], row["id"]))
    return {"batches": [canonical_batch(batch_event)], "samples": samples,
            "authorized": True}


def slice_digest(document, point_id, batch_id):
    """Digest a witness can commit to at reading time for one exact slice.

    Committing over the *visible* body (including the authorization boolean)
    never touches the hidden point, so the commitment is a genuine per-point
    promise rather than an unsalted digest of everything on file.
    """
    return _digest(target_visible(_events_of(document), point_id, batch_id))


# ---------------------------------------------------------------------------
# Point licenses inside the common original
# ---------------------------------------------------------------------------
def _license_rows(events, point_id, category, batch_id):
    return [event for event in events
            if event.get("kind") == "license"
            and event.get("point_id") == point_id
            and event.get("category") == category
            and event.get("batch_id") == batch_id]


def current_source_license(events, point_id, category, batch_id, witness_org):
    """The current license for exactly this point/class/witness/batch.

    Re-issues marked with ``supersedes`` retire the old row.  A shared
    original that also licenses the sibling point never matches, because the
    query is pinned to the target point and exact batch.
    """
    rows = _license_rows(events, point_id, category, batch_id)
    retired = {event["supersedes"] for event in rows if event.get("supersedes")}
    live = [event for event in rows
            if not event.get("revoked") and event["id"] not in retired]
    matches = [event for event in live if event.get("witness_org") == witness_org]
    return matches[-1] if matches else None


def make_scope(point, category, batch_id, witness_org, inspector_org):
    return {"point_id": point["id"], "zone": point["zone"],
            "category": category, "batch_id": batch_id,
            "witness_org": witness_org, "inspector_org": inspector_org}


def source_ref(point_id):
    return _opaque("src", point_id)


def license_ref(scope):
    return _opaque("lic", scope)


# ---------------------------------------------------------------------------
# Building a package
# ---------------------------------------------------------------------------
def _point_commitment(document, point_id, witness_org):
    """Reading-time per-point commitment supplied by the independent witness.

    A ``file_sha256`` computed once at intake is deliberately ignored: it
    cannot be reinterpreted afterwards as a commitment to one point's content.
    """
    if not isinstance(document, dict):
        return None
    commitments = document.get("field_commitments")
    if not isinstance(commitments, dict):
        return None
    raw = commitments.get(point_id)
    if not isinstance(raw, dict) or "slice_digest" not in raw:
        return None
    if raw.get("witness_org") not in (None, witness_org):
        return None
    return raw


def build_package(document, target_point, batch_id, witness_org,
                  credential_kind=RECEIPT_SOURCE, full_archive_present=True):
    """Build the restricted package for one target point and one batch.

    The returned object is exactly what leaves the collector.  The sibling
    point is never inspected, so originals differing only in hidden content
    produce byte-identical packages.
    """
    events = _events_of(document)
    if target_point not in declared_points(events):
        raise ValueError(ERR_UNKNOWN_POINT)
    point = point_declaration(events, target_point)
    visible = target_visible(events, target_point, batch_id)
    category = visible["batches"][0]["category"]
    inspector_org = point["inspector_org"]

    licensed = current_source_license(events, target_point, category,
                                      batch_id, witness_org)
    if licensed is None:
        raise ValueError(ERR_NO_AUTHORIZATION)
    if licensed.get("inspector_org") not in (None, inspector_org):
        raise ValueError(ERR_NO_AUTHORIZATION)

    scope = make_scope(point, category, batch_id, witness_org, inspector_org)
    visible_digest = _digest(visible)

    if credential_kind == RECEIPT_SOURCE:
        commitment = _point_commitment(document, target_point, witness_org)
        if commitment is None:
            raise ValueError(ERR_NO_POINT_COMMITMENT)
        if commitment["slice_digest"] != visible_digest:
            raise ValueError(ERR_NO_POINT_COMMITMENT)
        signer = witness_org
    elif credential_kind == RECEIPT_DERIVED:
        if not full_archive_present:
            raise ValueError(ERR_DERIVED_SCOPE)
        # Only the offline inspector still holding the complete original may
        # sign a derived receipt.
        signer = inspector_org
    else:
        raise ValueError(ERR_NO_CREDENTIAL)

    credential = make_credential(credential_kind, scope, visible_digest, signer)
    return {
        "format": FORMAT,
        "version": VERSION,
        "purpose": PURPOSE,
        "target_point": target_point,
        "batch_id": batch_id,
        "scope": scope,
        "point": point,
        "authorized": True,
        "visible": visible,
        "credential": credential,
        "source_ref": source_ref(target_point),
    }


# ---------------------------------------------------------------------------
# Merging overlapping packages
# ---------------------------------------------------------------------------
def merge_packages(first, second):
    """Merge only the identical licensed common part of two packages.

    The two verification paths may independently serve the same purpose, but
    composing them must never synthesize a wider scope than either credential
    actually signed, nor mix a source commitment with a derived receipt.
    """
    if first.get("format") != FORMAT or second.get("format") != FORMAT:
        raise ValueError(ERR_MALFORMED)
    if first.get("purpose") != second.get("purpose"):
        raise ValueError(ERR_OVERLAP)
    if first["credential"]["kind"] != second["credential"]["kind"]:
        raise ValueError(ERR_OVERLAP)
    if first["scope"] != second["scope"]:
        raise ValueError(ERR_OVERLAP)
    if first["credential"] != second["credential"]:
        raise ValueError(ERR_OVERLAP)
    merged = dict(first)
    merged["merged_from"] = 2
    return merged


# ---------------------------------------------------------------------------
# Verifying and replaying a package
# ---------------------------------------------------------------------------
def _check_structure(package):
    if not isinstance(package, dict):
        raise ValueError(ERR_MALFORMED)
    if package.get("format") != FORMAT or package.get("version") != VERSION:
        raise ValueError(ERR_MALFORMED)
    if package.get("purpose") != PURPOSE:
        raise ValueError(ERR_MALFORMED)
    visible = package.get("visible")
    if not isinstance(visible, dict):
        raise ValueError(ERR_MALFORMED)
    batches, samples = visible.get("batches"), visible.get("samples")
    if not isinstance(batches, list) or not isinstance(samples, list):
        raise ValueError(ERR_MALFORMED)
    if len(batches) != 1:
        raise ValueError(ERR_MALFORMED)
    batch = batches[0]
    if batch.get("id") != package.get("batch_id"):
        raise ValueError(ERR_MALFORMED)
    point = package.get("point")
    scope = package.get("scope")
    if not isinstance(point, dict) or not isinstance(scope, dict):
        raise ValueError(ERR_MALFORMED)
    for key in ("point_id", "zone", "category", "batch_id", "witness_org",
                "inspector_org"):
        if not scope.get(key):
            raise ValueError(ERR_NO_CREDENTIAL)
    if scope["point_id"] != package["target_point"]:
        raise ValueError(ERR_OTHER_POINT)
    if scope["batch_id"] != package["batch_id"]:
        raise ValueError(ERR_MALFORMED)
    if scope["category"] != batch.get("category"):
        raise ValueError(ERR_NO_AUTHORIZATION)
    if (point.get("id") != scope["point_id"]
            or point.get("category") != scope["category"]
            or point.get("inspector_org") != scope["inspector_org"]
            or point.get("zone") != scope["zone"]):
        raise ValueError(ERR_ORIGIN)
    for sample in samples:
        if sample.get("batch_id") != package["batch_id"]:
            raise ValueError(ERR_OTHER_POINT)
        if sample.get("inspector") != scope["inspector_org"]:
            raise ValueError(ERR_NO_AUTHORIZATION)
    if not samples:
        raise ValueError(ERR_MALFORMED)


def _signed_kind(package):
    _check_structure(package)
    scope = package["scope"]
    if package.get("authorized") is not True:
        raise ValueError(ERR_NO_AUTHORIZATION)
    if package.get("source_ref") != source_ref(scope["point_id"]):
        raise ValueError(ERR_ORIGIN)
    visible_digest = _digest(package["visible"])
    credential = package.get("credential")
    kind = verify_credential(credential, scope, visible_digest)
    if kind == RECEIPT_SOURCE:
        if credential["signer_org"] != scope["witness_org"]:
            raise ValueError(ERR_NO_CREDENTIAL)
    else:
        # Derived receipts may only be signed by the inspecting organisation
        # that still held the complete original.
        if credential["signer_org"] != scope["inspector_org"]:
            raise ValueError(ERR_DERIVED_SCOPE)
    return kind


def _license_state(db, scope):
    """Classify the receiver ledger's license history for one exact operation.

    ``bootstrap`` - the ledger has never seen a license for this batch, so a
                    correctly signed credential may establish it.
    ``current``   - a live license matches this exact point/class/witness.
    ``stale``     - a history exists but the exact association was superseded
                    or revoked; an old package must not enter the ledger.
    """
    rows = db.execute(
        "SELECT * FROM license WHERE point_id=? AND batch_id=? ORDER BY at, id",
        (scope["point_id"], scope["batch_id"])).fetchall()
    if not rows:
        return "bootstrap"
    for row in rows:
        if (not row["revoked"] and row["witness_org"] == scope["witness_org"]
                and row["category"] == scope["category"]
                and row["inspector_org"] == scope["inspector_org"]):
            return "current"
    return "stale"


def verify_package(db, package):
    """Verify a package before any ledger mutation.

    Returns ``(events, scope)``.  ``events`` are the canonical target-only
    rows that enter the existing sorting computation.
    """
    kind = _signed_kind(package)
    scope = package["scope"]
    existing = one(db, "SELECT * FROM point WHERE id=?", (scope["point_id"],))
    if existing:
        if (existing["category"] != scope["category"]
                or existing["inspector_org"] != scope["inspector_org"]):
            raise ValueError(ERR_NO_AUTHORIZATION)
    if _license_state(db, scope) == "stale":
        raise ValueError(ERR_STALE)
    return _reconstruct_events(package, kind), scope


def _reconstruct_events(package, kind):
    """Materialise the canonical target-only event rows.

    No sibling row is present in the package, so replay order is a function of
    the visible slice alone.
    """
    scope = package["scope"]
    point = package["point"]
    events = [{"id": point["id"], "kind": "point", "zone": point["zone"],
               "category": point["category"],
               "inspector_org": point["inspector_org"]}]
    events.append({
        "id": license_ref(scope), "kind": "license",
        "point_id": scope["point_id"], "category": scope["category"],
        "inspector_org": scope["inspector_org"],
        "witness_org": scope["witness_org"], "batch_id": scope["batch_id"],
        "at": package["credential"]["kind"] + ":" + source_ref(scope["point_id"]),
        "origin": "scoped",
    })
    for batch in package["visible"]["batches"]:
        events.append({"id": batch["id"], "kind": "batch",
                       "point_id": scope["point_id"],
                       "category": batch["category"],
                       "bag_count": int(batch["bag_count"]),
                       "declared_kg": float(batch["declared_kg"]),
                       "at": batch["at"]})
    for sample in package["visible"]["samples"]:
        events.append({"id": sample["id"], "kind": "sample",
                       "batch_id": sample["batch_id"],
                       "inspector": sample["inspector"], "at": sample["at"],
                       "contamination": float(sample["contamination"]),
                       "bag_refs": list(sample["bag_refs"])})
    return events


def replay_events(package):
    """Independently verify a package and return its canonical events.

    Used by ``audit`` from the stored raw bytes: no receiver ledger state is
    required, so a replay cannot silently depend on the current database.
    """
    kind = _signed_kind(package)
    return _reconstruct_events(package, kind)


def explain_rejection(db, package):
    """Why a package that looks fully populated still cannot enter the ledger.

    Returns ``{"admitted": True, "reason": None}`` when it would be accepted,
    otherwise the visible missing-evidence reason.  Performs no mutation.
    """
    try:
        verify_package(db, package)
    except ValueError as exc:
        return {"admitted": False, "reason": str(exc)}
    return {"admitted": True, "reason": None}


def read_package(path):
    raw = Path(path).read_bytes()
    try:
        package = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(ERR_MALFORMED)
    if not isinstance(package, dict) or package.get("format") != FORMAT:
        raise ValueError(ERR_MALFORMED)
    return package, raw


def is_package_document(data):
    return isinstance(data, dict) and data.get("format") == FORMAT


def package_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def describe(package):
    """Receiver-visible description of the exact authorized operation.

    Contains the witness and exact scope but no license number, no sibling
    count and no raw hash.
    """
    return {"target_point": package["target_point"],
            "batch_id": package["batch_id"],
            "category": package["scope"]["category"],
            "witness_org": package["scope"]["witness_org"],
            "credential": package["credential"]["kind"],
            "authorized": package["authorized"]}
