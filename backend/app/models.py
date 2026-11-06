from datetime import datetime, timezone

from sqlalchemy import (
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
