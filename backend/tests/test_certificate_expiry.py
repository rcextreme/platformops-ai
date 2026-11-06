from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Certificate, Cluster, Ingress, Namespace
from app.seed import seed_database
from app.services.certificate_expiry import (
    CRITICAL_DAYS,
    WARNING_DAYS,
    CertificateFinding,
    classify,
    evaluate_certificate,
    evaluate_certificates,
    normalize_utc,
    summarize,
)

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_cert(expires_at, renewal_status="not_due", name="cert-a"):
    return Certificate(
        id=1,
        name=name,
        namespace=Namespace(name="ns-a"),
        cluster=Cluster(name="cluster-a"),
        ingress=Ingress(name="ing-a"),
        tls_secret_name="secret-a",
        issuer="Let's Encrypt",
        subject="host.example.com",
        issued_at=NOW - timedelta(days=60),
        expires_at=expires_at,
        status="valid",
        renewal_status=renewal_status,
    )


# ---------- pure unit tests ----------


def test_thresholds():
    assert CRITICAL_DAYS == 7
    assert WARNING_DAYS == 30


@pytest.mark.parametrize(
    "delta, severity",
    [
        (timedelta(days=-1), "expired"),
        (timedelta(seconds=-1), "expired"),
        (timedelta(0), "expired"),
        (timedelta(seconds=1), "critical"),
        (timedelta(days=7), "critical"),
        (timedelta(days=7, seconds=1), "warning"),
        (timedelta(days=8), "warning"),
        (timedelta(days=30), "warning"),
        (timedelta(days=30, seconds=1), "healthy"),
        (timedelta(days=31), "healthy"),
    ],
)
def test_severity_boundaries(delta, severity):
    finding = evaluate_certificate(make_cert(NOW + delta), NOW)
    assert finding.severity == severity


def test_days_and_seconds_remaining():
    finding = evaluate_certificate(
        make_cert(NOW + timedelta(days=7, hours=23)), NOW
    )
    assert finding.days_remaining == 7
    assert finding.seconds_remaining == 7 * 86400 + 23 * 3600


def test_days_remaining_floors_for_expired():
    finding = evaluate_certificate(make_cert(NOW - timedelta(hours=1)), NOW)
    assert finding.days_remaining == -1
    assert finding.seconds_remaining == -3600


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 6, 8, 12, 0, 0)
    assert normalize_utc(naive) == datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    severity, remaining = classify(naive, NOW)
    assert severity == "critical"
    assert remaining == timedelta(days=7)


def test_aware_non_utc_converted():
    tz = timezone(timedelta(hours=5))
    aware = datetime(2026, 6, 8, 17, 0, 0, tzinfo=tz)  # 12:00 UTC
    assert normalize_utc(aware) == datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    assert normalize_utc(aware).utcoffset() == timedelta(0)
    assert classify(aware, NOW)[0] == "critical"


def test_naive_now_accepted():
    severity, _ = classify(NOW + timedelta(days=3), NOW.replace(tzinfo=None))
    assert severity == "critical"


def test_normalize_requires_value():
    with pytest.raises(ValueError):
        normalize_utc(None)


@pytest.mark.parametrize(
    "status, attention",
    [
        ("failed", True),
        ("pending", True),
        ("in_progress", True),
        ("not_due", False),
        ("renewed", False),
    ],
)
def test_renewal_attention(status, attention):
    finding = evaluate_certificate(make_cert(NOW + timedelta(days=60), status), NOW)
    assert finding.renewal_attention is attention
    assert finding.renewal_status == status


@pytest.mark.parametrize("status", ["failed", "pending", "in_progress", "not_due"])
def test_renewal_attention_does_not_change_severity(status):
    expires = NOW + timedelta(days=12)
    baseline = evaluate_certificate(make_cert(expires, "not_due"), NOW)
    finding = evaluate_certificate(make_cert(expires, status), NOW)
    assert finding.severity == baseline.severity == "warning"


