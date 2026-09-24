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


def _count_queries(env):
    from sqlalchemy import event

    engine = env["factory"].kw["bind"]
    seen: list[str] = []

    def on_exec(_conn, _cur, statement, *_a):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", on_exec)
    return seen, lambda: event.remove(engine, "before_cursor_execute", on_exec)


def test_queue_is_paginated_in_severity_order(api_env):
    match, review, clear = _seed_three(api_env)
    c, h = api_env["client"], api_env["auth"]["operator"]

    page1 = c.get("/screenings?limit=2", headers=h).json()
    assert page1["total"] == 3 and page1["limit"] == 2 and page1["offset"] == 0
    assert [i["run_id"] for i in page1["items"]] == [match, review]
    page2 = c.get("/screenings?limit=2&offset=2", headers=h).json()
    assert [i["run_id"] for i in page2["items"]] == [clear]
    filtered = c.get("/screenings?disposition=CLEAR&limit=1", headers=h).json()
    assert filtered["total"] == 1
    assert c.get("/screenings?limit=0", headers=h).status_code == 422
    assert c.get("/screenings?limit=501", headers=h).status_code == 422


def test_queue_query_count_does_not_grow_with_rows(api_env):
    """No per-row queries in the queue."""
    c, h = api_env["client"], api_env["auth"]["operator"]
    load_people(api_env["factory"], [{"name": "Teodor Vasilescu"}])

    def measure(n_runs):
        for _ in range(n_runs):
            _submit(api_env, {"name": "Teodor Vasilescu"})
        seen, stop = _count_queries(api_env)
        try:
            c.get("/screenings?list_source=ofac_sdn", headers=h)
            return len(seen)
        finally:
            stop()

    small, big = measure(1), measure(4)
    assert big == small, (small, big)


def test_detail_query_count_does_not_grow_with_candidates(api_env):
    c, h = api_env["client"], api_env["auth"]["operator"]

    def measure(people, source):
        load_people(api_env["factory"], people, source=source)
        run_id = _submit(api_env, {"name": "Teodor Vasilescu"})
        seen, stop = _count_queries(api_env)
        try:
            d = c.get(f"/screenings/{run_id}", headers=h).json()
            return len(d["candidates"]), len(seen)
        finally:
            stop()

    few, q_few = measure([{"name": "Teodor Vasilescu"}], "ofac_sdn")
    # A second list adds candidates without replacing the first snapshot.
    many, q_many = measure(
        [{"name": f"Teodor Vasilescu{s}"} for s in ("u", "o", "a", "e")],
        "un_consolidated",
    )
    assert many > few
    assert q_many == q_few, (q_few, q_many)
