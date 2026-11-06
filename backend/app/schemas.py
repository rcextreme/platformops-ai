from datetime import datetime

from pydantic import BaseModel

from .services.certificate_expiry import CertificateFinding


class SeverityCounts(BaseModel):
    expired: int
    critical: int
    warning: int
    healthy: int


class CertificateListResponse(BaseModel):
    generated_at: datetime
    count: int
    findings: list[CertificateFinding]


class CertificateSummaryResponse(BaseModel):
    generated_at: datetime
    total: int
    counts: SeverityCounts
    renewal_attention: int
    warning_days: int
    critical_days: int
