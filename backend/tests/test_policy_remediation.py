import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (
    ApprovalDecision,
    IncidentScenario,
    Pod,
    PolicyAction,
    RemediationExecution,
    ScenarioPodState,
    SupportTicket,
    VerificationResult,
    Workload,
)
from app.seed import seed_database
from app.services.investigation_analysis import RecommendedAction, build_report
from app.services.policy_engine import (
    AUTO_APPROVED_ACTIONS,
    POLICY_RULE_VERSION,
    classify_action,
)

TICKETS_URL = "/api/platform/tickets"


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        seed_database(db)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield session_factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def client(factory):
    return TestClient(app)


@pytest.fixture()
def ticket_id(factory):
    with factory() as db:
        return db.query(SupportTicket).filter_by(ticket_key="TCK-1001").one().id


def evaluate(client, ticket_id):
    response = client.post(f"{TICKETS_URL}/{ticket_id}/actions/evaluate")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    return response.json()["actions"][0]


def approve(client, action_id, decision="approve"):
    return client.post(
        f"/api/platform/actions/{action_id}/approval",
        json={
            "decision": decision,
            "operator": "demo-platform-engineer",
            "reason": "Reviewed deterministic incident evidence.",
        },
    )


def baseline_snapshot(db):
    return {
        "workload": (
            db.query(Workload).filter_by(name="customer-portal").one().desired_replicas,
            db.query(Workload).filter_by(name="customer-portal").one().ready_replicas,
            db.query(Workload).filter_by(name="customer-portal").one().status,
        ),
        "pods": [
            (
                pod.name,
                pod.status,
                pod.ready_containers,
                pod.restart_count,
                pod.desired_containers,
            )
            for pod in db.query(Pod).filter(Pod.name.like("customer-portal-%")).order_by(Pod.name)
        ],
    }


def test_policy_persists_deterministic_decision_audit(client, factory, ticket_id):
    response = client.post(f"{TICKETS_URL}/{ticket_id}/actions/evaluate")
    assert response.status_code == 200
    action = response.json()["actions"][0]
    assert action["classification"] == "approval_required"
    assert action["status"] == "pending_approval"
    assert action["rule_version"] == POLICY_RULE_VERSION
    assert action["rationale"]
    assert action["risk"]
    assert action["expected_impact"]
    assert action["tenant"] == "ACME Corp"
    assert action["target_workload"] == "customer-portal"
    assert action["evidence_refs"]
    assert action["evaluated_at"]
    with factory() as db:
        persisted = db.query(PolicyAction).one()
        assert persisted.classification == "approval_required"
        assert json.loads(persisted.evidence_refs) == action["evidence_refs"]


def test_policy_evaluation_is_idempotent(client, ticket_id, factory):
    first = evaluate(client, ticket_id)["id"]
    with factory() as db:
        evaluated_at = db.query(PolicyAction).one().evaluated_at
    second = evaluate(client, ticket_id)["id"]
    assert second == first
    with factory() as db:
        assert db.query(PolicyAction).count() == 1
        assert db.query(PolicyAction).one().evaluated_at == evaluated_at


def test_all_classifications_supported_without_inventing_operation(factory, ticket_id):
    assert AUTO_APPROVED_ACTIONS == frozenset()
    with factory() as db:
        report = build_report(db, db.get(SupportTicket, ticket_id))
    rollback = report.analysis.recommended_actions[0]
    assert classify_action(rollback, report)[0] == "approval_required"

    future_allowlisted = RecommendedAction.model_construct(
        action="future-safe-operation",
        target_workload="customer-portal",
        namespace="acme-prod",
        cluster="k8s-prod-east",
        from_revision=42,
        to_revision=41,
        rationale="Test only; no such operation is implemented.",
        evidence_refs=[],
    )
    assert classify_action(
        future_allowlisted,
        report,
        auto_approved_actions=frozenset({"future-safe-operation"}),
    )[0] == "auto_approved"

    unsupported = RecommendedAction.model_construct(
        action="delete-production-namespace",
        target_workload="customer-portal",
        namespace="acme-prod",
        cluster="k8s-prod-east",
        from_revision=42,
        to_revision=41,
        rationale="Unsupported test action.",
        evidence_refs=[],
    )
    assert classify_action(unsupported, report)[0] == "escalation_required"


def test_execution_requires_approval(client, ticket_id):
    action_id = evaluate(client, ticket_id)["id"]
    response = client.post(f"/api/platform/actions/{action_id}/execute")
    assert response.status_code == 409


