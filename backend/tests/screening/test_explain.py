"""Decision explanation endpoint (ticket 0067; plan PLAN-IS-EVIDENCE §[3]).

GET /screenings/{run_id}/explanation walks through every decision a run made
and cites the list entry and snapshot behind each term. It is derived from
stored rows only: it never decrypts the subject, so it works after a
crypto-shred and never carries subject values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.lists.persons import PersonRecord, store_records
from app.models.audit_event import AuditEvent
from app.models.list_snapshot import ListSnapshot
from app.screening.eval import load_corpus
from app.screening.metrics import CORPUS_PATH
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject
from tests.screening.conftest import load_people
from tests.screening.test_read_api import _count_queries, _submit

STEP_KINDS = ["sources", "blocking", "scoring", "banding", "disposition", "human"]


def _explain(env, run_id, who="operator"):
    headers = env["auth"][who] if who else env["api_key"]
    return env["client"].get(f"/screenings/{run_id}/explanation", headers=headers)


def _steps(body):
    return {s["kind"]: s for s in body["steps"]}


def _load_corpus_watchlist(factory):
    corpus = load_corpus(CORPUS_PATH)
    db = factory()
    snap = ListSnapshot(
        source="ofac_sdn",
        retrieved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        content_sha256="corpus-v2",
        record_count=len(corpus.watchlist),
    )
    db.add(snap)
    db.flush()
    store_records(
        db,
        snap,
        [
            PersonRecord(
                source="ofac_sdn",
                source_entry_id=r["id"],
                primary_name=r["primary_name"],
                names=r["names"],
                dobs=r["dobs"],
                nationalities=r["nationalities"],
                documents=r["documents"],
            )
            for r in corpus.watchlist
        ],
    )
    db.commit()
    db.close()
    return corpus


# ---------------------------------------------------------------------------
# The explanation agrees with the stored decision
# ---------------------------------------------------------------------------


def test_explanation_agrees_with_the_stored_decision_for_every_corpus_case(api_env):
    corpus = _load_corpus_watchlist(api_env["factory"])
    seen = set()
    for case in corpus.cases:
        payload = {k: v for k, v in case.subject.items() if v not in (None, [])}
        run_id = _submit(api_env, payload)
        r = _explain(api_env, run_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [s["kind"] for s in body["steps"]] == STEP_KINDS
        steps = _steps(body)

        db = api_env["factory"]()
        decision = db.query(ScreeningDecision).filter_by(run_id=run_id).one()
        stored = {c["candidate_id"]: c for c in decision.terms}
        db.close()

        banding = steps["banding"]["candidates"]
        assert {c["candidate_id"] for c in banding} == set(stored)
        for c in banding:
            assert c["band"] == stored[c["candidate_id"]]["band"], case.id
            assert c["score"] == stored[c["candidate_id"]]["score"]
            assert c["consistent"] is True
            assert c["reason_code"] and c["reason_text"]
        disp = steps["disposition"]
        assert disp["system_disposition"] == decision.system_disposition, case.id
        assert disp["auto_closed"] == decision.auto_closed
        assert disp["consistent"] is True
        seen.add((decision.system_disposition, disp["reason_code"]))
    # The corpus exercises every disposition path except a coverage gap.
    assert {d for d, _ in seen} == {"CLEAR", "REVIEW", "MATCH"}
    assert ("MATCH", "rollup_most_severe") in seen
    assert any(r in ("no_candidates", "auto_clear_allowed") for _d, r in seen)


def test_every_term_cites_both_sides_with_snapshot_metadata(api_env):
    load_people(
        api_env["factory"],
        [{"name": "Teodor Vasilescu", "dobs": [{"date": "1962-08-30"}], "nat": ["RO"]}],
    )
    run_id = _submit(
        api_env, {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "RO"}
    )
    body = _explain(api_env, run_id).json()
    [cand] = _steps(body)["scoring"]["candidates"]
    assert cand["record_ref"]["source"] == "ofac_sdn"
    assert cand["record_ref"]["primary_name"] == "Teodor Vasilescu"
    names = {t["name"] for t in cand["terms"]}
    assert {"name_exact_normalized", "dob_full_match", "nationality_match"} <= names
    for term in cand["terms"]:
        assert term["label"] and term["direction"] in ("+", "-", "0")
        about = {body["citations"][cid]["about"] for cid in term["citations"]}
        assert about == {"subject", "record"}, term["name"]

    record_cite = next(c for c in body["citations"].values() if c["about"] == "record")
    # Snapshot time comes from the snapshot row (conftest: 2026-09-01), not
    # the claim's run-time timestamp.
    assert record_cite["snapshot_retrieved_at"].startswith("2026-09-01")
    assert record_cite["content_sha256"] == "ofac_sdn-fixture"
    assert record_cite["display_name"] == "OFAC SDN List"
    assert record_cite["source_url"].startswith("https://")
    assert record_cite["entry_id"] == "0"
    assert record_cite["field"] in ("names", "dobs", "nationalities")

    sources = _steps(body)["sources"]["lists"]
    [ofac] = [s for s in sources if s["source"] == "ofac_sdn"]
    assert ofac["status"] == "complete" and ofac["record_count"] == 1
    assert ofac["retrieved_at"].startswith("2026-09-01")


def test_banding_step_explains_the_threshold_in_words(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"})
    steps = _steps(_explain(api_env, run_id).json())
    assert steps["banding"]["thresholds"] == {"clear_below": 0.35, "match_at": 0.9}
    [c] = steps["banding"]["candidates"]
    assert c["band"] == "REVIEW" and c["reason_code"] == "between_thresholds"
    assert "0.35" in c["reason_text"] and "0.9" in c["reason_text"]
    assert steps["disposition"]["reason_code"] == "rollup_most_severe"


# ---------------------------------------------------------------------------
# No subject values, ever
# ---------------------------------------------------------------------------


def test_explanation_never_contains_subject_values(api_env):
    load_people(
        api_env["factory"],
        [{"name": "Teodor Vasilescu", "dobs": [{"year": 1962}], "nat": ["RO"]}],
    )
    subject = {
        "name": "Vasilescu Teodor",
        "dob": "1962-08-30",
        "nationality": "RO",
        "pob": "Constanta Harbour District",
        "address": "14 Strada Fictiva, Brasov",
        "documents": [{"type": "passport", "number": "QZ4471902", "country": "RO"}],
    }
    run_id = _submit(api_env, subject)
    r = _explain(api_env, run_id)
    assert r.status_code == 200, r.text
    text = r.text
    for value in (
        "Vasilescu Teodor",
        "1962-08-30",
        "QZ4471902",
        "Constanta Harbour",
        "Strada Fictiva",
    ):
        assert value not in text, value


def test_explanation_works_after_the_subject_is_crypto_shredded(api_env):
    from app.screening.crypto import shred_subject, utcnow

    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"})
    db = api_env["factory"]()
    run = db.get(ScreeningRun, run_id)
    shred_subject(db, db.get(ScreeningSubject, run.subject_id), utcnow())
    db.commit()
    db.close()

    r = _explain(api_env, run_id)
    assert r.status_code == 200, r.text
    steps = _steps(r.json())
    assert r.json()["subject_shredded"] is True
    assert steps["banding"]["candidates"][0]["band"] == "REVIEW"
    assert steps["disposition"]["system_disposition"] == "REVIEW"


# ---------------------------------------------------------------------------
# Disposition-step paths
# ---------------------------------------------------------------------------


def test_coverage_gap_explains_why_auto_clear_was_blocked(api_env, monkeypatch):
    monkeypatch.setenv("ENTITYIQ_SCREENING_REQUIRED_SOURCES", "ofac_sdn,uk_ofsi")
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Chidi Okafor"})
    body = _explain(api_env, run_id).json()
    steps = _steps(body)
    assert steps["disposition"]["system_disposition"] == "REVIEW"
    assert steps["disposition"]["reason_code"] == "auto_clear_blocked_by_coverage"
    assert steps["disposition"]["coverage_gaps"] == ["list:uk_ofsi"]
    [uk] = [s for s in steps["sources"]["lists"] if s["source"] == "uk_ofsi"]
    assert uk["status"] == "unavailable" and uk["snapshot_id"] is None


def test_clean_subject_explains_the_auto_clear(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Chidi Okafor"})
    steps = _steps(_explain(api_env, run_id).json())
    assert steps["blocking"]["candidate_count"] == 0
    assert steps["disposition"]["reason_code"] == "no_candidates"
    assert steps["disposition"]["auto_closed"] is True
    assert steps["disposition"]["coverage_gaps"] == []


def test_common_name_step_reports_frequency_and_threshold(api_env):
    load_people(api_env["factory"], [{"name": "Mohammed Ali"} for _ in range(12)])
    run_id = _submit(api_env, {"name": "Mohammed Ali"})
    common = _steps(_explain(api_env, run_id).json())["disposition"]["common_name"]
    assert common == {"frequency": 12, "threshold": 10, "applied": True}


def test_common_name_is_null_when_not_applied(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"})
    assert (
        _steps(_explain(api_env, run_id).json())["disposition"]["common_name"] is None
    )


# ---------------------------------------------------------------------------
# Human step, access and audit
# ---------------------------------------------------------------------------


def test_human_step_lists_dispositions_and_replays(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"}, who=None)
    c = api_env["client"]
    c.post(
        f"/screenings/{run_id}/disposition",
        json={"disposition": "CLEAR", "notes": "different person, checked ID"},
        headers=api_env["auth"]["operator"],
    )
    c.post(f"/screenings/{run_id}/replay", headers=api_env["auth"]["lead"])

    human = _steps(_explain(api_env, run_id).json())["human"]
    assert [d["disposition"] for d in human["dispositions"]] == ["CLEAR"]
    assert human["dispositions"][0]["notes"] == "different person, checked ID"
    assert [r["reproduced"] for r in human["replays"]] == [True]

    # An integration key sees the outcome but not analyst notes or identities.
    r = _explain(api_env, run_id, who=None)
    assert r.status_code == 200
    assert "different person" not in r.text
    assert "operator_id" not in r.json()["steps"][-1]["dispositions"][0]


def test_access_is_scoped_like_the_detail_endpoint(api_env):
    from app.auth import service as service_auth

    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    mine = _submit(api_env, {"name": "Helena Lindqvistad"}, who=None)
    db = api_env["factory"]()
    _row, other_key = service_auth.create_api_client("someone-else", db)
    db.commit()
    db.close()

    assert _explain(api_env, mine, who=None).status_code == 200
    other = api_env["client"].get(
        f"/screenings/{mine}/explanation", headers={"X-API-Key": other_key}
    )
    assert other.status_code == 404
    assert _explain(api_env, mine, who="examiner").status_code == 200
    assert _explain(api_env, "nope").status_code == 404
    assert api_env["client"].get(f"/screenings/{mine}/explanation").status_code == 401


def test_reading_an_explanation_is_audited_without_pii(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"})
    _explain(api_env, run_id, who="examiner")
    db = api_env["factory"]()
    [event] = db.query(AuditEvent).filter_by(event_type="screening.explanation_viewed")
    assert event.payload == {"run_id": run_id}
    assert event.operator_id is not None
    db.close()


def test_run_without_a_decision_is_409(api_env):
    db = api_env["factory"]()
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    run = ScreeningRun(subject_id=subject.id, status="running")
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()
    r = _explain(api_env, run_id)
    assert r.status_code == 409
    assert "no decision" in r.json()["detail"].lower()


def test_query_count_does_not_grow_with_candidates(api_env):
    def measure(people, source):
        load_people(api_env["factory"], people, source=source)
        run_id = _submit(api_env, {"name": "Teodor Vasilescu"})
        seen, stop = _count_queries(api_env)
        try:
            body = _explain(api_env, run_id).json()
            return len(_steps(body)["scoring"]["candidates"]), len(seen)
        finally:
            stop()

    few, q_few = measure([{"name": "Teodor Vasilescu"}], "ofac_sdn")
    many, q_many = measure(
        [{"name": f"Teodor Vasilescu{s}"} for s in ("u", "o", "a", "e")],
        "un_consolidated",
    )
    assert many > few
    assert q_many == q_few, (q_few, q_many)


def test_explanation_payload_is_json_stable(api_env):
    load_people(api_env["factory"], [{"name": "Helena Lindqvistad"}])
    run_id = _submit(api_env, {"name": "Helena Lindqvistad"})
    ra, rb = _explain(api_env, run_id), _explain(api_env, run_id)
    assert ra.status_code == rb.status_code == 200
    a, b = ra.json(), rb.json()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_stored_band_the_current_rules_disagree_with_is_flagged():
    """Code drift: explain with today's rules, but say when they disagree."""
    from types import SimpleNamespace as NS

    from app.screening.explain import build_explanation

    decision = NS(
        id="d1",
        created_at=None,
        normalizer_version="n0",
        thresholds={"clear_below": 0.35, "match_at": 0.9},
        snapshot_ids=[],
        system_disposition="MATCH",
        auto_closed=False,
        # Stored as MATCH, but 0.5 is REVIEW under today's rules.
        terms=[
            {
                "candidate_id": "c1",
                "score": 0.5,
                "band": "MATCH",
                "terms": [{"name": "name_exact_normalized", "weight": 0.5}],
            }
        ],
    )
    kwargs = dict(
        run=NS(id="r1", source_availability={}),
        subject=NS(shredded_at=None),
        decision=decision,
        rule_config={"common_name_threshold": 10},
        rule_version=1,
        candidates={"c1": NS(id="c1", watchlist_record_id="w1", blocking_keys=[])},
        records={
            "w1": NS(
                source="ofac_sdn",
                source_entry_id="1",
                snapshot_id="s1",
                primary_name="A B",
                program=None,
            )
        },
        term_rows=[],
        claims={},
        snapshots={},
        dispositions=[],
        replays=[],
        show_operator_detail=True,
        required_sources=["ofac_sdn"],
        max_list_age_days=7,
    )
    body = build_explanation(**kwargs)
    steps = _steps(body)
    [band] = steps["banding"]["candidates"]
    assert band["band"] == "MATCH" and band["consistent"] is False
    # The run rule still rolls the stored bands up to the stored MATCH.
    assert steps["disposition"]["consistent"] is True

    # Run-level drift: a stored auto-CLEAR despite a coverage gap.
    decision.terms = []
    decision.system_disposition, decision.auto_closed = "CLEAR", True
    body = build_explanation(
        **{
            **kwargs,
            "decision": decision,
            "run": NS(id="r1", source_availability={"list:ofac_sdn": "unavailable"}),
        }
    )
    disposition = _steps(body)["disposition"]
    assert disposition["reason_code"] == "auto_clear_blocked_by_coverage"
    assert disposition["consistent"] is False
