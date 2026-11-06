from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    operator: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1)

    @field_validator("operator", "reason")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ApprovalResponse(BaseModel):
    decision: Literal["approve", "reject"]
    operator: str
    reason: str
    decided_at: datetime
    identity_notice: str = (
        "Operator identity is user-supplied for demo purposes; this endpoint "
        "does not authenticate or authorize users."
    )


class VerificationResponse(BaseModel):
    passed: bool
    checks: dict
    verified_at: datetime


class ExecutionResponse(BaseModel):
    outcome: str
    previous_state: dict
    resulting_state: dict
    started_at: datetime
    completed_at: datetime
    verification: VerificationResponse | None
    idempotent_replay: bool = False


class PolicyActionResponse(BaseModel):
    id: int
    ticket_id: int
    tenant: str
    action_type: str
    classification: Literal[
        "auto_approved", "approval_required", "escalation_required"
    ]
    status: str
    rule_version: str
    rationale: str
    risk: str
    expected_impact: str
    target_workload: str
    namespace: str | None
    cluster: str | None
    from_revision: int
    to_revision: int
    evidence_refs: list[str]
    evaluated_at: datetime
    approval: ApprovalResponse | None = None
    execution: ExecutionResponse | None = None


class PolicyActionListResponse(BaseModel):
    ticket_id: int
    count: int
    actions: list[PolicyActionResponse]
