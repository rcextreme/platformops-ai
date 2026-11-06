from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Certificate
from app.seed import seed_database
from app.services.certificate_expiry import summarize, evaluate_certificates

LIST_URL = "/api/platform/certificates"
SUMMARY_URL = "/api/platform/certificates/summary"

FINDING_FIELDS = {
    "certificate_id",
    "certificate_name",
    "namespace",
    "cluster",
    "hostname",
    "issuer",
    "tls_secret_name",
    "ingress_name",
    "expires_at",
    "days_remaining",
    "seconds_remaining",
    "severity",
    "renewal_status",
    "renewal_attention",
}


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
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
def client(factory):
    # Not used as a context manager, so the app lifespan (create_all + seed
    # against the real engine) never runs.
    return TestClient(app)


def add_certificates(factory, specs):
    """specs: list of (name, delta, renewal_status), cloned from the ACME cert."""
    now = datetime.now(timezone.utc)
    with factory() as db:
        base = db.query(Certificate).filter_by(name="customer-portal-cert").one()
        for i, (name, delta, renewal) in enumerate(specs):
            db.add(
                Certificate(
                    name=name,
                    namespace_id=base.namespace_id,
                    cluster_id=base.cluster_id,
                    ingress_id=base.ingress_id,
                    tls_secret_name=f"extra-secret-{i}",
                    issuer="Test CA",
                    subject="extra.example.com",
                    issued_at=now - timedelta(days=90),
                    expires_at=now + delta,
                    renewal_status=renewal,
                )
            )
        db.commit()


def names(response):
    return [f["certificate_name"] for f in response.json()["findings"]]


# ---------- list endpoint ----------


def test_list_default(client):
    response = client.get(LIST_URL)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"generated_at", "count", "findings"}
    assert body["count"] == len(body["findings"]) == 2
    assert names(response) == ["customer-portal-cert", "order-management-cert"]

    acme, globex = body["findings"]
    assert set(acme) == FINDING_FIELDS
    assert acme["namespace"] == "acme-prod"
    assert acme["cluster"] == "k8s-prod-east"
    assert acme["hostname"] == "portal.acme.example.com"
    assert acme["issuer"] == "Let's Encrypt"
    assert acme["tls_secret_name"] == "acme-portal-tls"
    assert acme["ingress_name"] == "customer-portal-ingress"
    assert acme["severity"] == "warning"
    assert acme["days_remaining"] in (11, 12)
    assert acme["renewal_status"] == "failed"
    assert acme["renewal_attention"] is True
    assert globex["severity"] == "healthy"
    assert globex["renewal_attention"] is False


def test_list_timestamps_are_timezone_aware(client):
    body = client.get(LIST_URL).json()
    generated = datetime.fromisoformat(body["generated_at"])
    assert generated.utcoffset() == timedelta(0)
    for finding in body["findings"]:
        assert datetime.fromisoformat(finding["expires_at"]).utcoffset() == timedelta(0)


def test_list_severity_filters(client):
    assert names(client.get(LIST_URL, params={"severity": "warning"})) == [
        "customer-portal-cert"
    ]
    assert names(client.get(LIST_URL, params={"severity": "healthy"})) == [
        "order-management-cert"
    ]
    assert names(client.get(LIST_URL, params={"severity": "expired"})) == []
    both = client.get(LIST_URL, params=[("severity", "warning"), ("severity", "healthy")])
    assert names(both) == ["customer-portal-cert", "order-management-cert"]


def test_list_include_healthy_false(client):
    response = client.get(LIST_URL, params={"include_healthy": "false"})
    assert names(response) == ["customer-portal-cert"]
    assert response.json()["count"] == 1


def test_list_namespace_and_cluster_filters(client):
    assert names(client.get(LIST_URL, params={"namespace": "globex-prod"})) == [
        "order-management-cert"
    ]
    assert names(client.get(LIST_URL, params={"cluster": "k8s-prod-east"})) == [
        "customer-portal-cert",
        "order-management-cert",
    ]
    for params in ({"namespace": "initech-dev"}, {"cluster": "k8s-dev-east"},
                   {"namespace": "does-not-exist"}):
        response = client.get(LIST_URL, params=params)
        assert response.status_code == 200
        assert response.json()["findings"] == []
        assert response.json()["count"] == 0


