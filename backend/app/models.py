from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    status = Column(String(30), default="active")

    workloads = relationship("Workload", back_populates="tenant")


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    environment = Column(String(30), nullable=False)
    region = Column(String(50), nullable=False)
    version = Column(String(30), nullable=False)
    status = Column(String(30), default="healthy")
    provider = Column(String(50), default="Vultr")


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
