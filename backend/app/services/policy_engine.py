"""Deterministic policy evaluation and persisted ticket action decisions."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import PolicyAction, SupportTicket
from .investigation_analysis import InvestigationReport, RecommendedAction, build_report

POLICY_RULE_VERSION = "platformops-policy-v1"
# Intentionally empty: the deterministic analyzer currently proposes no safe
# action eligible for automatic execution.
AUTO_APPROVED_ACTIONS: frozenset[str] = frozenset()


def _collect_refs(node) -> set[str]:
    if isinstance(node, dict):
        refs = set()
        for key, value in node.items():
            if key == "ref" and isinstance(value, str):
                refs.add(value)
            else:
                refs |= _collect_refs(value)
        return refs
    if isinstance(node, list):
        return set().union(*(_collect_refs(item) for item in node)) if node else set()
    return set()


def classify_action(
    action: RecommendedAction,
    report: InvestigationReport,
    auto_approved_actions: frozenset[str] | None = None,
) -> tuple[str, str]:
    """Classify a deterministic recommendation, never an AI-generated action."""
    allowlist = (
        AUTO_APPROVED_ACTIONS
        if auto_approved_actions is None
        else auto_approved_actions
    )
    if action.action in allowlist:
        return (
            "auto_approved",
            f"Action type '{action.action}' is explicitly listed in the safe-action policy.",
        )

    evidence_refs = _collect_refs(report.evidence.model_dump(mode="json"))
    revisions = {
        revision.revision: revision
        for revision in report.evidence.deployment_history.revisions
    }
    previous = revisions.get(action.to_revision)
    current = revisions.get(action.from_revision)
    supported_rollback = (
        action.action == "rollback"
        and report.ticket_key == "TCK-1001"
        and report.evidence.scenario is not None
        and report.evidence.scenario.key == "acme-customer-portal-503"
        and report.evidence.scenario.status == "active"
        and report.evidence.placement is not None
        and report.evidence.placement.cluster_environment == "production"
        and report.evidence.workload is not None
        and report.evidence.workload.name == action.target_workload == "customer-portal"
        and action.namespace == report.evidence.placement.namespace == "acme-prod"
        and action.cluster == report.evidence.placement.cluster == "k8s-prod-east"
        and current is not None
        and current.status == "failed"
        and previous is not None
        and previous.status == "healthy"
        and bool(action.evidence_refs)
        and set(action.evidence_refs) <= evidence_refs
    )
    if supported_rollback:
        return (
            "approval_required",
            "Production rollback can affect customer traffic and requires a platform operator's approval.",
        )
    return (
        "escalation_required",
        "The action is unsupported, inconsistent with the current deterministic evidence, or outside the explicitly supported rollback scenario.",
    )


def action_key(ticket: SupportTicket, action: RecommendedAction) -> str:
    return (
        f"{ticket.ticket_key}:{action.action}:{action.target_workload}:"
        f"{action.from_revision}:{action.to_revision}"
    )


def _risk_and_impact(
    action: RecommendedAction, classification: str
) -> tuple[str, str]:
    if classification == "approval_required":
        return (
            "Production rollback may disrupt active customer sessions and changes the deployed application version.",
            f"Restore {action.target_workload} to revision {action.to_revision}; recovery is expected but must be verified.",
        )
    if classification == "auto_approved":
        return (
            "The action type is explicitly allowlisted as low risk.",
            "Expected impact must be defined by the action-specific policy before execution.",
        )
    return (
        "Risk cannot be safely assessed for an unsupported or insufficiently evidenced action.",
        "Expected impact is unknown; refer the action to engineering.",
    )


def evaluate_ticket_actions(
    db: Session, ticket: SupportTicket, now: datetime | None = None
) -> list[PolicyAction]:
    """Persist each deterministic decision once; repeated evaluation is idempotent."""
    report = build_report(db, ticket, now=now)
    evaluated_at = now or datetime.now(timezone.utc)
    decisions: list[PolicyAction] = []
    for action in report.analysis.recommended_actions:
        key = action_key(ticket, action)
        decision = db.query(PolicyAction).filter_by(action_key=key).one_or_none()
        if decision is None:
            classification, rationale = classify_action(action, report)
            risk, expected_impact = _risk_and_impact(action, classification)
            status = {
                "auto_approved": "authorized",
                "approval_required": "pending_approval",
                "escalation_required": "escalated",
            }[classification]
            decision = PolicyAction(
                action_key=key,
                ticket_id=ticket.id,
                action_type=action.action,
                classification=classification,
                status=status,
                rule_version=POLICY_RULE_VERSION,
                rationale=rationale,
                risk=risk,
                expected_impact=expected_impact,
                target_workload=action.target_workload,
                namespace=action.namespace,
                cluster=action.cluster,
                from_revision=action.from_revision,
                to_revision=action.to_revision,
                evidence_refs=json.dumps(action.evidence_refs),
                evaluated_at=evaluated_at,
            )
            db.add(decision)
            db.flush()
        decisions.append(decision)
    return decisions
