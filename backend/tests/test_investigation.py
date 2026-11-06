from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import configure_mappers, sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    Certificate,
    Ingress,
    Namespace,
    Pod,
    Service,
    SupportTicket,
    Tenant,
    Workload,
)
from app.seed import get_or_create_support_ticket, seed_database
from app.services.investigation import NOT_COLLECTED, gather_evidence

TICKETS_URL = "/api/platform/tickets"


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'investigation.db'}",
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


def missing_areas(evidence):
    return {m.area for m in evidence.missing_evidence}


# ---------- model and seed ----------


def test_mappers_configure():
    configure_mappers()


def test_seeded_ticket(db):
    ticket = acme_ticket(db)
    assert ticket.title == "Customer Portal returning HTTP 503"
    assert "503" in ticket.description
    assert ticket.severity == "high"
    assert ticket.status == "open"
    assert ticket.tenant.slug == "acme"
    assert ticket.workload.name == "customer-portal"
    assert ticket.created_at is not None
    assert ticket in ticket.tenant.support_tickets
    assert ticket in ticket.workload.support_tickets


def test_ticket_seed_is_idempotent(factory):
    with factory() as db:
        seed_database(db)
        seed_database(db)
        assert db.query(SupportTicket).count() == 1


def test_ticket_helper_returns_existing_unchanged(db):
    tenant = db.query(Tenant).filter_by(slug="acme").one()
    workload = db.query(Workload).filter_by(name="customer-portal").one()
    ticket = get_or_create_support_ticket(
        db, tenant, workload, "TCK-1001", title="Different", description="x"
    )
    assert ticket.title == "Customer Portal returning HTTP 503"
    assert db.query(SupportTicket).count() == 1


def test_ticket_helper_rejects_tenant_workload_mismatch(db):
    globex = db.query(Tenant).filter_by(slug="globex").one()
    workload = db.query(Workload).filter_by(name="customer-portal").one()
    with pytest.raises(ValueError):
        get_or_create_support_ticket(
            db, globex, workload, "TCK-9999", title="t", description="d"
        )


def test_baseline_seed_data_is_healthy(db):
    assert db.query(Pod).filter(Pod.ready_containers != 1).count() == 0
    assert db.query(Pod).filter(Pod.restart_count != 0).count() == 0
    assert all(w.status == "healthy" for w in db.query(Workload))


# ---------- evidence gathering ----------


def test_seeded_ticket_evidence(db):
    evidence = gather_evidence(db, acme_ticket(db))

    assert evidence.ticket_key == "TCK-1001"
    assert evidence.tenant.name == "ACME Corp"
    assert evidence.tenant.slug == "acme"
    assert evidence.workload.name == "customer-portal"
    assert evidence.workload.desired_replicas == 3
    assert evidence.workload.ready_replicas == 3
    assert evidence.placement.cluster == "k8s-prod-east"
    assert evidence.placement.cluster_environment == "production"
    assert evidence.placement.namespace == "acme-prod"

    pods = evidence.pods
    assert pods.status == "observed"
    assert pods.expected_replicas == 3
    assert pods.observed_count == 3
    assert pods.ready_count == 1
    assert pods.baseline_ready_count == 3
    assert pods.total_restarts == 8
    assert [p.name for p in pods.pods] == [
        "customer-portal-7d9f6c-abc12",
        "customer-portal-7d9f6c-def34",
        "customer-portal-7d9f6c-ghi56",
    ]
    assert [p.ready for p in pods.pods] == [True, False, False]
    assert all(p.status == "Running" for p in pods.pods)
    assert all(p.node and "worker" in p.node for p in pods.pods)
    assert all(p.pod_ip for p in pods.pods)

    assert evidence.services.status == "observed"
    (svc,) = evidence.services.services
    assert (svc.name, svc.port, svc.target_port, svc.protocol) == (
        "customer-portal-svc",
        80,
        8080,
        "TCP",
    )
    assert svc.status == "healthy"
    assert svc.cluster_ip

    assert evidence.ingresses.status == "observed"
    (ing,) = evidence.ingresses.ingresses
    assert ing.name == "customer-portal-ingress"
    assert ing.host == "portal.acme.example.com"
    assert ing.service == "customer-portal-svc"
    assert ing.tls_enabled is True
    assert ing.tls_secret_name == "acme-portal-tls"
    assert ing.status == "healthy"

    assert evidence.certificates.status == "observed"
    (cert,) = evidence.certificates.certificates
    assert cert.certificate_name == "customer-portal-cert"
    assert cert.hostname == "portal.acme.example.com"
    assert cert.severity == "warning"
    assert cert.renewal_status == "failed"
    assert cert.renewal_attention is True


