from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from . import models
from .schemas import (
    ApprovalRequest,
    ApprovalResponse,
    CertificateListResponse,
    CertificateSummaryResponse,
    ExecutionResponse,
    PolicyActionListResponse,
    PolicyActionResponse,
    SeverityCounts,
    TicketListResponse,
    TicketResponse,
    VerificationResponse,
)
from .seed import seed_database
from .services.certificate_expiry import (
    CRITICAL_DAYS,
    WARNING_DAYS,
    Severity,
    evaluate_certificates,
    summarize,
)
from .services.investigation import InvestigationEvidence, gather_evidence
from .services.investigation_analysis import InvestigationReport, build_report
from .services.ai_assessment import (
    AIAssessmentResponse,
    AIUnavailableResponse,
    InvalidModelOutputError,
    generate_assessment,
)
from .services.llm_provider import LLMProvider, LLMUnavailableError, get_llm_provider
from .models import ApprovalDecision, PolicyAction, RemediationExecution
from .services.policy_engine import evaluate_ticket_actions
from .services.remediation import WorkflowConflict, execute_simulated_action
import json


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed_database(db)

    yield


app = FastAPI(
    title="PlatformOps AI",
    description="Autonomous Platform Operations Center",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "platformops-api"}


@app.get("/api/platform/overview")
def platform_overview(db: Session = Depends(get_db)):
    tenants = db.query(models.Tenant).all()
    clusters = db.query(models.Cluster).all()
    workloads = db.query(models.Workload).all()

    return {
        "summary": {
            "tenant_count": len(tenants),
            "cluster_count": len(clusters),
            "workload_count": len(workloads),
            "healthy_workload_count": sum(
                1 for w in workloads if w.status == "healthy"
            ),
        },
        "tenants": [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "status": t.status,
            }
            for t in tenants
        ],
        "clusters": [
            {
                "id": c.id,
                "name": c.name,
                "environment": c.environment,
                "region": c.region,
                "version": c.version,
                "status": c.status,
                "provider": c.provider,
            }
            for c in clusters
        ],
        "workloads": [
            {
                "id": w.id,
                "name": w.name,
                "namespace": w.namespace,
                "tenant": w.tenant.name if w.tenant else None,
                "cluster": db.get(models.Cluster, w.cluster_id).name
                if w.cluster_id else None,
                "desired_replicas": w.desired_replicas,
                "ready_replicas": w.ready_replicas,
                "status": w.status,
            }
            for w in workloads
        ],
    }


