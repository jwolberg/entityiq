"""Screening read + human-disposition API (IS2-T6, ticket 0041)."""

from __future__ import annotations

from app.models.audit_event import AuditEvent
from app.screening.models import ScreeningDisposition
from tests.screening.conftest import load_people

MATCH_SUBJECT = {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "RO"}


def _submit(env, payload, who="operator"):
    r = env["client"].post("/screenings", json=payload, headers=env["auth"][who])
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _seed_three(env):
    load_people(
        env["factory"],
        [
            {
                "name": "Teodor Vasilescu",
                "dobs": [{"date": "1962-08-30"}],
                "nat": ["RO"],
            },
            {"name": "Helena Lindqvistad"},
        ],
    )
    match = _submit(env, MATCH_SUBJECT)
    review = _submit(env, {"name": "Helena Lindqvistad"})
    clear = _submit(env, {"name": "Chidi Okafor"})
    return match, review, clear


def test_queue_is_sorted_by_severity_and_filterable(api_env):
    match, review, clear = _seed_three(api_env)
    c, h = api_env["client"], api_env["auth"]["operator"]

    items = c.get("/screenings", headers=h).json()["items"]
    assert [i["run_id"] for i in items] == [match, review, clear]
    assert [i["system_disposition"] for i in items] == ["MATCH", "REVIEW", "CLEAR"]
    assert items[0]["subject_name"] == "Teodor Vasilescu"

    only = c.get("/screenings?disposition=REVIEW", headers=h).json()["items"]
    assert [i["run_id"] for i in only] == [review]
    by_list = c.get("/screenings?list_source=ofac_sdn", headers=h).json()["items"]
    assert {i["run_id"] for i in by_list} == {match, review}
    assert c.get("/screenings?older_than_days=1", headers=h).json()["items"] == []


def test_detail_has_terms_with_evidence_snapshots_rule_and_timing(api_env):
    match, _r, _c = _seed_three(api_env)
    d = (
        api_env["client"]
        .get(f"/screenings/{match}", headers=api_env["auth"]["operator"])
        .json()
    )

    assert d["subject"]["name"] == "Teodor Vasilescu"
    assert d["decision"]["system_disposition"] == "MATCH"
    assert d["decision"]["rule_version"] == 1
    assert d["decision"]["thresholds"] == {"clear_below": 0.35, "match_at": 0.9}
    assert d["decision"]["snapshot_ids"]
    assert d["decision"]["normalizer_version"] == "n2"
    assert d["run"]["started_at"] and d["run"]["duration_seconds"] is not None
    cand = d["candidates"][0]
    assert cand["record"]["source"] == "ofac_sdn"
    assert cand["band"] == "MATCH"
    for term in cand["terms"]:
        assert term["claim_ids"], term["name"]
        assert all(cid in cand["claims"] for cid in term["claim_ids"])
    claim = next(iter(cand["claims"].values()))
    assert claim["source"] and claim["locator"]


def test_reading_detail_is_audited(api_env):
    match, _r, _c = _seed_three(api_env)
    api_env["client"].get(f"/screenings/{match}", headers=api_env["auth"]["lead"])
    db = api_env["factory"]()
    events = db.query(AuditEvent).filter_by(event_type="screening.viewed").all()
    assert len(events) == 1 and events[0].payload["run_id"] == match
    assert "Vasilescu" not in str(events[0].payload)
    db.close()


def test_disposition_appends_rows_and_keeps_history(api_env):
    _m, review, _c = _seed_three(api_env)
    c, h = api_env["client"], api_env["auth"]["operator"]

    r1 = c.post(
        f"/screenings/{review}/disposition",
        json={"disposition": "CLEAR", "notes": "different DOB on ID"},
        headers=h,
    )
    r2 = c.post(
        f"/screenings/{review}/disposition",
        json={"disposition": "MATCH", "notes": "lead override"},
        headers=api_env["auth"]["lead"],
    )
    assert r1.status_code == r2.status_code == 201

    d = c.get(f"/screenings/{review}", headers=h).json()
    assert [x["disposition"] for x in d["dispositions"]] == ["CLEAR", "MATCH"]
    assert d["dispositions"][0]["notes"] == "different DOB on ID"
    db = api_env["factory"]()
    assert db.query(ScreeningDisposition).count() == 2
    assert (
        db.query(AuditEvent).filter_by(event_type="screening.dispositioned").count()
        == 2
    )
    db.close()


def test_auto_cleared_runs_can_be_qa_dispositioned(api_env):
    _m, _r, clear = _seed_three(api_env)
    r = api_env["client"].post(
        f"/screenings/{clear}/disposition",
        json={"disposition": "CLEAR", "notes": "QA sample ok"},
        headers=api_env["auth"]["lead"],
    )
    assert r.status_code == 201


def test_review_is_not_a_human_disposition(api_env):
    _m, review, _c = _seed_three(api_env)
    r = api_env["client"].post(
        f"/screenings/{review}/disposition",
        json={"disposition": "REVIEW"},
        headers=api_env["auth"]["operator"],
    )
    assert r.status_code == 422


def test_unknown_run_is_404_and_integration_keys_cannot_read_the_queue(api_env):
    c = api_env["client"]
    assert (
        c.get("/screenings/nope", headers=api_env["auth"]["operator"]).status_code
        == 404
    )
    assert c.get("/screenings", headers=api_env["api_key"]).status_code == 401
