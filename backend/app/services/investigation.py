"""Read-only evidence gathering for tenant support tickets.

This layer only reports what the database contains. It makes no diagnosis:
every area is marked "observed", "missing" or "not_applicable", and anything
the platform does not track yet is listed in `missing_evidence`.

Nothing here adds, flushes, commits, or modifies ORM records.
"""

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import (
    Cluster,
    DeploymentRevision,
    IncidentScenario,
    Ingress,
    Namespace,
    PlatformEvent,
    Pod,
    ScenarioPodState,
    Service,
    SupportTicket,
    Workload,
)
from .certificate_expiry import (
    CertificateFinding,
    evaluate_certificate,
    normalize_utc,
)

EvidenceStatus = Literal["observed", "missing", "not_applicable"]

# Evidence the platform does not store yet. Reported on every investigation so
# the reasoning layer never mistakes "not collected" for "healthy".
NOT_COLLECTED = {
    "logs": "Application logs are not recorded yet.",
    "argocd": "ArgoCD application state is not recorded yet.",
    "audit_history": "Audit history is not recorded yet.",
    "metrics": "Metrics are not recorded yet.",
}


class MissingEvidence(BaseModel):
    area: str
    reason: str


class TenantEvidence(BaseModel):
    id: int
    name: str
    slug: str
    status: str | None


class WorkloadEvidence(BaseModel):
    ref: str
    id: int
    name: str
    namespace: str
    desired_replicas: int | None
    ready_replicas: int | None
    status: str | None


class PlacementEvidence(BaseModel):
    cluster: str | None
    cluster_environment: str | None
    cluster_status: str | None
    namespace: str | None
    namespace_status: str | None


class ScenarioEvidence(BaseModel):
    key: str
    name: str
    description: str
    status: str | None
    simulated: bool = True
    ref: str


class PodFact(BaseModel):
    ref: str
    name: str
    # Effective values: the scenario override when one applies, else baseline.
    status: str | None
    ready_containers: int | None
    desired_containers: int | None
    ready: bool
    restart_count: int | None
    node: str | None
    pod_ip: str | None
    source: Literal["baseline", "scenario_override"] = "baseline"
    health_reason: str | None = None
    baseline_status: str | None = None
    baseline_ready: bool = True
    baseline_restart_count: int | None = None


class PodsEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    expected_replicas: int | None = None
    observed_count: int = 0
    ready_count: int = 0
    baseline_ready_count: int = 0
    total_restarts: int = 0
    pods: list[PodFact] = []


class EventFact(BaseModel):
    ref: str
    event_type: str | None
    reason: str
    message: str
    source_component: str | None
    involved_object: str
    count: int | None
    occurred_at: datetime
    scenario_key: str | None


class EventsEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    events: list[EventFact] = []


class RevisionFact(BaseModel):
    ref: str
    revision: int
    image: str
    status: str | None
    deployed_at: datetime
    change_cause: str | None
    is_current: bool
    scenario_key: str | None


class DeploymentHistoryEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    current_revision: int | None = None
    last_known_healthy_revision: int | None = None
    revisions: list[RevisionFact] = []


class ServiceFact(BaseModel):
    ref: str
    name: str
    service_type: str | None
    port: int
    target_port: int
    protocol: str | None
    status: str | None
    cluster_ip: str | None


class ServicesEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    services: list[ServiceFact] = []


class IngressFact(BaseModel):
    ref: str
    name: str
    service: str
    host: str
    path: str | None
    path_type: str | None
    ingress_class: str | None
    tls_enabled: bool
    tls_secret_name: str | None
    status: str | None


class IngressesEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    ingresses: list[IngressFact] = []


class CertificatesEvidence(BaseModel):
    status: EvidenceStatus
    reason: str | None = None
    certificates: list[CertificateFinding] = []