def test_all_finding_fields():
    expires = NOW + timedelta(days=12, hours=6)
    finding = evaluate_certificate(make_cert(expires, "failed"), NOW)
    assert isinstance(finding, CertificateFinding)
    assert finding.model_dump() == {
        "certificate_id": 1,
        "certificate_name": "cert-a",
        "namespace": "ns-a",
        "cluster": "cluster-a",
        "hostname": "host.example.com",
        "issuer": "Let's Encrypt",
        "tls_secret_name": "secret-a",
        "ingress_name": "ing-a",
        "expires_at": expires,
        "days_remaining": 12,
        "seconds_remaining": 12 * 86400 + 6 * 3600,
        "severity": "warning",
        "renewal_status": "failed",
        "renewal_attention": True,
    }


def test_missing_ingress_gives_none():
    cert = make_cert(NOW + timedelta(days=60))
    cert.ingress = None
    assert evaluate_certificate(cert, NOW).ingress_name is None


def test_missing_relationships_raise():
    cert = make_cert(NOW + timedelta(days=60))
    cert.namespace = None
    with pytest.raises(ValueError):
        evaluate_certificate(cert, NOW)


def test_evaluation_does_not_mutate_certificate():
    expires = datetime(2026, 6, 20, 12, 0, 0)  # naive on purpose
    cert = make_cert(expires, "failed")
    before = {c.key: getattr(cert, c.key) for c in Certificate.__table__.columns}
    evaluate_certificate(cert, NOW)
    after = {c.key: getattr(cert, c.key) for c in Certificate.__table__.columns}
    assert before == after
    assert cert.expires_at is expires
    assert cert.expires_at.tzinfo is None


def test_summarize_always_has_all_severities():
    findings = [
        evaluate_certificate(make_cert(NOW + d, name=f"c{i}"), NOW)
        for i, d in enumerate([timedelta(days=-1), timedelta(days=3), timedelta(days=3)])
    ]
    assert summarize(findings) == {
        "expired": 1,
        "critical": 2,
        "warning": 0,
        "healthy": 0,
    }
    assert summarize([]) == {"expired": 0, "critical": 0, "warning": 0, "healthy": 0}


# ---------- SQLite integration tests ----------


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    assert engine.dialect.name == "sqlite"
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=True, autocommit=False)
    with factory() as db:
        seed_database(db)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    with session_factory() as session:
        yield session


def test_seeded_certificates_evaluated(db):
    findings = evaluate_certificates(db)
    assert [f.certificate_name for f in findings] == [
        "customer-portal-cert",
        "order-management-cert",
    ]
    acme, globex = findings

    assert acme.namespace == "acme-prod"
    assert acme.cluster == "k8s-prod-east"
    assert acme.hostname == "portal.acme.example.com"
    assert acme.ingress_name == "customer-portal-ingress"
    assert acme.tls_secret_name == "acme-portal-tls"
    assert acme.severity == "warning"
    assert acme.days_remaining in (11, 12)
    assert acme.renewal_status == "failed"
    assert acme.renewal_attention is True

    assert globex.namespace == "globex-prod"
    assert globex.hostname == "orders.globex.example.com"
    assert globex.severity == "healthy"
    assert globex.days_remaining in (59, 60)
    assert globex.renewal_attention is False

    assert summarize(findings) == {
        "expired": 0,
        "critical": 0,
        "warning": 1,
        "healthy": 1,
    }


def test_initech_has_no_certificate(db):
    assert evaluate_certificates(db, namespace="initech-dev") == []


