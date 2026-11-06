from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    DeploymentRevision,
    IncidentScenario,
    Namespace,
    PlatformEvent,
    Pod,
    ScenarioPodState,
    SupportTicket,
    Tenant,
    Workload,
)
from app.seed import (
    get_or_create_incident_scenario,
    get_or_create_platform_event,
    get_or_create_scenario_pod_state,
    seed_database,
)
from app.services.investigation import gather_evidence
from app.services.investigation_analysis import analyze, build_report

TICKETS_URL = "/api/platform/tickets"
SCENARIO_KEY = "acme-customer-portal-503"
FAILING = ["customer-portal-7d9f6c-def34", "customer-portal-7d9f6c-ghi56"]


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'incident.db'}",
        connect_args={"check_same_thread": False},
    )
    assert engine.dialect.name == "sqlite"
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        seed_database(db)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield session_factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture()
def client(factory):
    # Not a context manager: the app lifespan must never run in tests.
    return TestClient(app)


def acme_ticket(db):
    return db.query(SupportTicket).filter_by(ticket_key="TCK-1001").one()


def other_ticket(db, slug, workload_name, key):
    tenant = db.query(Tenant).filter_by(slug=slug).one()
    workload = db.query(Workload).filter_by(name=workload_name).one()
    ticket = SupportTicket(
        ticket_key=key,
        tenant_id=tenant.id,
        workload_id=workload.id,
        title="Investigate",
        description="Please investigate.",
    )
    db.add(ticket)
    db.commit()
    return ticket


def all_refs(evidence):
    refs = {evidence.workload.ref}
    if evidence.scenario:
        refs.add(evidence.scenario.ref)
    refs |= {p.ref for p in evidence.pods.pods}
    refs |= {s.ref for s in evidence.services.services}
    refs |= {i.ref for i in evidence.ingresses.ingresses}
    refs |= {e.ref for e in evidence.events.events}
    refs |= {r.ref for r in evidence.deployment_history.revisions}
    return refs


# ---------- seeding ----------


def test_scenario_seeded(db):
    scenario = db.query(IncidentScenario).filter_by(key=SCENARIO_KEY).one()
    assert scenario.status == "active"
    assert scenario.ticket.ticket_key == "TCK-1001"
    assert scenario.workload.name == "customer-portal"
    assert db.query(ScenarioPodState).count() == 2
    assert db.query(DeploymentRevision).count() == 3
    assert db.query(PlatformEvent).count() == 7


def test_scenario_seed_is_idempotent_and_keeps_timestamps(factory):
    def snapshot(session):
        return (
            [(r.revision, r.deployed_at, r.status)
             for r in session.query(DeploymentRevision).order_by(DeploymentRevision.id)],
            [(e.event_key, e.occurred_at)
             for e in session.query(PlatformEvent).order_by(PlatformEvent.id)],
            [(s.pod_id, s.restart_count)
             for s in session.query(ScenarioPodState).order_by(ScenarioPodState.id)],
        )

    with factory() as db:
        before = snapshot(db)
        seed_database(db)
        seed_database(db)
        assert snapshot(db) == before
        assert db.query(IncidentScenario).count() == 1
        assert db.query(ScenarioPodState).count() == 2
        assert db.query(DeploymentRevision).count() == 3
        assert db.query(PlatformEvent).count() == 7


def test_baseline_pods_remain_healthy_and_unchanged(db):
    pods = db.query(Pod).all()
    assert len(pods) == 8
    assert all(p.status == "Running" for p in pods)
    assert all(p.ready_containers == p.desired_containers == 1 for p in pods)
    assert all(p.restart_count == 0 for p in pods)
    assert all(w.status == "healthy" and w.ready_replicas == w.desired_replicas
               for w in db.query(Workload))


def test_additive_schema_leaves_existing_columns_alone():
    assert {c.name for c in Pod.__table__.columns} == {
        "id", "name", "namespace_id", "cluster_id", "node_id", "status",
        "desired_containers", "ready_containers", "restart_count", "pod_ip",
        "created_at",
    }
    assert {c.name for c in Workload.__table__.columns} == {
        "id", "name", "namespace", "tenant_id", "cluster_id",
        "desired_replicas", "ready_replicas", "status",
    }