class InvestigationEvidence(BaseModel):
    ticket_id: int
    ticket_key: str
    ticket_title: str
    generated_at: datetime
    tenant: TenantEvidence
    scenario: ScenarioEvidence | None
    workload: WorkloadEvidence | None
    placement: PlacementEvidence | None
    pods: PodsEvidence
    services: ServicesEvidence
    ingresses: IngressesEvidence
    certificates: CertificatesEvidence
    events: EventsEvidence
    deployment_history: DeploymentHistoryEvidence
    missing_evidence: list[MissingEvidence]


def _pod_belongs_to(workload_name: str, pod_name: str) -> bool:
    # Deployment pods are named <workload>-<replicaset-hash>-<suffix>.
    return re.fullmatch(rf"{re.escape(workload_name)}-[a-z0-9]+-[a-z0-9]+", pod_name) is not None


def _collect_pods(
    db: Session,
    workload: Workload,
    namespace: Namespace | None,
    overrides: dict[int, ScenarioPodState],
) -> PodsEvidence:
    if namespace is None:
        return PodsEvidence(
            status="missing",
            reason=f"Namespace {workload.namespace} was not found for this workload.",
            expected_replicas=workload.desired_replicas,
        )

    candidates = (
        db.query(Pod)
        .filter(Pod.namespace_id == namespace.id)
        .order_by(Pod.name)
        .all()
    )
    pods = [p for p in candidates if _pod_belongs_to(workload.name, p.name)]
    if not pods:
        return PodsEvidence(
            status="missing",
            reason=f"No pods were found for workload {workload.name}.",
            expected_replicas=workload.desired_replicas,
        )

    def is_ready(status, ready_containers, desired_containers):
        return (
            status == "Running"
            and desired_containers is not None
            and ready_containers == desired_containers
        )

    facts = []
    for pod in pods:
        override = overrides.get(pod.id)
        status = override.status if override else pod.status
        ready_containers = override.ready_containers if override else pod.ready_containers
        restarts = override.restart_count if override else pod.restart_count
        facts.append(
            PodFact(
                ref=f"pod/{pod.name}",
                name=pod.name,
                status=status,
                ready_containers=ready_containers,
                desired_containers=pod.desired_containers,
                ready=is_ready(status, ready_containers, pod.desired_containers),
                restart_count=restarts,
                node=pod.node.name if pod.node is not None else None,
                pod_ip=pod.pod_ip,
                source="scenario_override" if override else "baseline",
                health_reason=override.reason if override else None,
                baseline_status=pod.status,
                baseline_ready=is_ready(
                    pod.status, pod.ready_containers, pod.desired_containers
                ),
                baseline_restart_count=pod.restart_count,
            )
        )

    return PodsEvidence(
        status="observed",
        expected_replicas=workload.desired_replicas,
        observed_count=len(facts),
        ready_count=sum(1 for f in facts if f.ready),
        baseline_ready_count=sum(1 for f in facts if f.baseline_ready),
        total_restarts=sum(f.restart_count or 0 for f in facts),
        pods=facts,
    )


def _collect_services(workload: Workload, services: list[Service]) -> ServicesEvidence:
    if not services:
        return ServicesEvidence(
            status="missing",
            reason=f"No services are defined for workload {workload.name}.",
        )
    return ServicesEvidence(
        status="observed",
        services=[
            ServiceFact(
                ref=f"service/{s.name}",
                name=s.name,
                service_type=s.service_type,
                port=s.port,
                target_port=s.target_port,
                protocol=s.protocol,
                status=s.status,
                cluster_ip=s.cluster_ip,
            )
            for s in services
        ],
    )


def _collect_ingresses(services: list[Service]) -> tuple[IngressesEvidence, list[Ingress]]:
    ingresses = sorted(
        (i for s in services for i in s.ingresses), key=lambda i: i.name
    )
    if not ingresses:
        return (
            IngressesEvidence(
                status="missing", reason="No ingress routes to the workload's services."
            ),
            [],
        )
    return (
        IngressesEvidence(
            status="observed",
            ingresses=[
                IngressFact(
                    ref=f"ingress/{i.name}",
                    name=i.name,
                    service=i.service.name,
                    host=i.host,
                    path=i.path,
                    path_type=i.path_type,
                    ingress_class=i.ingress_class,
                    tls_enabled=bool(i.tls_enabled),
                    tls_secret_name=i.tls_secret_name,
                    status=i.status,
                )
                for i in ingresses
            ],
        ),
        ingresses,
    )