@pytest.mark.parametrize(
    "params",
    [{"severity": "bogus"}, {"include_healthy": "notabool"}],
)
def test_list_invalid_parameters_rejected(client, params):
    assert client.get(LIST_URL, params=params).status_code == 422


def test_list_preserves_service_order(client, factory):
    add_certificates(
        factory,
        [
            ("expired-cert", timedelta(days=-2), "not_due"),
            ("critical-cert", timedelta(days=3), "not_due"),
            ("warning-ok", timedelta(days=9), "not_due"),
            ("warning-pending", timedelta(days=25), "pending"),
        ],
    )
    api_names = names(client.get(LIST_URL))
    with factory() as db:
        service_names = [f.certificate_name for f in evaluate_certificates(db)]
    assert api_names == service_names
    assert api_names == [
        "expired-cert",
        "critical-cert",
        "customer-portal-cert",
        "warning-pending",
        "warning-ok",
        "order-management-cert",
    ]


# ---------- summary endpoint ----------


def test_summary_seeded(client):
    response = client.get(SUMMARY_URL)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "generated_at",
        "total",
        "counts",
        "renewal_attention",
        "warning_days",
        "critical_days",
    }
    assert body["counts"] == {"expired": 0, "critical": 0, "warning": 1, "healthy": 1}
    assert body["total"] == 2
    assert body["renewal_attention"] == 1
    assert body["warning_days"] == 30
    assert body["critical_days"] == 7


def test_summary_filters_and_zero_counts(client):
    body = client.get(SUMMARY_URL, params={"namespace": "globex-prod"}).json()
    assert body["counts"] == {"expired": 0, "critical": 0, "warning": 0, "healthy": 1}
    assert body["total"] == 1
    assert body["renewal_attention"] == 0

    empty = client.get(SUMMARY_URL, params={"cluster": "k8s-dev-east"}).json()
    assert empty["counts"] == {"expired": 0, "critical": 0, "warning": 0, "healthy": 0}
    assert empty["total"] == 0
    assert empty["renewal_attention"] == 0


def test_summary_counts_expired_critical_and_attention(client, factory):
    add_certificates(
        factory,
        [
            ("expired-cert", timedelta(days=-2), "failed"),
            ("critical-cert", timedelta(days=3), "in_progress"),
            ("critical-ok", timedelta(days=5), "renewed"),
        ],
    )
    body = client.get(SUMMARY_URL).json()
    assert body["counts"] == {"expired": 1, "critical": 2, "warning": 1, "healthy": 1}
    assert body["total"] == 5
    # seeded ACME (failed) + expired (failed) + critical (in_progress)
    assert body["renewal_attention"] == 3


def test_summary_matches_list_findings(client, factory):
    add_certificates(factory, [("expired-cert", timedelta(days=-1), "pending")])
    listing = client.get(LIST_URL).json()["findings"]
    summary = client.get(SUMMARY_URL).json()

    from app.services.certificate_expiry import CertificateFinding

    findings = [CertificateFinding(**f) for f in listing]
    assert summary["counts"] == summarize(findings)
    assert summary["renewal_attention"] == sum(f.renewal_attention for f in findings)


def test_summary_ignores_include_healthy(client):
    body = client.get(SUMMARY_URL, params={"include_healthy": "false"}).json()
    assert body["counts"]["healthy"] == 1


# ---------- read-only, routing, isolation ----------


def test_requests_only_issue_select_and_change_nothing(client, factory):
    def snapshot():
        with factory() as db:
            return [
                (c.id, c.name, c.expires_at, c.issued_at, c.status, c.renewal_status)
                for c in db.query(Certificate).order_by(Certificate.id)
            ]

    before = snapshot()
    statements = []
    engine = factory.kw["bind"]

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        client.get(LIST_URL)
        client.get(LIST_URL, params={"severity": "warning"})
        client.get(SUMMARY_URL)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements
    assert all(s.startswith("SELECT") for s in statements)
    assert snapshot() == before


@pytest.mark.parametrize("url", [LIST_URL, SUMMARY_URL])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_not_allowed(client, url, method):
    assert getattr(client, method)(url).status_code == 405


def test_summary_route_not_shadowed_by_list(client):
    assert "counts" in client.get(SUMMARY_URL).json()
    assert "findings" in client.get(LIST_URL).json()


def test_tests_use_sqlite_override_only(client):
    override = app.dependency_overrides[get_db]
    session = next(override())
    try:
        assert session.get_bind().dialect.name == "sqlite"
    finally:
        session.close()
