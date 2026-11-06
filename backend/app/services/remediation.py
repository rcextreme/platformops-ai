"""Controlled simulated rollback and post-execution verification."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    IncidentScenario,
    Namespace,
    Pod,
    PolicyAction,
    RemediationExecution,
    ScenarioPodState,
    SupportTicket,
    VerificationResult,
    Workload,
)
from .investigation import gather_evidence


class WorkflowConflict(Exception):
    """The requested workflow transition is not permitted in the current state."""


def _baseline_state(db: Session, ticket: SupportTicket) -> dict:
    report = gather_evidence(db, ticket)
    workload = db.get(Workload, ticket.workload_id)
    namespace = (
        db.query(Namespace)
        .filter_by(name=workload.namespace, cluster_id=workload.cluster_id)
        .one()
    )
    pod_names = {pod.name for pod in report.pods.pods}

    def row_values(row):
        return json.loads(
            json.dumps(
                {
                    column.name: getattr(row, column.name)
                    for column in row.__table__.columns
                },
                default=str,
            )
        )

    return {
        "workload": row_values(workload),
        "pods": [
            row_values(pod)
            for pod in db.query(Pod)
            .filter(Pod.namespace_id == namespace.id, Pod.name.in_(pod_names))
            .order_by(Pod.id)
        ],
    }


def execute_simulated_action(
    db: Session, action: PolicyAction, now: datetime | None = None
) -> RemediationExecution:
    """Execute the one supported simulated rollback after explicit authorization."""
    existing = (
        db.query(RemediationExecution)
        .filter_by(policy_action_id=action.id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    authorized = action.status == "authorized" and action.classification == "auto_approved"
    approved = (
        action.status == "approved"
        and action.classification == "approval_required"
        and action.approval is not None
        and action.approval.decision == "approve"
    )
    if not (authorized or approved):
        raise WorkflowConflict("Action has no explicit policy authorization.")
    if action.action_type != "rollback" or action.classification != "approval_required":
        raise WorkflowConflict("No simulated executor exists for this action.")
    if (
        action.ticket.ticket_key != "TCK-1001"
        or action.target_workload != "customer-portal"
        or action.namespace != "acme-prod"
        or action.cluster != "k8s-prod-east"
        or (action.from_revision, action.to_revision) != (42, 41)
    ):
        raise WorkflowConflict("Action is outside the supported ACME rollback scope.")

    scenario = (
        db.query(IncidentScenario)
        .filter_by(
            ticket_id=action.ticket_id,
            workload_id=action.ticket.workload_id,
            key="acme-customer-portal-503",
            status="active",
        )
        .one_or_none()
    )
    if scenario is None:
        raise WorkflowConflict("The ACME incident scenario is not active.")

    overrides = (
        db.query(ScenarioPodState)
        .filter_by(scenario_id=scenario.id)
        .order_by(ScenarioPodState.id)
        .all()
    )
    before_report = gather_evidence(db, action.ticket)
    if (
        before_report.deployment_history.current_revision != 42
        or before_report.deployment_history.last_known_healthy_revision != 41
        or before_report.pods.ready_count != 1
        or len(overrides) != 2
    ):
        raise WorkflowConflict("Current simulated state does not match the authorized rollback preconditions.")

    started_at = now or datetime.now(timezone.utc)
    previous_state = {
        "scenario_status": scenario.status,
        "effective_revision": before_report.deployment_history.current_revision,
        "baseline": _baseline_state(db, action.ticket),
        "scenario_pod_overrides": [
            {
                "pod_id": row.pod_id,
                "status": row.status,
                "ready_containers": row.ready_containers,
                "restart_count": row.restart_count,
                "reason": row.reason,
            }
            for row in overrides
        ],
    }
    execution = RemediationExecution(
        policy_action_id=action.id,
        outcome="running",
        previous_state=json.dumps(previous_state),
        resulting_state="{}",
        started_at=started_at,
        completed_at=started_at,
    )
    db.add(execution)
    db.flush()

    # The simulation removes the failed rollout's overlay. Baseline pods and
    # workload are never mutated; resolved scenarios expose that baseline state.
    for override in overrides:
        db.delete(override)
    scenario.status = "resolved"
    db.flush()

    after_report = gather_evidence(db, action.ticket)
    remaining_overrides = (
        db.query(ScenarioPodState).filter_by(scenario_id=scenario.id).count()
    )
    attempted_state = {
        "scenario_status": scenario.status,
        "effective_revision": after_report.deployment_history.current_revision,
        "ready_replicas": after_report.pods.ready_count,
        "expected_replicas": after_report.pods.expected_replicas,
        "scenario_pod_overrides": remaining_overrides,
        "baseline": _baseline_state(db, action.ticket),
    }
    checks = {
        "effective_revision_41": (
            after_report.deployment_history.current_revision == 41
        ),
        "replicas_3_of_3": (
            after_report.pods.expected_replicas == 3
            and after_report.pods.observed_count == 3
            and after_report.pods.ready_count == 3
        ),
        "failed_revision_overrides_removed": remaining_overrides == 0,
        "baseline_unchanged": attempted_state["baseline"] == previous_state["baseline"],
    }
    passed = all(checks.values())

    if not passed:
        # A failed verification must leave the incident active, not present an
        # unverified recovery as resolved. Restore the simulated overlay.
        scenario.status = "active"
        for item in previous_state["scenario_pod_overrides"]:
            db.add(
                ScenarioPodState(
                    scenario_id=scenario.id,
                    pod_id=item["pod_id"],
                    status=item["status"],
                    ready_containers=item["ready_containers"],
                    restart_count=item["restart_count"],
                    reason=item["reason"],
                )
            )
        db.flush()
        persisted_report = gather_evidence(db, action.ticket)
        resulting_state = {
            "scenario_status": scenario.status,
            "effective_revision": persisted_report.deployment_history.current_revision,
            "ready_replicas": persisted_report.pods.ready_count,
            "expected_replicas": persisted_report.pods.expected_replicas,
            "scenario_pod_overrides": (
                db.query(ScenarioPodState).filter_by(scenario_id=scenario.id).count()
            ),
            "baseline": _baseline_state(db, action.ticket),
            "verification_attempt": attempted_state,
        }
    else:
        resulting_state = attempted_state

    verified_at = datetime.now(timezone.utc)
    execution.outcome = "succeeded" if passed else "failed"
    execution.resulting_state = json.dumps(resulting_state)
    execution.completed_at = verified_at
    db.add(
        VerificationResult(
            execution_id=execution.id,
            passed=passed,
            checks=json.dumps(checks),
            verified_at=verified_at,
        )
    )
    action.status = "executed" if passed else "execution_failed"
    if passed:
        action.ticket.status = "resolved"
    db.flush()
    return execution