def test_not_collected_areas_are_declared_missing(db):
    evidence = gather_evidence(db, acme_ticket(db))
    assert missing_areas(evidence) == set(NOT_COLLECTED)
    assert set(NOT_COLLECTED) == {"logs", "argocd", "audit_history", "metrics"}
    assert evidence.events.status == "observed"
    assert evidence.deployment_history.status == "observed"


def test_evidence_contains_no_diagnosis_fields(db):
    fields = set(gather_evidence(db, acme_ticket(db)).model_dump())
    assert not fields & {"diagnosis", "root_cause", "confidence", "recommendation"}


def test_baseline_pod_state_is_used_where_no_override_applies(db):
    healthy = db.query(Pod).filter_by(name="customer-portal-7d9f6c-abc12").one()
    healthy.status = "CrashLoopBackOff"
    healthy.ready_containers = 0
    healthy.restart_count = 7
    db.commit()

    evidence = gather_evidence(db, acme_ticket(db)).pods
    assert evidence.status == "observed"
    assert evidence.observed_count == 3
    assert evidence.ready_count == 0
    assert evidence.total_restarts == 7 + 3 + 5
    assert [p.ready for p in evidence.pods] == [False, False, False]
    assert evidence.pods[0].status == "CrashLoopBackOff"
    assert evidence.pods[0].source == "baseline"
    assert evidence.pods[1].source == "scenario_override"


def test_pods_are_scoped_to_workload_and_namespace(db):
    globex_ns = db.query(Namespace).filter_by(name="globex-prod").one()
    acme_ns = db.query(Namespace).filter_by(name="acme-prod").one()
    db.add_all(
        [
            # same name pattern, other namespace
            Pod(name="customer-portal-aaaa-bbbb", namespace_id=globex_ns.id,
                cluster_id=globex_ns.cluster_id),
            # same namespace, different workload that shares a name prefix
            Pod(name="customer-portal-admin-cccc-dddd", namespace_id=acme_ns.id,
                cluster_id=acme_ns.cluster_id),
            Pod(name="customer-portalx-eeee-ffff", namespace_id=acme_ns.id,
                cluster_id=acme_ns.cluster_id),
        ]
    )
    db.commit()

    evidence = gather_evidence(db, acme_ticket(db)).pods
    assert evidence.observed_count == 3
    assert all(p.name.startswith("customer-portal-7d9f6c-") for p in evidence.pods)


def test_missing_pods(db):
    for pod in db.query(Pod).filter(Pod.name.like("customer-portal-%")):
        db.delete(pod)
    db.commit()

    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.pods.status == "missing"
    assert evidence.pods.observed_count == 0
    assert evidence.pods.expected_replicas == 3
    assert "pods" in missing_areas(evidence)
    assert evidence.services.status == "observed"


def test_missing_services_and_ingresses(db):
    for cert in db.query(Certificate):
        db.delete(cert)
    for ingress in db.query(Ingress).filter(Ingress.name == "customer-portal-ingress"):
        db.delete(ingress)
    db.commit()
    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.ingresses.status == "missing"
    assert evidence.certificates.status == "not_applicable"
    assert "ingresses" in missing_areas(evidence)

    for service in db.query(Service).filter(Service.name == "customer-portal-svc"):
        db.delete(service)
    db.commit()
    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.services.status == "missing"
    assert "services" in missing_areas(evidence)