def _collect_certificates(
    ingresses: list[Ingress], now: datetime
) -> CertificatesEvidence:
    tls_ingresses = [i for i in ingresses if i.tls_enabled]
    if not tls_ingresses:
        return CertificatesEvidence(
            status="not_applicable", reason="No ingress has TLS enabled."
        )

    certificates = sorted(
        (c for i in tls_ingresses for c in i.certificates),
        key=lambda c: c.name,
    )
    if not certificates:
        return CertificatesEvidence(
            status="missing",
            reason="TLS is enabled but no certificate is recorded for it.",
        )
    return CertificatesEvidence(
        status="observed",
        certificates=[evaluate_certificate(c, now) for c in certificates],
    )


def _find_active_scenario(
    db: Session, ticket: SupportTicket, workload: Workload
) -> IncidentScenario | None:
    return (
        db.query(IncidentScenario)
        .filter_by(ticket_id=ticket.id, workload_id=workload.id, status="active")
        .order_by(IncidentScenario.id)
        .first()
    )


def _scoped_to(model, scenario: IncidentScenario | None):
    """Rows with no scenario, plus rows belonging to the active scenario only."""
    if scenario is None:
        return model.scenario_id.is_(None)
    return or_(model.scenario_id.is_(None), model.scenario_id == scenario.id)


def _collect_events(
    db: Session, workload: Workload, scenario: IncidentScenario | None
) -> EventsEvidence:
    rows = (
        db.query(PlatformEvent)
        .filter(
            PlatformEvent.workload_id == workload.id,
            _scoped_to(PlatformEvent, scenario),
        )
        .order_by(PlatformEvent.occurred_at, PlatformEvent.id)
        .all()
    )
    if not rows:
        return EventsEvidence(
            status="missing",
            reason=f"No platform events are recorded for workload {workload.name}.",
        )
    return EventsEvidence(
        status="observed",
        events=[
            EventFact(
                ref=f"event/{e.event_key}",
                event_type=e.event_type,
                reason=e.reason,
                message=e.message,
                source_component=e.source_component,
                involved_object=e.pod.name if e.pod is not None else workload.name,
                count=e.count,
                occurred_at=normalize_utc(e.occurred_at),
                scenario_key=e.scenario.key if e.scenario is not None else None,
            )
            for e in rows
        ],
    )


def _collect_deployment_history(
    db: Session, workload: Workload, scenario: IncidentScenario | None
) -> DeploymentHistoryEvidence:
    rows = (
        db.query(DeploymentRevision)
        .filter(
            DeploymentRevision.workload_id == workload.id,
            _scoped_to(DeploymentRevision, scenario),
        )
        .order_by(DeploymentRevision.deployed_at, DeploymentRevision.revision)
        .all()
    )
    if not rows:
        return DeploymentHistoryEvidence(
            status="missing",
            reason=f"No deployment revisions are recorded for workload {workload.name}.",
        )
    healthy = [r for r in rows if r.status == "healthy"]
    return DeploymentHistoryEvidence(
        status="observed",
        current_revision=rows[-1].revision,
        last_known_healthy_revision=healthy[-1].revision if healthy else None,
        revisions=[
            RevisionFact(
                ref=f"revision/{r.revision}",
                revision=r.revision,
                image=r.image,
                status=r.status,
                deployed_at=normalize_utc(r.deployed_at),
                change_cause=r.change_cause,
                is_current=r is rows[-1],
                scenario_key=r.scenario.key if r.scenario is not None else None,
            )
            for r in rows
        ],
    )