def test_approval_is_final_idempotent_and_reject_blocks_execution(
    client, ticket_id, factory
):
    action_id = evaluate(client, ticket_id)["id"]
    first = approve(client, action_id, "reject")
    assert first.status_code == 200
    assert first.json()["decision"] == "reject"
    repeated = approve(client, action_id, "reject")
    assert repeated.status_code == 200
    assert repeated.json()["decided_at"] == first.json()["decided_at"]
    conflict = approve(client, action_id, "approve")
    assert conflict.status_code == 409
    execution = client.post(f"/api/platform/actions/{action_id}/execute")
    assert execution.status_code == 409
    with factory() as db:
        assert db.query(ApprovalDecision).count() == 1
        assert db.query(RemediationExecution).count() == 0


def test_escalated_actions_cannot_execute(client, factory, ticket_id):
    with factory() as db:
        ticket = db.get(SupportTicket, ticket_id)
        action = PolicyAction(
            action_key="test-escalated",
            ticket_id=ticket.id,
            action_type="unsupported",
            classification="escalation_required",
            status="escalated",
            rule_version=POLICY_RULE_VERSION,
            rationale="Unsupported.",
            risk="Unknown.",
            expected_impact="Unknown.",
            target_workload="customer-portal",
            namespace="acme-prod",
            cluster="k8s-prod-east",
            from_revision=42,
            to_revision=41,
            evidence_refs="[]",
            evaluated_at=datetime.now(timezone.utc),
        )
        db.add(action)
        db.commit()
        action_id = action.id
    assert client.post(f"/api/platform/actions/{action_id}/execute").status_code == 409
    with factory() as db:
        assert db.query(RemediationExecution).count() == 0


def test_approved_rollback_verifies_and_is_idempotent(client, ticket_id, factory):
    with factory() as db:
        before = baseline_snapshot(db)
    action_id = evaluate(client, ticket_id)["id"]
    decision = approve(client, action_id)
    assert decision.status_code == 200
    assert decision.json()["decision"] == "approve"

    response = client.post(f"/api/platform/actions/{action_id}/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["idempotent_replay"] is False
    assert body["resulting_state"]["effective_revision"] == 41
    assert body["resulting_state"]["ready_replicas"] == 3
    assert body["resulting_state"]["scenario_pod_overrides"] == 0
    assert body["verification"]["passed"] is True
    assert all(body["verification"]["checks"].values())

    repeated = client.post(f"/api/platform/actions/{action_id}/execute")
    assert repeated.status_code == 200
    assert repeated.json()["idempotent_replay"] is True
    assert repeated.json()["resulting_state"] == body["resulting_state"]

    with factory() as db:
        assert baseline_snapshot(db) == before
        assert db.query(ScenarioPodState).count() == 0
        assert db.query(IncidentScenario).one().status == "resolved"
        assert db.query(SupportTicket).filter_by(id=ticket_id).one().status == "resolved"
        assert db.query(RemediationExecution).count() == 1
        assert db.query(VerificationResult).count() == 1
        assert db.query(VerificationResult).one().passed is True
        policy_action = db.query(PolicyAction).one()
        assert policy_action.status == "executed"
        assert policy_action.approval.decision == "approve"


def test_verification_failure_does_not_resolve_ticket(client, ticket_id, factory):
    with factory() as db:
        workload = db.query(Workload).filter_by(name="customer-portal").one()
        workload.desired_replicas = 4
        db.commit()
    action_id = evaluate(client, ticket_id)["id"]
    assert approve(client, action_id).status_code == 200
    response = client.post(f"/api/platform/actions/{action_id}/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert body["verification"]["passed"] is False
    assert body["verification"]["checks"]["effective_revision_41"] is True
    assert body["verification"]["checks"]["replicas_3_of_3"] is False
    with factory() as db:
        assert db.query(SupportTicket).filter_by(id=ticket_id).one().status == "open"
        assert db.query(VerificationResult).one().passed is False
        assert db.query(IncidentScenario).one().status == "active"
        assert db.query(ScenarioPodState).count() == 2
        assert db.query(PolicyAction).one().status == "execution_failed"


def test_policy_gets_are_read_only_and_history_is_complete(client, ticket_id, factory):
    action_id = evaluate(client, ticket_id)["id"]
    approve(client, action_id)
    client.post(f"/api/platform/actions/{action_id}/execute")
    with factory() as db:
        engine = db.get_bind()
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        listing = client.get(f"{TICKETS_URL}/{ticket_id}/actions")
        history = client.get(f"/api/platform/actions/{action_id}")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert listing.status_code == history.status_code == 200
    assert statements and all(statement.startswith("SELECT") for statement in statements)
    item = history.json()
    assert item["approval"]["operator"] == "demo-platform-engineer"
    assert item["execution"]["outcome"] == "succeeded"
    assert item["execution"]["verification"]["passed"] is True


def test_demo_operator_input_is_required(client, ticket_id):
    action_id = evaluate(client, ticket_id)["id"]
    response = client.post(
        f"/api/platform/actions/{action_id}/approval",
        json={"decision": "approve", "operator": "", "reason": ""},
    )
    assert response.status_code == 422