def test_tls_enabled_without_certificate_is_missing(db):
    for cert in db.query(Certificate).filter_by(name="customer-portal-cert"):
        db.delete(cert)
    db.commit()
    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.certificates.status == "missing"
    assert "certificates" in missing_areas(evidence)


def test_tls_disabled_certificates_not_applicable(db):
    db.query(Ingress).filter_by(name="customer-portal-ingress").one().tls_enabled = False
    db.commit()
    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.certificates.status == "not_applicable"
    assert "certificates" not in missing_areas(evidence)


def test_ticket_without_workload(db):
    tenant = db.query(Tenant).filter_by(slug="acme").one()
    ticket = SupportTicket(
        ticket_key="TCK-2000", tenant_id=tenant.id, title="Something broken",
        description="No idea which app.",
    )
    db.add(ticket)
    db.commit()

    evidence = gather_evidence(db, ticket)
    assert evidence.workload is None
    assert evidence.placement is None
    for section in (evidence.pods, evidence.services, evidence.ingresses,
                    evidence.certificates):
        assert section.status == "missing"
    assert "workload" in missing_areas(evidence)


def test_missing_namespace_record(db):
    db.query(Pod).delete()
    db.query(Certificate).delete()
    db.query(Ingress).delete()
    db.query(Service).delete()
    db.query(Namespace).filter_by(name="acme-prod").delete()
    db.commit()
    evidence = gather_evidence(db, acme_ticket(db))
    assert evidence.placement.namespace is None
    assert evidence.pods.status == "missing"
    assert "placement" in missing_areas(evidence)


def test_future_now_changes_certificate_severity(db):
    later = datetime.now(timezone.utc) + timedelta(days=20)
    cert = gather_evidence(db, acme_ticket(db), now=later).certificates.certificates[0]
    assert cert.severity == "expired"


def test_gather_evidence_is_read_only(factory):
    tables = [Pod, Service, Ingress, Certificate, Workload, SupportTicket]

    def snapshot(session):
        return {
            t.__name__: [
                tuple(getattr(row, c.key) for c in t.__table__.columns)
                for row in session.query(t).order_by(t.id)
            ]
            for t in tables
        }

    with factory() as db:
        ticket = acme_ticket(db)
        before = snapshot(db)
        db.expire_all()

        statements = []
        engine = db.get_bind()

        def capture(conn, cursor, statement, *args):
            statements.append(statement.lstrip().upper())

        event.listen(engine, "before_cursor_execute", capture)
        try:
            gather_evidence(db, ticket)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert statements
        assert all(s.startswith("SELECT") for s in statements)
        assert not db.new and not db.dirty and not db.deleted

    with factory() as db2:
        assert snapshot(db2) == before


def test_does_not_flush_pending_caller_changes(factory):
    with factory() as db:
        ticket = acme_ticket(db)
        db.query(Pod).first().restart_count = 99
        gather_evidence(db, ticket)
        assert db.dirty
        db.rollback()
    with factory() as db2:
        assert db2.query(Pod).filter(Pod.restart_count == 99).count() == 0


# ---------- API ----------


