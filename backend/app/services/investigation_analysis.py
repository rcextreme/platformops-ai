"""Deterministic, read-only analysis of gathered investigation evidence.

`analyze()` is a pure function over `InvestigationEvidence`: it performs no
database access and executes nothing. It only reports what the evidence
supports. Every claim cites evidence refs that exist in the evidence, and
confidence is capped at "medium" because logs, metrics, ArgoCD state and
audit history are not available to confirm a cause.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models import SupportTicket
from .investigation import (
    EventFact,
    InvestigationEvidence,
    RevisionFact,
    gather_evidence,
)

CONTEXT_AREAS = ("logs", "argocd", "audit_history", "metrics")


class CausalStep(BaseModel):
    key: str
    description: str
    kind: Literal["observed", "inferred"]
    established: bool
    timestamp: datetime | None = None
    evidence_refs: list[str] = []


class Finding(BaseModel):
    key: str
    title: str
    root_cause: str
    confidence: Literal["low", "medium"]
    summary: str
    evidence_refs: list[str]
    ruled_out: list[str]
    limitations: list[str]


class RecommendedAction(BaseModel):
    action: Literal["rollback"]
    target_workload: str
    namespace: str | None
    cluster: str | None
    from_revision: int
    to_revision: int
    rationale: str
    evidence_refs: list[str]
    status: Literal["proposed"] = "proposed"
    executed: bool = False
    note: str = (
        "Recommendation only. Nothing has been executed; whether it is "
        "permitted is decided separately by the policy engine."
    )


class InvestigationAnalysis(BaseModel):
    outcome: Literal["finding", "inconclusive"]
    causal_sequence: list[CausalStep]
    findings: list[Finding]
    recommended_actions: list[RecommendedAction]
    other_observations: list[str]


class InvestigationReport(BaseModel):
    ticket_id: int
    ticket_key: str
    generated_at: datetime
    evidence: InvestigationEvidence
    analysis: InvestigationAnalysis


def _ago(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def analyze(evidence: InvestigationEvidence) -> InvestigationAnalysis:
    pods = evidence.pods
    history = evidence.deployment_history
    events = evidence.events

    revisions: list[RevisionFact] = history.revisions
    current = revisions[-1] if revisions else None
    previous_healthy = (
        next(
            (r for r in reversed(revisions[:-1]) if r.status == "healthy"), None
        )
        if current is not None
        else None
    )
    current_unhealthy = current is not None and current.status != "healthy"

    unhealthy_pods = [p for p in pods.pods if not p.ready]
    unhealthy_names = {p.name for p in unhealthy_pods}
    readiness_events: list[EventFact] = [
        e
        for e in events.events
        if e.reason == "Unhealthy"
        and e.involved_object in unhealthy_names
        and current is not None
        and e.occurred_at >= current.deployed_at
    ]
    failing_pod_names = sorted({e.involved_object for e in readiness_events})

    steps: list[CausalStep] = []

    step1 = previous_healthy is not None
    steps.append(
        CausalStep(
            key="previous_revision_healthy",
            description=(
                f"Revision {previous_healthy.revision} was healthy "
                f"(deployed {_ago(previous_healthy.deployed_at)})."
                if step1
                else "No earlier healthy revision is recorded."
            ),
            kind="observed",
            established=step1,
            timestamp=previous_healthy.deployed_at if step1 else None,
            evidence_refs=[previous_healthy.ref] if step1 else [],
        )
    )

    step2 = step1 and current is not None and current_unhealthy
    steps.append(
        CausalStep(
            key="new_revision_deployed",
            description=(
                f"Revision {current.revision} ({current.image}) was deployed "
                f"{_ago(current.deployed_at)} with status '{current.status}'."
                if step2
                else "No newer unhealthy revision follows a healthy one."
            ),
            kind="observed",
            established=step2,
            timestamp=current.deployed_at if step2 else None,
            evidence_refs=[current.ref] if step2 else [],
        )
    )

    step3 = bool(readiness_events)
    steps.append(
        CausalStep(
            key="readiness_failures_after_deploy",
            description=(
                f"Readiness probe failures were recorded after the deployment "
                f"for {len(failing_pod_names)} pod(s): "
                f"{', '.join(failing_pod_names)}."
                if step3
                else "No readiness failure events after the deployment were found."
            ),
            kind="observed",
            established=step3,
            timestamp=min(e.occurred_at for e in readiness_events) if step3 else None,
            evidence_refs=(
                [e.ref for e in readiness_events]
                + [f"pod/{n}" for n in failing_pod_names]
                if step3
                else []
            ),
        )
    )

    expected = pods.expected_replicas
    step4 = (
        pods.status == "observed"
        and expected is not None
        and pods.ready_count < expected
    )
    steps.append(
        CausalStep(
            key="reduced_ready_capacity",
            description=(
                f"Only {pods.ready_count} of {expected} replicas are ready; "
                f"{len(unhealthy_pods)} are not ready."
                if step4
                else "Ready replica count matches the expected replica count."
            ),
            kind="observed",
            established=step4,
            evidence_refs=[p.ref for p in pods.pods] if step4 else [],
        )
    )

    all_four = step1 and step2 and step3 and step4
    steps.append(
        CausalStep(
            key="plausible_http_503_source",
            description=(
                "Reduced ready capacity after the new revision is a plausible "
                "source of the intermittent HTTP 503 responses in the ticket. "
                "This is an inference from correlated evidence, not a "
                "confirmed cause."
                if all_four
                else "The evidence does not establish the full sequence."
            ),
            kind="inferred",
            established=all_four,
            evidence_refs=(
                [current.ref] + [p.ref for p in unhealthy_pods] if all_four else []
            ),
        )
    )

    ruled_out: list[str] = []
    ruled_out_refs: list[str] = []
    for svc in evidence.services.services:
        if svc.status == "healthy":
            ruled_out.append(
                f"Service {svc.name} has a recorded status of healthy "
                f"(port {svc.port} to {svc.target_port}); it is not the likely cause."
            )
            ruled_out_refs.append(svc.ref)
    for ing in evidence.ingresses.ingresses:
        if ing.status == "healthy":
            ruled_out.append(
                f"Ingress {ing.name} for {ing.host} has a recorded status of "
                "healthy; it is not the likely cause."
            )
            ruled_out_refs.append(ing.ref)
    for cert in evidence.certificates.certificates:
        if cert.severity != "expired":
            ruled_out.append(
                f"TLS certificate {cert.certificate_name} is not expired "
                f"({cert.days_remaining} days remaining)."
            )

    other: list[str] = []
    for cert in evidence.certificates.certificates:
        if cert.severity in ("critical", "warning") or cert.renewal_attention:
            other.append(
                f"Certificate {cert.certificate_name} expires in "
                f"{cert.days_remaining} days (renewal status: "
                f"{cert.renewal_status}). This is a separate risk and is "
                "unrelated to the HTTP 503 symptom."
            )

    limitations = [
        f"No {area.replace('_', ' ')} evidence is available."
        for area in CONTEXT_AREAS
        if any(m.area == area for m in evidence.missing_evidence)
    ]
    limitations.append(
        "Service and ingress health are recorded statuses, not live probes."
    )
    limitations.append(
        "The finding rests on correlation in time and does not prove causation."
    )

    findings: list[Finding] = []
    actions: list[RecommendedAction] = []

    if step2 and step4:
        confidence = "medium" if all_four else "low"
        evidence_refs = [current.ref, previous_healthy.ref]
        evidence_refs += [e.ref for e in readiness_events]
        evidence_refs += [p.ref for p in unhealthy_pods]
        evidence_refs += ruled_out_refs
        findings.append(
            Finding(
                key="deployment_regression",
                title=(
                    f"Recent deployment (revision {current.revision}) is "
                    "associated with unhealthy replicas"
                ),
                root_cause=(
                    "A recent deployment is associated with unhealthy "
                    "application replicas."
                ),
                confidence=confidence,
                summary=(
                    f"Revision {previous_healthy.revision} was healthy. After "
                    f"revision {current.revision} was deployed, "
                    f"{len(unhealthy_pods)} of {expected} replicas stopped "
                    f"being ready, leaving {pods.ready_count} ready. This is "
                    "a plausible source of the HTTP 503 errors."
                    + (
                        ""
                        if all_four
                        else " Readiness failure events were not found, so "
                        "confidence is low."
                    )
                ),
                evidence_refs=list(dict.fromkeys(evidence_refs)),
                ruled_out=ruled_out,
                limitations=limitations,
            )
        )
        actions.append(
            RecommendedAction(
                action="rollback",
                target_workload=evidence.workload.name,
                namespace=evidence.placement.namespace if evidence.placement else None,
                cluster=evidence.placement.cluster if evidence.placement else None,
                from_revision=current.revision,
                to_revision=previous_healthy.revision,
                rationale=(
                    f"Revision {previous_healthy.revision} is the last known "
                    f"healthy revision before revision {current.revision}."
                ),
                evidence_refs=[current.ref, previous_healthy.ref]
                + [p.ref for p in unhealthy_pods],
            )
        )

    return InvestigationAnalysis(
        outcome="finding" if findings else "inconclusive",
        causal_sequence=steps,
        findings=findings,
        recommended_actions=actions,
        other_observations=other,
    )


def build_report(
    db: Session, ticket: SupportTicket, now: datetime | None = None
) -> InvestigationReport:
    current = now if now is not None else datetime.now(timezone.utc)
    evidence = gather_evidence(db, ticket, now=current)
    return InvestigationReport(
        ticket_id=ticket.id,
        ticket_key=ticket.ticket_key,
        generated_at=current,
        evidence=evidence,
        analysis=analyze(evidence),
    )
