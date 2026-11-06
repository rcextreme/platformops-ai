from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
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