def test_seed_helpers_validate_consistency(db):
    ticket = acme_ticket(db)
    globex_workload = db.query(Workload).filter_by(name="order-management").one()
    with pytest.raises(ValueError):
        get_or_create_incident_scenario(
            db, "bad", ticket, globex_workload, name="n", description="d"
        )

    scenario = db.query(IncidentScenario).one()
    globex_pod = db.query(Pod).filter_by(name="order-management-6b8d5f-jkl78").one()
    with pytest.raises(ValueError):
        get_or_create_scenario_pod_state(
            db, scenario, globex_pod, status="Running", ready_containers=0,
            restart_count=1,
        )

    acme_ns = db.query(Namespace).filter_by(name="acme-prod").one()
    workload = db.query(Workload).filter_by(name="customer-portal").one()
    with pytest.raises(ValueError):
        get_or_create_platform_event(
            db, "bad-event", acme_ns, workload, pod=globex_pod, reason="X",
            message="m", occurred_at=datetime.now(timezone.utc),
        )


# ---------- evidence ----------


def test_investigation_sees_scenario_overrides(db):
    evidence = gather_evidence(db, acme_ticket(db))

    assert evidence.scenario.key == SCENARIO_KEY
    assert evidence.scenario.simulated is True

    by_name = {p.name: p for p in evidence.pods.pods}
    healthy = by_name["customer-portal-7d9f6c-abc12"]
    assert healthy.ready and healthy.source == "baseline"
    assert healthy.restart_count == 0

    for name, restarts in zip(FAILING, (3, 5)):
        pod = by_name[name]
        assert pod.ready is False
        assert pod.source == "scenario_override"
        assert pod.health_reason == "ReadinessProbeFailed"
        assert pod.ready_containers == 0 and pod.desired_containers == 1
        assert pod.restart_count == restarts
        assert pod.baseline_ready is True
        assert pod.baseline_restart_count == 0

    assert evidence.pods.ready_count == 1
    assert evidence.pods.baseline_ready_count == 3
    assert evidence.pods.expected_replicas == 3
    # the recorded workload row still shows the healthy baseline
    assert evidence.workload.ready_replicas == 3


def test_events_are_chronological_and_show_failure_sequence(db):
    events = gather_evidence(db, acme_ticket(db)).events
    assert events.status == "observed"
    times = [e.occurred_at for e in events.events]
    assert times == sorted(times)
    assert all(t.tzinfo is not None for t in times)

    revision42 = next(
        r for r in gather_evidence(db, acme_ticket(db)).deployment_history.revisions
        if r.revision == 42
    )
    assert times[0] == revision42.deployed_at
    assert events.events[0].reason == "ScalingReplicaSet"

    readiness = [e for e in events.events if e.reason == "Unhealthy"]
    assert [e.involved_object for e in readiness] == FAILING
    assert all(e.event_type == "Warning" for e in readiness)
    assert all("Readiness probe failed" in e.message for e in readiness)
    assert all(e.occurred_at > revision42.deployed_at for e in readiness)
    assert all(e.scenario_key == SCENARIO_KEY for e in events.events)


def test_deployment_history_and_last_known_healthy(db):
    history = gather_evidence(db, acme_ticket(db)).deployment_history
    assert history.status == "observed"
    assert [r.revision for r in history.revisions] == [40, 41, 42]
    times = [r.deployed_at for r in history.revisions]
    assert times == sorted(times)
    assert history.current_revision == 42
    assert history.last_known_healthy_revision == 41
    assert [r.status for r in history.revisions] == ["healthy", "healthy", "failed"]
    assert [r.is_current for r in history.revisions] == [False, False, True]
    assert history.revisions[2].scenario_key == SCENARIO_KEY
    assert history.revisions[1].scenario_key is None
    recent = datetime.now(timezone.utc) - history.revisions[2].deployed_at
    assert timedelta(minutes=30) < recent < timedelta(hours=2)


def test_missing_evidence_is_not_fabricated(db):
    evidence = gather_evidence(db, acme_ticket(db))
    assert {m.area for m in evidence.missing_evidence} == {
        "logs", "argocd", "audit_history", "metrics",
    }
    assert not hasattr(evidence, "logs")