@app.get("/api/platform/certificates", response_model=CertificateListResponse)
def list_certificates(
    namespace: str | None = None,
    cluster: str | None = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    include_healthy: bool = True,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    findings = evaluate_certificates(
        db,
        now=now,
        namespace=namespace,
        cluster=cluster,
        severity=severity,
        include_healthy=include_healthy,
    )

    return CertificateListResponse(
        generated_at=now,
        count=len(findings),
        findings=findings,
    )


@app.get(
    "/api/platform/certificates/summary",
    response_model=CertificateSummaryResponse,
)
def certificate_summary(
    namespace: str | None = None,
    cluster: str | None = None,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    findings = evaluate_certificates(
        db, now=now, namespace=namespace, cluster=cluster
    )

    return CertificateSummaryResponse(
        generated_at=now,
        total=len(findings),
        counts=SeverityCounts(**summarize(findings)),
        renewal_attention=sum(1 for f in findings if f.renewal_attention),
        warning_days=WARNING_DAYS,
        critical_days=CRITICAL_DAYS,
    )


def _ticket_response(db: Session, ticket: models.SupportTicket) -> TicketResponse:
    workload = ticket.workload
    cluster = (
        db.get(models.Cluster, workload.cluster_id)
        if workload is not None and workload.cluster_id
        else None
    )
    return TicketResponse(
        id=ticket.id,
        ticket_key=ticket.ticket_key,
        title=ticket.title,
        description=ticket.description,
        severity=ticket.severity,
        status=ticket.status,
        created_at=ticket.created_at,
        tenant_id=ticket.tenant_id,
        tenant=ticket.tenant.name,
        workload_id=ticket.workload_id,
        workload=workload.name if workload else None,
        namespace=workload.namespace if workload else None,
        cluster=cluster.name if cluster else None,
    )


def _get_ticket_or_404(db: Session, ticket_id: int) -> models.SupportTicket:
    ticket = db.get(models.SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.get("/api/platform/tickets", response_model=TicketListResponse)
def list_tickets(
    status: str | None = None,
    tenant: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.SupportTicket).join(models.SupportTicket.tenant)
    if status is not None:
        query = query.filter(models.SupportTicket.status == status)
    if tenant is not None:
        query = query.filter(models.Tenant.slug == tenant)
    tickets = query.order_by(
        models.SupportTicket.created_at.desc(), models.SupportTicket.id.desc()
    ).all()

    return TicketListResponse(
        count=len(tickets),
        tickets=[_ticket_response(db, t) for t in tickets],
    )


@app.get("/api/platform/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    return _ticket_response(db, _get_ticket_or_404(db, ticket_id))


@app.get(
    "/api/platform/tickets/{ticket_id}/investigation",
    response_model=InvestigationEvidence,
)
def get_ticket_investigation(ticket_id: int, db: Session = Depends(get_db)):
    ticket = _get_ticket_or_404(db, ticket_id)
    return gather_evidence(db, ticket)


@app.get(
    "/api/platform/tickets/{ticket_id}/investigation/analysis",
    response_model=InvestigationReport,
)
def get_ticket_investigation_analysis(ticket_id: int, db: Session = Depends(get_db)):
    ticket = _get_ticket_or_404(db, ticket_id)
    return build_report(db, ticket)


@app.get(
    "/api/platform/tickets/{ticket_id}/investigation/ai",
    response_model=AIAssessmentResponse,
    responses={
        502: {"model": AIUnavailableResponse},
        503: {"model": AIUnavailableResponse},
    },
)
def get_ticket_investigation_ai(
    ticket_id: int,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    report = build_report(db, ticket)
    try:
        return generate_assessment(report, provider)
    except LLMUnavailableError as exc:
        status_code, status, reason = 503, "provider_unavailable", exc.reason
    except InvalidModelOutputError as exc:
        status_code, status, reason = 502, "provider_invalid_response", str(exc)
    return JSONResponse(
        status_code=status_code,
        content=AIUnavailableResponse(
            status=status,
            ticket_id=report.ticket_id,
            ticket_key=report.ticket_key,
            reason=reason,
        ).model_dump(),
    )


def _action_response(action: PolicyAction) -> PolicyActionResponse:
    approval = action.approval
    execution = action.execution
    verification = execution.verification if execution is not None else None
    return PolicyActionResponse(
        id=action.id,
        ticket_id=action.ticket_id,
        tenant=action.ticket.tenant.name,
        action_type=action.action_type,
        classification=action.classification,
        status=action.status,
        rule_version=action.rule_version,
        rationale=action.rationale,
        risk=action.risk,
        expected_impact=action.expected_impact,
        target_workload=action.target_workload,
        namespace=action.namespace,
        cluster=action.cluster,
        from_revision=action.from_revision,
        to_revision=action.to_revision,
        evidence_refs=json.loads(action.evidence_refs),
        evaluated_at=action.evaluated_at,
        approval=(
            ApprovalResponse(
                decision=approval.decision,
                operator=approval.operator,
                reason=approval.reason,
                decided_at=approval.decided_at,
            )
            if approval is not None
            else None
        ),
        execution=(
            ExecutionResponse(
                outcome=execution.outcome,
                previous_state=json.loads(execution.previous_state),
                resulting_state=json.loads(execution.resulting_state),
                started_at=execution.started_at,
                completed_at=execution.completed_at,
                verification=(
                    VerificationResponse(
                        passed=verification.passed,
                        checks=json.loads(verification.checks),
                        verified_at=verification.verified_at,
                    )
                    if verification is not None
                    else None
                ),
            )
            if execution is not None
            else None
        ),
    )


@app.post(
    "/api/platform/tickets/{ticket_id}/actions/evaluate",
    response_model=PolicyActionListResponse,
)
def evaluate_ticket_policy(ticket_id: int, db: Session = Depends(get_db)):
    ticket = _get_ticket_or_404(db, ticket_id)
    actions = evaluate_ticket_actions(db, ticket)
    db.commit()
    return PolicyActionListResponse(
        ticket_id=ticket.id,
        count=len(actions),
        actions=[_action_response(action) for action in actions],
    )


@app.get(
    "/api/platform/tickets/{ticket_id}/actions",
    response_model=PolicyActionListResponse,
)
def list_ticket_actions(ticket_id: int, db: Session = Depends(get_db)):
    ticket = _get_ticket_or_404(db, ticket_id)
    actions = (
        db.query(PolicyAction)
        .filter_by(ticket_id=ticket.id)
        .order_by(PolicyAction.evaluated_at, PolicyAction.id)
        .all()
    )
    return PolicyActionListResponse(
        ticket_id=ticket.id,
        count=len(actions),
        actions=[_action_response(action) for action in actions],
    )


@app.get(
    "/api/platform/actions/{action_id}",
    response_model=PolicyActionResponse,
)
def get_action_history(action_id: int, db: Session = Depends(get_db)):
    action = db.get(PolicyAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return _action_response(action)


@app.post(
    "/api/platform/actions/{action_id}/approval",
    response_model=ApprovalResponse,
)
def decide_action_approval(
    action_id: int,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
):
    action = db.get(PolicyAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    existing = (
        db.query(ApprovalDecision)
        .filter_by(policy_action_id=action.id)
        .one_or_none()
    )
    if existing is not None:
        if existing.decision != request.decision:
            raise HTTPException(status_code=409, detail="A conflicting final decision already exists")
        return ApprovalResponse(
            decision=existing.decision,
            operator=existing.operator,
            reason=existing.reason,
            decided_at=existing.decided_at,
        )
    if action.classification != "approval_required" or action.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Action is not awaiting human approval")
    decision = ApprovalDecision(
        policy_action_id=action.id,
        decision=request.decision,
        operator=request.operator,
        reason=request.reason,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    action.status = "approved" if request.decision == "approve" else "rejected"
    db.commit()
    db.refresh(decision)
    return ApprovalResponse(
        decision=decision.decision,
        operator=decision.operator,
        reason=decision.reason,
        decided_at=decision.decided_at,
    )


@app.post(
    "/api/platform/actions/{action_id}/execute",
    response_model=ExecutionResponse,
)
def execute_action(action_id: int, db: Session = Depends(get_db)):
    action = db.get(PolicyAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    existing = (
        db.query(RemediationExecution)
        .filter_by(policy_action_id=action.id)
        .one_or_none()
    )
    try:
        execution = execute_simulated_action(db, action)
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(execution)
    verification = execution.verification
    return ExecutionResponse(
        outcome=execution.outcome,
        previous_state=json.loads(execution.previous_state),
        resulting_state=json.loads(execution.resulting_state),
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        verification=(
            VerificationResponse(
                passed=verification.passed,
                checks=json.loads(verification.checks),
                verified_at=verification.verified_at,
            )
            if verification is not None
            else None
        ),
        idempotent_replay=existing is not None,
    )