def test_sorting_by_urgency_attention_then_expiry(db):
    base = db.query(Certificate).filter_by(name="customer-portal-cert").one()
    now = datetime.now(timezone.utc)

    def add(name, secret, delta, renewal):
        db.add(
            Certificate(
                name=name,
                namespace_id=base.namespace_id,
                cluster_id=base.cluster_id,
                ingress_id=base.ingress_id,
                tls_secret_name=secret,
                issuer="Test CA",
                subject="x.example.com",
                issued_at=now - timedelta(days=90),
                expires_at=now + delta,
                renewal_status=renewal,
            )
        )

    add("expired-cert", "s1", timedelta(days=-2), "not_due")
    add("critical-cert", "s2", timedelta(days=3), "not_due")
    add("warning-early-ok", "s3", timedelta(days=9), "not_due")
    add("warning-late-failed", "s4", timedelta(days=25), "pending")
    db.commit()

    names = [f.certificate_name for f in evaluate_certificates(db, now=now)]
    assert names == [
        "expired-cert",
        "critical-cert",
        "customer-portal-cert",  # attention, earliest expiry (~12 days)
        "warning-late-failed",  # attention, 25 days
        "warning-early-ok",  # no attention, sorts after attention items
        "order-management-cert",
    ]


def test_severity_and_scope_filters(db):
    assert [f.certificate_name for f in evaluate_certificates(db, severity="warning")] == [
        "customer-portal-cert"
    ]
    assert [
        f.certificate_name
        for f in evaluate_certificates(db, severity=["warning", "healthy"])
    ] == ["customer-portal-cert", "order-management-cert"]
    assert [
        f.certificate_name for f in evaluate_certificates(db, include_healthy=False)
    ] == ["customer-portal-cert"]
    assert [
        f.certificate_name
        for f in evaluate_certificates(db, namespace="globex-prod")
    ] == ["order-management-cert"]
    assert evaluate_certificates(db, cluster="k8s-dev-east") == []
    assert evaluate_certificates(db, severity="expired") == []


def test_unknown_severity_rejected(db):
    with pytest.raises(ValueError):
        evaluate_certificates(db, severity="bogus")


def test_future_now_changes_findings(db):
    later = datetime.now(timezone.utc) + timedelta(days=35)
    by_name = {f.certificate_name: f for f in evaluate_certificates(db, now=later)}
    assert by_name["customer-portal-cert"].severity == "expired"
    assert by_name["order-management-cert"].severity == "warning"


def test_naive_datetimes_from_sqlite_are_handled(session_factory):
    with session_factory() as db:
        raw = db.query(Certificate).filter_by(name="customer-portal-cert").one()
        assert raw.expires_at.tzinfo is None  # SQLite drops timezone info
        raw_expiry = raw.expires_at
        db.expunge_all()

        finding = evaluate_certificates(db, namespace="acme-prod")[0]
        assert finding.expires_at.tzinfo is not None
        assert finding.expires_at == raw_expiry.replace(tzinfo=timezone.utc)
        assert finding.expires_at.utcoffset() == timedelta(0)


def test_evaluation_is_read_only(session_factory):
    with session_factory() as db:
        before = [
            (c.id, c.name, c.expires_at, c.issued_at, c.status, c.renewal_status)
            for c in db.query(Certificate).order_by(Certificate.id)
        ]
        db.expire_all()

        statements = []

        @event.listens_for(db.get_bind(), "before_cursor_execute")
        def capture(conn, cursor, statement, *args):
            statements.append(statement.lstrip().upper())

        evaluate_certificates(db)
        evaluate_certificates(db, severity="warning")

        assert statements
        assert all(s.startswith("SELECT") for s in statements)
        assert not db.new and not db.dirty and not db.deleted

    with session_factory() as db2:
        after = [
            (c.id, c.name, c.expires_at, c.issued_at, c.status, c.renewal_status)
            for c in db2.query(Certificate).order_by(Certificate.id)
        ]
    assert before == after


def test_does_not_flush_pending_caller_changes(session_factory):
    with session_factory() as db:
        cert = db.query(Certificate).filter_by(name="customer-portal-cert").one()
        cert.status = "pending-change"
        evaluate_certificates(db)
        assert cert in db.dirty
        db.rollback()

    with session_factory() as db2:
        stored = db2.query(Certificate).filter_by(name="customer-portal-cert").one()
        assert stored.status == "valid"
