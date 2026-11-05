from sqlalchemy.orm import Session

from .models import Tenant, Cluster, Workload


def seed_database(db: Session):
    # Avoid duplicating seed data on restart.
    if db.query(Tenant).first():
        return

    acme = Tenant(name="ACME Corp", slug="acme")
    globex = Tenant(name="Globex", slug="globex")
    initech = Tenant(name="Initech", slug="initech")

    db.add_all([acme, globex, initech])
    db.flush()

    prod_cluster = Cluster(
        name="k8s-prod-east",
        environment="production",
        region="ewr",
        version="1.35.2",
        status="healthy",
        provider="Vultr",
    )

    dev_cluster = Cluster(
        name="k8s-dev-east",
        environment="development",
        region="ewr",
        version="1.35.2",
        status="healthy",
        provider="Vultr",
    )

    db.add_all([prod_cluster, dev_cluster])
    db.flush()

    workloads = [
        Workload(
            name="customer-portal",
            namespace="acme-prod",
            tenant_id=acme.id,
            cluster_id=prod_cluster.id,
            desired_replicas=3,
            ready_replicas=3,
            status="healthy",
        ),
        Workload(
            name="order-management",
            namespace="globex-prod",
            tenant_id=globex.id,
            cluster_id=prod_cluster.id,
            desired_replicas=3,
            ready_replicas=3,
            status="healthy",
        ),
        Workload(
            name="employee-services",
            namespace="initech-dev",
            tenant_id=initech.id,
            cluster_id=dev_cluster.id,
            desired_replicas=2,
            ready_replicas=2,
            status="healthy",
        ),
    ]

    db.add_all(workloads)
    db.commit()
