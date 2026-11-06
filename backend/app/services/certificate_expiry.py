"""Read-only evaluation of TLS certificate expiration.

Layers, from lowest to highest, so callers can reuse only what they need:
  normalize_utc / classify      pure functions, no database or clock access
  evaluate_certificate          one Certificate object -> one finding
  evaluate_certificates         database query -> sorted findings
  summarize                     findings -> counts per severity

Nothing in this module adds, flushes, commits, or modifies ORM records.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from ..models import Certificate, Cluster, Namespace

CRITICAL_DAYS = 7
WARNING_DAYS = 30

Severity = Literal["expired", "critical", "warning", "healthy"]

# Most urgent first; also defines the sort order of findings.
SEVERITY_ORDER: dict[str, int] = {
    "expired": 0,
    "critical": 1,
    "warning": 2,
    "healthy": 3,
}

RENEWAL_ATTENTION_STATUSES = frozenset({"failed", "pending", "in_progress"})


class CertificateFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    certificate_id: int | None
    certificate_name: str
    namespace: str
    cluster: str
    hostname: str
    issuer: str
    tls_secret_name: str | None
    ingress_name: str | None
    expires_at: datetime
    days_remaining: int
    seconds_remaining: int
    severity: Severity
    renewal_status: str | None
    renewal_attention: bool


def normalize_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Naive values (such as those SQLite returns) are assumed to already be UTC.
    Aware values (such as those PostgreSQL returns) are converted to UTC.
    """
    if value is None:
        raise ValueError("A datetime is required")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify(expires_at: datetime, now: datetime) -> tuple[Severity, timedelta]:
    """Return (severity, time remaining) using exact timedelta comparisons."""
    remaining = normalize_utc(expires_at) - normalize_utc(now)

    if remaining <= timedelta(0):
        severity: Severity = "expired"
    elif remaining <= timedelta(days=CRITICAL_DAYS):
        severity = "critical"
    elif remaining <= timedelta(days=WARNING_DAYS):
        severity = "warning"
    else:
        severity = "healthy"

    return severity, remaining


def needs_renewal_attention(renewal_status: str | None) -> bool:
    return (renewal_status or "").strip().lower() in RENEWAL_ATTENTION_STATUSES


def evaluate_certificate(
    certificate: Certificate, now: datetime | None = None
) -> CertificateFinding:
    """Evaluate one certificate. Does not modify it."""
    current = normalize_utc(now) if now is not None else datetime.now(timezone.utc)

    if certificate.namespace is None or certificate.cluster is None:
        raise ValueError(
            f"Certificate {certificate.name} must have its namespace and "
            "cluster loaded"
        )

    expires_at = normalize_utc(certificate.expires_at)
    severity, remaining = classify(expires_at, current)
    ingress = certificate.ingress

    return CertificateFinding(
        certificate_id=certificate.id,
        certificate_name=certificate.name,
        namespace=certificate.namespace.name,
        cluster=certificate.cluster.name,
        hostname=certificate.subject,
        issuer=certificate.issuer,
        tls_secret_name=certificate.tls_secret_name,
        ingress_name=ingress.name if ingress is not None else None,
        expires_at=expires_at,
        days_remaining=remaining // timedelta(days=1),
        seconds_remaining=remaining // timedelta(seconds=1),
        severity=severity,
        renewal_status=certificate.renewal_status,
        renewal_attention=needs_renewal_attention(certificate.renewal_status),
    )


def _sort_key(finding: CertificateFinding) -> tuple:
    return (
        SEVERITY_ORDER[finding.severity],
        not finding.renewal_attention,
        finding.expires_at,
        finding.certificate_name,
        finding.certificate_id or 0,
    )


def _severity_filter(
    severity: Severity | Iterable[Severity] | None,
) -> set[str] | None:
    if severity is None:
        return None
    wanted = {severity} if isinstance(severity, str) else set(severity)
    unknown = wanted - SEVERITY_ORDER.keys()
    if unknown:
        raise ValueError(f"Unknown severity: {', '.join(sorted(unknown))}")
    return wanted


def evaluate_certificates(
    db: Session,
    now: datetime | None = None,
    namespace: str | None = None,
    cluster: str | None = None,
    severity: Severity | Iterable[Severity] | None = None,
    include_healthy: bool = True,
) -> list[CertificateFinding]:
    """Evaluate stored certificates, most urgent first.

    Order: severity (expired, critical, warning, healthy), then certificates
    needing renewal attention, then earliest expiration.
    """
    wanted = _severity_filter(severity)
    current = normalize_utc(now) if now is not None else datetime.now(timezone.utc)

    statement = (
        select(Certificate)
        .join(Certificate.namespace)
        .join(Certificate.cluster)
        .outerjoin(Certificate.ingress)
        .options(
            contains_eager(Certificate.namespace),
            contains_eager(Certificate.cluster),
            contains_eager(Certificate.ingress),
        )
    )
    if namespace is not None:
        statement = statement.where(Namespace.name == namespace)
    if cluster is not None:
        statement = statement.where(Cluster.name == cluster)

    # no_autoflush guarantees this read never pushes the caller's pending changes.
    with db.no_autoflush:
        certificates = db.execute(statement).scalars().all()

    findings = [evaluate_certificate(c, current) for c in certificates]

    if wanted is not None:
        findings = [f for f in findings if f.severity in wanted]
    if not include_healthy:
        findings = [f for f in findings if f.severity != "healthy"]

    return sorted(findings, key=_sort_key)


def summarize(findings: Iterable[CertificateFinding]) -> dict[str, int]:
    """Count findings per severity. All four severities are always present."""
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[finding.severity] += 1
    return counts
