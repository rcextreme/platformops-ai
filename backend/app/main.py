from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from . import models
from .schemas import (
    CertificateListResponse,
    CertificateSummaryResponse,
    SeverityCounts,
    TicketListResponse,
    TicketResponse,
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