def gather_evidence(
    db: Session, ticket: SupportTicket, now: datetime | None = None
) -> InvestigationEvidence:
    """Collect structured, read-only evidence for a ticket."""
    current = now if now is not None else datetime.now(timezone.utc)
    missing = [MissingEvidence(area=a, reason=r) for a, r in NOT_COLLECTED.items()]

    with db.no_autoflush:
        tenant = ticket.tenant
        tenant_evidence = TenantEvidence(
            id=tenant.id, name=tenant.name, slug=tenant.slug, status=tenant.status
        )
        header = dict(
            ticket_id=ticket.id,
            ticket_key=ticket.ticket_key,
            ticket_title=ticket.title,
            generated_at=current,
            tenant=tenant_evidence,
        )

        workload = ticket.workload
        if workload is None:
            reason = "The ticket is not linked to a workload."
            missing = [
                MissingEvidence(area=a, reason=reason)
                for a in ("workload", "events", "deployment_history")
            ] + missing
            return InvestigationEvidence(
                **header,
                scenario=None,
                workload=None,
                placement=None,
                pods=PodsEvidence(status="missing", reason=reason),
                services=ServicesEvidence(status="missing", reason=reason),
                ingresses=IngressesEvidence(status="missing", reason=reason),
                certificates=CertificatesEvidence(status="missing", reason=reason),
                events=EventsEvidence(status="missing", reason=reason),
                deployment_history=DeploymentHistoryEvidence(
                    status="missing", reason=reason
                ),
                missing_evidence=missing,
            )

        cluster = db.get(Cluster, workload.cluster_id) if workload.cluster_id else None
        namespace = None
        if cluster is not None:
            namespace = (
                db.query(Namespace)
                .filter_by(name=workload.namespace, cluster_id=cluster.id)
                .first()
            )

        placement = PlacementEvidence(
            cluster=cluster.name if cluster else None,
            cluster_environment=cluster.environment if cluster else None,
            cluster_status=cluster.status if cluster else None,
            namespace=namespace.name if namespace else None,
            namespace_status=namespace.status if namespace else None,
        )

        scenario = _find_active_scenario(db, ticket, workload)
        scenario_evidence = (
            ScenarioEvidence(
                key=scenario.key,
                name=scenario.name,
                description=scenario.description,
                status=scenario.status,
                ref=f"scenario/{scenario.key}",
            )
            if scenario is not None
            else None
        )
        overrides = (
            {s.pod_id: s for s in scenario.pod_states} if scenario is not None else {}
        )

        pods = _collect_pods(db, workload, namespace, overrides)
        services = sorted(workload.services, key=lambda s: s.name)
        services_evidence = _collect_services(workload, services)
        ingresses_evidence, ingresses = _collect_ingresses(services)
        certificates_evidence = _collect_certificates(ingresses, current)
        events_evidence = _collect_events(db, workload, scenario)
        history_evidence = _collect_deployment_history(db, workload, scenario)

        for area, section in (
            ("pods", pods),
            ("services", services_evidence),
            ("ingresses", ingresses_evidence),
            ("certificates", certificates_evidence),
            ("events", events_evidence),
            ("deployment_history", history_evidence),
        ):
            if section.status == "missing":
                missing.insert(0, MissingEvidence(area=area, reason=section.reason or ""))
        if cluster is None or namespace is None:
            missing.insert(
                0,
                MissingEvidence(
                    area="placement",
                    reason="The workload's cluster or namespace record was not found.",
                ),
            )

        return InvestigationEvidence(
            **header,
            scenario=scenario_evidence,
            workload=WorkloadEvidence(
                ref=f"workload/{workload.name}",
                id=workload.id,
                name=workload.name,
                namespace=workload.namespace,
                desired_replicas=workload.desired_replicas,
                ready_replicas=workload.ready_replicas,
                status=workload.status,
            ),
            placement=placement,
            pods=pods,
            services=services_evidence,
            ingresses=ingresses_evidence,
            certificates=certificates_evidence,
            events=events_evidence,
            deployment_history=history_evidence,
            missing_evidence=missing,
        )