def test_list_tickets(client):
    response = client.get(TICKETS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    (ticket,) = body["tickets"]
    assert ticket["ticket_key"] == "TCK-1001"
    assert ticket["tenant"] == "ACME Corp"
    assert ticket["workload"] == "customer-portal"
    assert ticket["namespace"] == "acme-prod"
    assert ticket["cluster"] == "k8s-prod-east"
    assert ticket["severity"] == "high"
    assert ticket["status"] == "open"
    assert ticket["created_at"]


def test_list_tickets_filters_and_order(client, factory):
    with factory() as db:
        globex = db.query(Tenant).filter_by(slug="globex").one()
        db.add(
            SupportTicket(
                ticket_key="TCK-1002",
                tenant_id=globex.id,
                title="Slow orders",
                description="Orders page is slow.",
                status="closed",
                created_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        db.commit()

    keys = [t["ticket_key"] for t in client.get(TICKETS_URL).json()["tickets"]]
    assert keys == ["TCK-1002", "TCK-1001"]

    by_status = client.get(TICKETS_URL, params={"status": "open"}).json()
    assert [t["ticket_key"] for t in by_status["tickets"]] == ["TCK-1001"]
    by_tenant = client.get(TICKETS_URL, params={"tenant": "globex"}).json()
    assert [t["ticket_key"] for t in by_tenant["tickets"]] == ["TCK-1002"]
    assert by_tenant["tickets"][0]["workload"] is None
    assert client.get(TICKETS_URL, params={"tenant": "nope"}).json()["count"] == 0


def test_get_ticket(client, db):
    ticket_id = acme_ticket(db).id
    response = client.get(f"{TICKETS_URL}/{ticket_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ticket_id
    assert body["ticket_key"] == "TCK-1001"
    assert "503" in body["description"]


def test_get_ticket_not_found_and_invalid_id(client):
    assert client.get(f"{TICKETS_URL}/9999").status_code == 404
    assert client.get(f"{TICKETS_URL}/9999/investigation").status_code == 404
    assert client.get(f"{TICKETS_URL}/abc").status_code == 422
    assert client.get(f"{TICKETS_URL}/abc/investigation").status_code == 422


def test_investigation_endpoint(client, db):
    ticket_id = acme_ticket(db).id
    response = client.get(f"{TICKETS_URL}/{ticket_id}/investigation")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {
        "ticket_id", "ticket_key", "ticket_title", "generated_at", "tenant",
        "scenario", "workload", "placement", "pods", "services", "ingresses",
        "certificates", "events", "deployment_history", "missing_evidence",
    }
    assert body["ticket_id"] == ticket_id
    assert datetime.fromisoformat(body["generated_at"]).utcoffset() == timedelta(0)
    assert body["tenant"]["slug"] == "acme"
    assert body["scenario"]["key"] == "acme-customer-portal-503"
    assert body["scenario"]["simulated"] is True
    assert body["workload"]["name"] == "customer-portal"
    assert body["placement"]["cluster"] == "k8s-prod-east"
    assert body["pods"]["status"] == "observed"
    assert body["pods"]["ready_count"] == 1
    assert body["services"]["services"][0]["name"] == "customer-portal-svc"
    assert body["ingresses"]["ingresses"][0]["host"] == "portal.acme.example.com"
    assert body["certificates"]["certificates"][0]["severity"] == "warning"
    assert body["events"]["status"] == "observed"
    assert body["deployment_history"]["current_revision"] == 42
    assert {m["area"] for m in body["missing_evidence"]} == set(NOT_COLLECTED)


def test_investigation_reflects_baseline_change_where_not_overridden(client, factory, db):
    ticket_id = acme_ticket(db).id
    with factory() as session:
        pod = session.query(Pod).filter_by(name="customer-portal-7d9f6c-abc12").one()
        pod.ready_containers = 0
        pod.restart_count = 4
        session.commit()

    body = client.get(f"{TICKETS_URL}/{ticket_id}/investigation").json()
    assert body["pods"]["ready_count"] == 0
    assert body["pods"]["total_restarts"] == 4 + 3 + 5


def test_ticket_endpoints_are_read_only(client, factory, db):
    ticket_id = acme_ticket(db).id
    statements = []
    engine = factory.kw["bind"]

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        client.get(TICKETS_URL)
        client.get(f"{TICKETS_URL}/{ticket_id}")
        client.get(f"{TICKETS_URL}/{ticket_id}/investigation")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements
    assert all(s.startswith("SELECT") for s in statements)


@pytest.mark.parametrize("suffix", ["", "/1", "/1/investigation"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_not_allowed(client, suffix, method):
    assert getattr(client, method)(f"{TICKETS_URL}{suffix}").status_code == 405


def test_api_uses_sqlite_override_only(client):
    session = next(app.dependency_overrides[get_db]())
    try:
        assert session.get_bind().dialect.name == "sqlite"
    finally:
        session.close()
