from datetime import datetime

from pydantic import BaseModel

from .services.certificate_expiry import CertificateFinding


class TicketResponse(BaseModel):
    id: int
    ticket_key: str
    title: str
    description: str
    severity: str | None
    status: str | None
    created_at: datetime | None
    tenant_id: int
    tenant: str
    workload_id: int | None
    workload: str | None
    namespace: str | None
    cluster: str | None


class TicketListResponse(BaseModel):
    count: int
    tickets: list[TicketResponse]



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
