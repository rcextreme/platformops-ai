import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import SupportTicket
from app.seed import seed_database
from app.services.ai_assessment import collect_refs, generate_assessment
from app.services.investigation_analysis import build_report
from app.services.llm_provider import (
    LLMProvider,
    LLMUnavailableError,
    OpenAICompatibleProvider,
    UnconfiguredProvider,
    get_llm_provider,
)

TICKETS_URL = "/api/platform/tickets"


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def good_reply(**overrides):
    reply = {
        "executive_summary": "Customer Portal is degraded: 1 of 3 replicas is ready.",
        "likely_cause_explanation": (
            "Revision 42 was deployed and two pods then failed readiness. "
            "This is correlated, not proven."
        ),
        "customer_impact": {
            "assessment": "Some requests may return HTTP 503.",
            "assumptions": ["Assumes traffic is spread evenly across replicas."],
        },
        "supporting_evidence": [
            {"statement": "Revision 42 failed.", "evidence_refs": ["revision/42"]}
        ],
        "contradictory_evidence": [
            {
                "statement": "Ingress reports healthy.",
                "evidence_refs": ["ingress/customer-portal-ingress"],
            }
        ],
        "missing_information": ["Application logs were not collected."],
        "limitations": ["No metrics are available."],
        "recommended_next_steps": ["Review the proposed rollback to revision 41."],
        "customer_response": "We are investigating elevated errors and will update you.",
    }
    reply.update(overrides)
    return reply


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai.db'}",
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


@pytest.fixture()
def report(factory, ticket_id):
    with factory() as db:
        return build_report(db, db.get(SupportTicket, ticket_id))


def use_provider(provider):
    app.dependency_overrides[get_llm_provider] = lambda: provider
    return provider


def ai_url(ticket_id):
    return f"{TICKETS_URL}/{ticket_id}/investigation/ai"


