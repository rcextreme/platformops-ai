from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    status = Column(String(30), default="active")

    workloads = relationship("Workload", back_populates="tenant")
    namespaces = relationship("Namespace", back_populates="tenant")
    support_tickets = relationship("SupportTicket", back_populates="tenant")


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    environment = Column(String(30), nullable=False)
    region = Column(String(50), nullable=False)
    version = Column(String(30), nullable=False)
    status = Column(String(30), default="healthy")
    provider = Column(String(50), default="Vultr")

    namespaces = relationship("Namespace", back_populates="cluster")
    nodes = relationship("Node", back_populates="cluster")
    pods = relationship("Pod", back_populates="cluster")
    services = relationship("Service", back_populates="cluster")
    ingresses = relationship("Ingress", back_populates="cluster")
    certificates = relationship("Certificate", back_populates="cluster")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("cluster_id", "name", name="uq_node_cluster_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    status = Column(String(30), default="Ready")
    role = Column(String(50), nullable=False)
    cpu_capacity_millicores = Column(Integer, nullable=False)
    memory_capacity_mb = Column(Integer, nullable=False)
    kubelet_version = Column(String(30), nullable=False)

    cluster = relationship("Cluster", back_populates="nodes")
    pods = relationship("Pod", back_populates="node")


class Namespace(Base):
    __tablename__ = "namespaces"
    __table_args__ = (
        UniqueConstraint("cluster_id", "name", name="uq_namespace_cluster_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    status = Column(String(30), default="active")

    tenant = relationship("Tenant", back_populates="namespaces")
    cluster = relationship("Cluster", back_populates="namespaces")
    pods = relationship("Pod", back_populates="namespace")
    services = relationship("Service", back_populates="namespace")
    ingresses = relationship("Ingress", back_populates="namespace")
    certificates = relationship("Certificate", back_populates="namespace")


class Pod(Base):
    __tablename__ = "pods"
    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_pod_namespace_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    namespace_id = Column(Integer, ForeignKey("namespaces.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)
    status = Column(String(30), default="Running")
    desired_containers = Column(Integer, default=1)
    ready_containers = Column(Integer, default=1)
    restart_count = Column(Integer, default=0)
    pod_ip = Column(String(45), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    namespace = relationship("Namespace", back_populates="pods")
    cluster = relationship("Cluster", back_populates="pods")
    node = relationship("Node", back_populates="pods")


class Workload(Base):
    __tablename__ = "workloads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    namespace = Column(String(100), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"))
    cluster_id = Column(Integer, ForeignKey("clusters.id"))
    desired_replicas = Column(Integer, default=1)
    ready_replicas = Column(Integer, default=1)
    status = Column(String(30), default="healthy")

    tenant = relationship("Tenant", back_populates="workloads")
    services = relationship("Service", back_populates="workload")
    support_tickets = relationship("SupportTicket", back_populates="workload")
    incident_scenarios = relationship("IncidentScenario", back_populates="workload")
    deployment_revisions = relationship(
        "DeploymentRevision", back_populates="workload"
    )
    platform_events = relationship("PlatformEvent", back_populates="workload")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_service_namespace_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    namespace_id = Column(Integer, ForeignKey("namespaces.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    workload_id = Column(Integer, ForeignKey("workloads.id"), nullable=False)
    service_type = Column(String(30), default="ClusterIP")
    port = Column(Integer, nullable=False)
    target_port = Column(Integer, nullable=False)
    protocol = Column(String(10), default="TCP")
    status = Column(String(30), default="healthy")
    cluster_ip = Column(String(45), nullable=True)

    namespace = relationship("Namespace", back_populates="services")
    cluster = relationship("Cluster", back_populates="services")
    workload = relationship("Workload", back_populates="services")
    ingresses = relationship("Ingress", back_populates="service")


class Ingress(Base):
    __tablename__ = "ingresses"
    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_ingress_namespace_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    namespace_id = Column(Integer, ForeignKey("namespaces.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    host = Column(String(255), nullable=False)
    path = Column(String(255), default="/")
    path_type = Column(String(30), default="Prefix")
    ingress_class = Column(String(100), default="nginx")
    tls_enabled = Column(Boolean, default=False)
    tls_secret_name = Column(String(100), nullable=True)
    status = Column(String(30), default="healthy")

    namespace = relationship("Namespace", back_populates="ingresses")
    cluster = relationship("Cluster", back_populates="ingresses")
    service = relationship("Service", back_populates="ingresses")
    certificates = relationship("Certificate", back_populates="ingress")


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "name", name="uq_certificate_namespace_name"
        ),
        UniqueConstraint(
            "namespace_id",
            "tls_secret_name",
            name="uq_certificate_namespace_secret",
        ),
        CheckConstraint(
            "expires_at > issued_at", name="ck_certificate_expires_after_issued"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    namespace_id = Column(Integer, ForeignKey("namespaces.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    ingress_id = Column(Integer, ForeignKey("ingresses.id"), nullable=True)
    tls_secret_name = Column(String(100), nullable=False)
    issuer = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(30), default="valid")
    renewal_status = Column(String(30), default="not_due")

    namespace = relationship("Namespace", back_populates="certificates")
    cluster = relationship("Cluster", back_populates="certificates")
    ingress = relationship("Ingress", back_populates="certificates")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_key = Column(String(30), unique=True, nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workload_id = Column(Integer, ForeignKey("workloads.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(30), default="medium")
    status = Column(String(30), default="open")
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tenant = relationship("Tenant", back_populates="support_tickets")
    workload = relationship("Workload", back_populates="support_tickets")
    incident_scenarios = relationship("IncidentScenario", back_populates="ticket")
    policy_actions = relationship("PolicyAction", back_populates="ticket")


class IncidentScenario(Base):
    """A simulated incident. Its data overlays, and never edits, baseline records."""

    __tablename__ = "incident_scenarios"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), nullable=False)
    workload_id = Column(Integer, ForeignKey("workloads.id"), nullable=False)
    status = Column(String(30), default="active")
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    ticket = relationship("SupportTicket", back_populates="incident_scenarios")
    workload = relationship("Workload", back_populates="incident_scenarios")
    pod_states = relationship("ScenarioPodState", back_populates="scenario")
    events = relationship("PlatformEvent", back_populates="scenario")
    deployment_revisions = relationship(
        "DeploymentRevision", back_populates="scenario"
    )


class ScenarioPodState(Base):
    """Effective pod health while a scenario is active. Baseline Pod rows are untouched."""

    __tablename__ = "scenario_pod_states"
    __table_args__ = (
        UniqueConstraint("scenario_id", "pod_id", name="uq_scenario_pod_state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("incident_scenarios.id"), nullable=False)
    pod_id = Column(Integer, ForeignKey("pods.id"), nullable=False)
    status = Column(String(30), nullable=False)
    ready_containers = Column(Integer, nullable=False)
    restart_count = Column(Integer, nullable=False)
    reason = Column(String(100), nullable=True)

    scenario = relationship("IncidentScenario", back_populates="pod_states")
    pod = relationship("Pod")


class DeploymentRevision(Base):
    __tablename__ = "deployment_revisions"
    __table_args__ = (
        UniqueConstraint(
            "workload_id", "revision", name="uq_deployment_revision_workload_revision"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    workload_id = Column(Integer, ForeignKey("workloads.id"), nullable=False)
    scenario_id = Column(Integer, ForeignKey("incident_scenarios.id"), nullable=True)
    revision = Column(Integer, nullable=False)
    image = Column(String(255), nullable=False)
    status = Column(String(30), default="healthy")
    deployed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    change_cause = Column(String(255), nullable=True)

    workload = relationship("Workload", back_populates="deployment_revisions")
    scenario = relationship("IncidentScenario", back_populates="deployment_revisions")


class PlatformEvent(Base):
    __tablename__ = "platform_events"

    id = Column(Integer, primary_key=True, index=True)
    event_key = Column(String(100), unique=True, nullable=False, index=True)
    scenario_id = Column(Integer, ForeignKey("incident_scenarios.id"), nullable=True)
    namespace_id = Column(Integer, ForeignKey("namespaces.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=False)
    workload_id = Column(Integer, ForeignKey("workloads.id"), nullable=True)
    pod_id = Column(Integer, ForeignKey("pods.id"), nullable=True)
    event_type = Column(String(20), default="Normal")
    reason = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    source_component = Column(String(100), nullable=True)
    count = Column(Integer, default=1)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)

    scenario = relationship("IncidentScenario", back_populates="events")
    workload = relationship("Workload", back_populates="platform_events")
    pod = relationship("Pod")


class PolicyAction(Base):
    """Persisted deterministic authorization decision for a ticket action."""

    __tablename__ = "policy_actions"
    __table_args__ = (
        UniqueConstraint("action_key", name="uq_policy_action_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    action_key = Column(String(255), nullable=False)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    classification = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False)
    rule_version = Column(String(50), nullable=False)
    rationale = Column(Text, nullable=False)
    risk = Column(Text, nullable=False)
    expected_impact = Column(Text, nullable=False)
    target_workload = Column(String(100), nullable=False)
    namespace = Column(String(100), nullable=True)
    cluster = Column(String(100), nullable=True)
    from_revision = Column(Integer, nullable=False)
    to_revision = Column(Integer, nullable=False)
    evidence_refs = Column(Text, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False)

    ticket = relationship("SupportTicket", back_populates="policy_actions")
    approval = relationship(
        "ApprovalDecision", back_populates="policy_action", uselist=False
    )
    execution = relationship(
        "RemediationExecution", back_populates="policy_action", uselist=False
    )


class ApprovalDecision(Base):
    """A single final human decision; identity is demo-only, not authentication."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("policy_action_id", name="uq_approval_action"),
    )

    id = Column(Integer, primary_key=True, index=True)
    policy_action_id = Column(
        Integer, ForeignKey("policy_actions.id"), nullable=False
    )
    decision = Column(String(20), nullable=False)
    operator = Column(String(100), nullable=False)
    reason = Column(Text, nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False)

    policy_action = relationship("PolicyAction", back_populates="approval")


class RemediationExecution(Base):
    """One simulated execution attempt per explicitly authorized action."""

    __tablename__ = "remediation_executions"
    __table_args__ = (
        UniqueConstraint("policy_action_id", name="uq_execution_action"),
    )

    id = Column(Integer, primary_key=True, index=True)
    policy_action_id = Column(
        Integer, ForeignKey("policy_actions.id"), nullable=False
    )
    outcome = Column(String(30), nullable=False)
    previous_state = Column(Text, nullable=False)
    resulting_state = Column(Text, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)

    policy_action = relationship("PolicyAction", back_populates="execution")
    verification = relationship(
        "VerificationResult", back_populates="execution", uselist=False
    )


class VerificationResult(Base):
    """Persisted post-remediation checks."""

    __tablename__ = "verification_results"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_verification_execution"),
    )

    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(
        Integer, ForeignKey("remediation_executions.id"), nullable=False
    )
    passed = Column(Boolean, nullable=False)
    checks = Column(Text, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False)

    execution = relationship("RemediationExecution", back_populates="verification")