# ---------- analysis ----------


def test_causal_sequence_is_established(db):
    analysis = build_report(db, acme_ticket(db)).analysis
    steps = {s.key: s for s in analysis.causal_sequence}
    assert [s.key for s in analysis.causal_sequence] == [
        "previous_revision_healthy",
        "new_revision_deployed",
        "readiness_failures_after_deploy",
        "reduced_ready_capacity",
        "plausible_http_503_source",
    ]
    assert all(s.established for s in analysis.causal_sequence)
    assert steps["plausible_http_503_source"].kind == "inferred"
    assert all(
        steps[k].kind == "observed"
        for k in list(steps)[:4]
    )
    t1 = steps["previous_revision_healthy"].timestamp
    t2 = steps["new_revision_deployed"].timestamp
    t3 = steps["readiness_failures_after_deploy"].timestamp
    assert t1 < t2 < t3
    assert "Revision 41 was healthy" in steps["previous_revision_healthy"].description
    assert "Only 1 of 3 replicas are ready" in steps["reduced_ready_capacity"].description


def test_finding_and_recommendation_are_evidence_backed(db):
    report = build_report(db, acme_ticket(db))
    analysis, evidence = report.analysis, report.evidence

    assert analysis.outcome == "finding"
    (finding,) = analysis.findings
    assert finding.key == "deployment_regression"
    assert "recent deployment" in finding.root_cause.lower()
    assert "unhealthy" in finding.root_cause.lower()
    assert finding.confidence == "medium"  # never "high": no logs or metrics
    assert "plausible" in finding.summary
    assert any("logs" in l for l in finding.limitations)
    assert any("correlation" in l for l in finding.limitations)

    refs = all_refs(evidence)
    assert finding.evidence_refs
    assert set(finding.evidence_refs) <= refs
    assert {"revision/42", "revision/41"} <= set(finding.evidence_refs)
    assert {f"pod/{n}" for n in FAILING} <= set(finding.evidence_refs)
    assert any(r.startswith("event/") for r in finding.evidence_refs)
    assert "service/customer-portal-svc" in finding.evidence_refs
    assert any("customer-portal-svc" in r for r in finding.ruled_out)
    assert any("customer-portal-ingress" in r for r in finding.ruled_out)

    (action,) = analysis.recommended_actions
    assert action.action == "rollback"
    assert action.target_workload == "customer-portal"
    assert action.namespace == "acme-prod"
    assert action.cluster == "k8s-prod-east"
    assert (action.from_revision, action.to_revision) == (42, 41)
    assert action.status == "proposed"
    assert action.executed is False
    assert "policy engine" in action.note
    assert set(action.evidence_refs) <= refs

    for step in analysis.causal_sequence:
        assert set(step.evidence_refs) <= refs


def test_certificate_is_reported_as_unrelated(db):
    analysis = build_report(db, acme_ticket(db)).analysis
    assert any(
        "customer-portal-cert" in o and "unrelated" in o
        for o in analysis.other_observations
    )
    assert "certificate" not in analysis.findings[0].root_cause.lower()


def test_low_confidence_without_readiness_events(db):
    for e in db.query(PlatformEvent).filter_by(reason="Unhealthy").all():
        db.delete(e)
    db.commit()
    analysis = build_report(db, acme_ticket(db)).analysis
    (finding,) = analysis.findings
    assert finding.confidence == "low"
    steps = {s.key: s for s in analysis.causal_sequence}
    assert steps["readiness_failures_after_deploy"].established is False
    assert steps["plausible_http_503_source"].established is False


def test_no_finding_when_current_revision_is_healthy(db):
    db.query(DeploymentRevision).filter_by(revision=42).one().status = "healthy"
    db.commit()
    analysis = build_report(db, acme_ticket(db)).analysis
    assert analysis.outcome == "inconclusive"
    assert analysis.findings == [] and analysis.recommended_actions == []


def test_inactive_scenario_is_ignored(db):
    db.query(IncidentScenario).one().status = "resolved"
    db.commit()
    report = build_report(db, acme_ticket(db))
    evidence = report.evidence
    assert evidence.scenario is None
    assert evidence.pods.ready_count == 3
    assert [r.revision for r in evidence.deployment_history.revisions] == [40, 41]
    assert evidence.events.status == "missing"
    assert report.analysis.outcome == "inconclusive"