def test_success_structure_and_deterministic_basis(client, ticket_id):
    provider = use_provider(FakeProvider(good_reply()))
    resp = client.get(ai_url(ticket_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "generated"
    assert body["ai_generated"] is True
    assert body["provider"] == {"name": "fake", "model": "fake-1"}
    a = body["assessment"]
    for key in (
        "executive_summary",
        "likely_cause_explanation",
        "customer_impact",
        "supporting_evidence",
        "contradictory_evidence",
        "missing_information",
        "limitations",
        "recommended_next_steps",
        "customer_response",
    ):
        assert a[key]
    assert a["customer_impact"]["assumptions"]
    basis = body["deterministic_basis"]
    assert basis["outcome"] == "finding"
    assert basis["confidence"] == "medium"
    action = basis["recommended_actions"][0]
    assert action["action"] == "rollback"
    assert (action["from_revision"], action["to_revision"]) == (42, 41)
    assert action["status"] == "proposed" and action["executed"] is False
    assert body["grounding"]["dropped_refs"] == []
    assert len(provider.calls) == 1


def test_prompt_contains_report_and_safety_rules(client, ticket_id):
    provider = use_provider(FakeProvider(good_reply()))
    client.get(ai_url(ticket_id))
    system, user = provider.calls[0]
    assert "cannot authorize or execute" in system
    assert "revision/42" in user
    assert "TCK-1001" in user


def test_confidence_and_actions_cannot_be_overridden_by_model(client, ticket_id):
    reply = good_reply(
        confidence="high",
        recommended_actions=[{"action": "delete-namespace", "executed": True}],
    )
    use_provider(FakeProvider(reply))
    body = client.get(ai_url(ticket_id)).json()
    assert body["deterministic_basis"]["confidence"] == "medium"
    actions = body["deterministic_basis"]["recommended_actions"]
    assert [a["action"] for a in actions] == ["rollback"]
    assert "confidence" not in body["assessment"]


def test_invented_refs_are_dropped_and_reported(client, ticket_id):
    reply = good_reply(
        supporting_evidence=[
            {"statement": "Real.", "evidence_refs": ["revision/42", "log/oom-kill"]},
            {"statement": "Made up.", "evidence_refs": ["metric/cpu-spike"]},
        ]
    )
    use_provider(FakeProvider(reply))
    body = client.get(ai_url(ticket_id)).json()
    support = body["assessment"]["supporting_evidence"]
    assert len(support) == 1
    assert support[0]["evidence_refs"] == ["revision/42"]
    assert body["grounding"]["dropped_refs"] == ["log/oom-kill", "metric/cpu-spike"]
    assert body["grounding"]["dropped_statements"] == 1


def test_all_cited_refs_exist_in_evidence(client, ticket_id, report):
    use_provider(FakeProvider(good_reply()))
    body = client.get(ai_url(ticket_id)).json()
    known = collect_refs(report.evidence.model_dump(mode="json"))
    for item in (
        body["assessment"]["supporting_evidence"]
        + body["assessment"]["contradictory_evidence"]
    ):
        assert set(item["evidence_refs"]) <= known


def test_claim_of_executed_remediation_is_redacted(client, ticket_id):
    reply = good_reply(customer_response="The rollback has been rolled back and fixed.")
    use_provider(FakeProvider(reply))
    body = client.get(ai_url(ticket_id)).json()
    assert body["grounding"]["redacted_fields"] == ["customer_response"]
    assert body["assessment"]["customer_response"].startswith("[Removed")


def test_json_in_code_fence_is_accepted(client, ticket_id):
    use_provider(FakeProvider("```json\n" + json.dumps(good_reply()) + "\n```"))
    assert client.get(ai_url(ticket_id)).status_code == 200


def test_unconfigured_provider_returns_503(client, ticket_id, monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_API_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    resp = client.get(ai_url(ticket_id))
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "provider_unavailable"
    assert body["ai_generated"] is False
    assert "LLM_API_KEY" in body["reason"]
    assert "assessment" not in body


def test_provider_error_returns_503_without_fake_ai(client, ticket_id):
    use_provider(FakeProvider(error=LLMUnavailableError("boom")))
    resp = client.get(ai_url(ticket_id))
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "provider_unavailable"
    assert body["ai_generated"] is False
    assert "assessment" not in body and "deterministic_basis" not in body


@pytest.mark.parametrize(
    "reply", ["not json at all", "[1, 2]", {"executive_summary": "only this"}]
)
def test_invalid_model_output_returns_502(client, ticket_id, reply):
    use_provider(FakeProvider(reply))
    resp = client.get(ai_url(ticket_id))
    assert resp.status_code == 502
    body = resp.json()
    assert body["status"] == "provider_invalid_response"
    assert body["ai_generated"] is False


def test_unknown_ticket_404(client):
    use_provider(FakeProvider(good_reply()))
    assert client.get(ai_url(99999)).status_code == 404


def test_endpoint_is_read_only(client, factory, ticket_id):
    use_provider(FakeProvider(good_reply()))
    engine = factory.kw["bind"]
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn, cursor, statement, *a):
        statements.append(statement.lstrip().upper())

    assert client.get(ai_url(ticket_id)).status_code == 200
    assert statements
    assert all(s.startswith("SELECT") for s in statements)


def test_generate_assessment_direct(report):
    result = generate_assessment(report, FakeProvider(good_reply()))
    assert result.ticket_key == "TCK-1001"
    assert result.deterministic_basis.recommended_actions[0].executed is False


# --- provider configuration -------------------------------------------------


def test_get_llm_provider_unconfigured(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_API_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    provider = get_llm_provider()
    assert isinstance(provider, UnconfiguredProvider)
    with pytest.raises(LLMUnavailableError):
        provider.complete("s", "u")


def test_get_llm_provider_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_BASE_URL", "https://llm.example.test/v1/")
    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://llm.example.test/v1"
    assert provider.model == "m1"


def test_get_llm_provider_unsupported_kind(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mystery")
    monkeypatch.setenv("LLM_API_BASE_URL", "https://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_API_KEY", "k")
    assert isinstance(get_llm_provider(), UnconfiguredProvider)


def test_openai_compatible_http_error_is_unavailable(monkeypatch):
    import httpx

    def fake_post(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleProvider("https://x/v1", "m", "k")
    with pytest.raises(LLMUnavailableError):
        provider.complete("s", "u")


def test_openai_compatible_parses_reply(monkeypatch):
    import httpx

    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleProvider("https://x/v1", "m", "secret")
    assert provider.complete("s", "u") == "hi"
    assert seen["url"] == "https://x/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret"
