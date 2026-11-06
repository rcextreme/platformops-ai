from sqlalchemy.orm import Session

from .models import Tenant, Cluster, Namespace, Node, Pod, Workload


def get_or_create_tenant(db: Session, name: str, slug: str) -> Tenant:
    tenant = db.query(Tenant).filter_by(slug=slug).first()
    if tenant is None:
        tenant = Tenant(name=name, slug=slug)
        db.add(tenant)
        db.flush()
    return tenant


def get_or_create_cluster(db: Session, **attrs) -> Cluster:
    cluster = db.query(Cluster).filter_by(name=attrs["name"]).first()
    if cluster is None:
        cluster = Cluster(**attrs)
        db.add(cluster)
        db.flush()
    return cluster


def get_or_create_namespace(
    db: Session, name: str, tenant: Tenant, cluster: Cluster
) -> Namespace:
    namespace = (
        db.query(Namespace).filter_by(name=name, cluster_id=cluster.id).first()
    )
    if namespace is None:
        namespace = Namespace(
            name=name,
            tenant_id=tenant.id,
            cluster_id=cluster.id,
            status="active",
        )
        db.add(namespace)
        db.flush()
    return namespace


def get_or_create_node(db: Session, **attrs) -> Node:
    node = (
        db.query(Node)
        .filter_by(name=attrs["name"], cluster_id=attrs["cluster_id"])
        .first()
    )
    if node is None:
        node = Node(**attrs)
        db.add(node)
        db.flush()
    return node


def get_or_create_pod(
    db: Session, namespace: Namespace, name: str, **attrs
) -> Pod:
    pod = db.query(Pod).filter_by(namespace_id=namespace.id, name=name).first()
    if pod is None:
        pod = Pod(
            name=name,
            namespace_id=namespace.id,
            cluster_id=namespace.cluster_id,
            **attrs,
        )
        db.add(pod)
        db.flush()
    return pod


def get_or_create_workload(db: Session, **attrs) -> Workload:
    workload = (
        db.query(Workload)
        .filter_by(
            name=attrs["name"],
            namespace=attrs["namespace"],
            cluster_id=attrs["cluster_id"],
        )
        .first()
    )
    if workload is None:
        workload = Workload(**attrs)
        db.add(workload)
        db.flush()
    return workload


def seed_database(db: Session):
    acme = get_or_create_tenant(db, "ACME Corp", "acme")
    globex = get_or_create_tenant(db, "Globex", "globex")
    initech = get_or_create_tenant(db, "Initech", "initech")

    prod_cluster = get_or_create_cluster(
        db,
        name="k8s-prod-east",
        environment="production",
        region="ewr",
        version="1.35.2",
        status="healthy",
        provider="Vultr",
    )
    dev_cluster = get_or_create_cluster(
        db,
        name="k8s-dev-east",
        environment="development",
        region="ewr",
        version="1.35.2",
        status="healthy",
        provider="Vultr",
    )

    acme_ns = get_or_create_namespace(db, "acme-prod", acme, prod_cluster)
    globex_ns = get_or_create_namespace(db, "globex-prod", globex, prod_cluster)
    initech_ns = get_or_create_namespace(db, "initech-dev", initech, dev_cluster)

    nodes = [
        (prod_cluster, "prod-control-01", "control-plane", 4000, 16384),
        (prod_cluster, "prod-worker-01", "worker", 8000, 32768),
        (prod_cluster, "prod-worker-02", "worker", 8000, 32768),
        (dev_cluster, "dev-control-01", "control-plane", 2000, 8192),
        (dev_cluster, "dev-worker-01", "worker", 4000, 16384),
    ]
    for cluster, name, role, cpu, memory in nodes:
        get_or_create_node(
            db,
            name=name,
            cluster_id=cluster.id,
            status="Ready",
            role=role,
            cpu_capacity_millicores=cpu,
            memory_capacity_mb=memory,
            kubelet_version="v1.35.2",
        )

    get_or_create_workload(
        db,
        name="customer-portal",
        namespace="acme-prod",
        tenant_id=acme.id,
        cluster_id=prod_cluster.id,
        desired_replicas=3,
        ready_replicas=3,
        status="healthy",
    )
    get_or_create_workload(
        db,
        name="order-management",
        namespace="globex-prod",
        tenant_id=globex.id,
        cluster_id=prod_cluster.id,
        desired_replicas=3,
        ready_replicas=3,
        status="healthy",
    )
    get_or_create_workload(
        db,
        name="employee-services",
        namespace="initech-dev",
        tenant_id=initech.id,
        cluster_id=dev_cluster.id,
        desired_replicas=2,
        ready_replicas=2,
        status="healthy",
    )

    pods = [
        (acme_ns, "customer-portal-7d9f6c-abc12", "10.244.1.11"),
        (acme_ns, "customer-portal-7d9f6c-def34", "10.244.2.12"),
        (acme_ns, "customer-portal-7d9f6c-ghi56", "10.244.1.13"),
        (globex_ns, "order-management-6b8d5f-jkl78", "10.244.2.21"),
        (globex_ns, "order-management-6b8d5f-mno90", "10.244.1.22"),
        (globex_ns, "order-management-6b8d5f-pqr12", "10.244.2.23"),
        (initech_ns, "employee-services-5c7e9a-stu34", "10.245.1.11"),
        (initech_ns, "employee-services-5c7e9a-vwx56", "10.245.1.12"),
    ]
    workers = {
        cluster.id: db.query(Node)
        .filter_by(cluster_id=cluster.id, role="worker")
        .order_by(Node.name)
        .all()
        for cluster in (prod_cluster, dev_cluster)
    }
    assigned = {prod_cluster.id: 0, dev_cluster.id: 0}
    for namespace, name, ip in pods:
        cluster_workers = workers[namespace.cluster_id]
        node = cluster_workers[assigned[namespace.cluster_id] % len(cluster_workers)]
        assigned[namespace.cluster_id] += 1
        get_or_create_pod(
            db,
            namespace,
            name,
            node_id=node.id,
            status="Running",
            desired_containers=1,
            ready_containers=1,
            restart_count=0,
            pod_ip=ip,
        )

    db.commit()