# ---------- isolation from other tenants and workloads ----------


@pytest.mark.parametrize(
    "slug, workload, key",
    [
        ("globex", "order-management", "TCK-2001"),
        ("initech", "employee-services", "TCK-3001"),
    ],
)
def test_other_tenants_do_not_receive_acme_scenario(db, slug, workload, key):
    report = build_report(db, other_ticket(db, slug, workload, key))
    evidence = report.evidence

    assert evidence.scenario is None
    assert all(p.source == "baseline" for p in evidence.pods.pods)
    assert evidence.pods.ready_count == evidence.pods.expected_replicas
    assert evidence.events.status == "missing"
    assert evidence.events.events == []
    assert evidence.deployment_history.status == "missing"
    assert {"events", "deployment_history"} <= {
        m.area for m in evidence.missing_evidence
    }
    assert report.analysis.outcome == "inconclusive"
    assert report.analysis.findings == []
    assert report.analysis.recommended_actions == []


def test_second_ticket_on_same_workload_does_not_inherit_scenario(db):
    ticket = other_ticket(db, "acme", "customer-portal", "TCK-1002")
    evidence = gather_evidence(db, ticket)
    assert evidence.scenario is None
    assert evidence.pods.ready_count == 3
    assert evidence.events.events == []
    # baseline history only; the failed scenario revision is hidden
    assert [r.revision for r in evidence.deployment_history.revisions] == [40, 41]
    assert analyze(evidence).outcome == "inconclusive"


# ---------- read-only behaviour ----------


def snapshot(session):
    models = [Pod, Workload, IncidentScenario, ScenarioPodState,
              DeploymentRevision, PlatformEvent, SupportTicket]
    return {
        m.__name__: [
            tuple(getattr(row, c.key) for c in m.__table__.columns)
            for row in session.query(m).order_by(m.id)
        ]
        for m in models
    }


def test_get_requests_only_select_and_change_nothing(client, factory):
    with factory() as db:
        ticket_id = acme_ticket(db).id
        before = snapshot(db)

    statements = []
    engine = factory.kw["bind"]

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for url in (
            f"{TICKETS_URL}/{ticket_id}",
            f"{TICKETS_URL}/{ticket_id}/investigation",
            f"{TICKETS_URL}/{ticket_id}/investigation/analysis",
        ):
            assert client.get(url).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements
    assert all(s.startswith("SELECT") for s in statements)
    with factory() as db:
        assert snapshot(db) == before


def test_analysis_service_is_read_only_in_session(db):
    ticket = acme_ticket(db)
    build_report(db, ticket)
    assert not db.new and not db.dirty and not db.deleted


def test_analyze_is_pure_over_evidence(db):
    evidence = gather_evidence(db, acme_ticket(db))
    assert analyze(evidence) == analyze(evidence)


# ---------- API ----------


def test_analysis_endpoint(client, db):
    ticket_id = acme_ticket(db).id
    response = client.get(f"{TICKETS_URL}/{ticket_id}/investigation/analysis")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ticket_id", "ticket_key", "generated_at", "evidence", "analysis"}
    assert body["ticket_key"] == "TCK-1001"
    assert body["evidence"]["scenario"]["key"] == SCENARIO_KEY
    analysis = body["analysis"]
    assert analysis["outcome"] == "finding"
    assert analysis["findings"][0]["confidence"] == "medium"
    action = analysis["recommended_actions"][0]
    assert (action["from_revision"], action["to_revision"]) == (42, 41)
    assert action["executed"] is False
    assert [r["revision"] for r in body["evidence"]["deployment_history"]["revisions"]] == [
        40, 41, 42,
    ]


def test_analysis_endpoint_errors_and_methods(client):
    assert client.get(f"{TICKETS_URL}/9999/investigation/analysis").status_code == 404
    assert client.get(f"{TICKETS_URL}/abc/investigation/analysis").status_code == 422
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(f"{TICKETS_URL}/1/investigation/analysis")
        assert response.status_code == 405
