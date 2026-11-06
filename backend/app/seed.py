from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import (
    Tenant,
    Cluster,
    Namespace,
    Node,
    Pod,
    Service,
    Ingress,
    Certificate,
    Workload,
    SupportTicket,
    IncidentScenario,
    ScenarioPodState,
    DeploymentRevision,
    PlatformEvent,
)


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


def get_or_create_service(
    db: Session, namespace: Namespace, workload: Workload, name: str, **attrs
) -> Service:
    if (
        workload.namespace != namespace.name
        or workload.cluster_id != namespace.cluster_id
        or workload.tenant_id != namespace.tenant_id
    ):
        raise ValueError(
            f"Workload {workload.name} does not belong to namespace {namespace.name}"
        )

    service = (
        db.query(Service).filter_by(namespace_id=namespace.id, name=name).first()
    )
    if service is None:
        service = Service(
            name=name,
            namespace_id=namespace.id,
            cluster_id=namespace.cluster_id,
            workload_id=workload.id,
            **attrs,
        )
        db.add(service)
        db.flush()
    return service


def get_or_create_ingress(
    db: Session, namespace: Namespace, service: Service, name: str, **attrs
) -> Ingress:
    if service.namespace_id != namespace.id:
        raise ValueError(
            f"Service {service.name} does not belong to namespace {namespace.name}"
        )
    if service.cluster_id != namespace.cluster_id:
        raise ValueError(
            f"Service {service.name} is not in the same cluster as "
            f"namespace {namespace.name}"
        )

    ingress = (
        db.query(Ingress).filter_by(namespace_id=namespace.id, name=name).first()
    )
    if ingress is None:
        ingress = Ingress(
            name=name,
            namespace_id=namespace.id,
            cluster_id=namespace.cluster_id,
            service_id=service.id,
            **attrs,
        )
        db.add(ingress)
        db.flush()
    return ingress


def get_or_create_certificate(
    db: Session,
    namespace: Namespace,
    name: str,
    ingress: Ingress | None = None,
    **attrs,
) -> Certificate:
    if ingress is not None:
        if ingress.namespace_id != namespace.id:
            raise ValueError(
                f"Ingress {ingress.name} does not belong to namespace "
                f"{namespace.name}"
            )
        if ingress.cluster_id != namespace.cluster_id:
            raise ValueError(
                f"Ingress {ingress.name} is not in the same cluster as "
                f"namespace {namespace.name}"
            )
        if not ingress.tls_enabled:
            raise ValueError(f"Ingress {ingress.name} does not have TLS enabled")
        if attrs.get("tls_secret_name") != ingress.tls_secret_name:
            raise ValueError(
                f"Certificate {name} secret does not match ingress "
                f"{ingress.name} TLS secret"
            )
        if attrs.get("subject") != ingress.host:
            raise ValueError(
                f"Certificate {name} subject does not match ingress "
                f"{ingress.name} host"
            )

    certificate = (
        db.query(Certificate)
        .filter_by(namespace_id=namespace.id, name=name)
        .first()
    )
    if certificate is None:
        certificate = Certificate(
            name=name,
            namespace_id=namespace.id,
            cluster_id=namespace.cluster_id,
            ingress_id=ingress.id if ingress else None,
            **attrs,
        )
        db.add(certificate)
        db.flush()
    return certificate


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


def get_or_create_support_ticket(
    db: Session, tenant: Tenant, workload: Workload, ticket_key: str, **attrs
) -> SupportTicket:
    ticket = db.query(SupportTicket).filter_by(ticket_key=ticket_key).first()
    if ticket is not None:
        return ticket
    if workload.tenant_id != tenant.id:
        raise ValueError(
            f"Workload {workload.name} does not belong to tenant {tenant.name}"
        )
    ticket = SupportTicket(
        ticket_key=ticket_key,
        tenant_id=tenant.id,
        workload_id=workload.id,
        **attrs,
    )
    db.add(ticket)
    db.flush()
    return ticket


def get_or_create_incident_scenario(
    db: Session, key: str, ticket: SupportTicket, workload: Workload, **attrs
) -> IncidentScenario:
    scenario = db.query(IncidentScenario).filter_by(key=key).first()
    if scenario is not None:
        return scenario
    if ticket.workload_id != workload.id:
        raise ValueError(
            f"Ticket {ticket.ticket_key} is not linked to workload {workload.name}"
        )
    scenario = IncidentScenario(
        key=key, ticket_id=ticket.id, workload_id=workload.id, **attrs
    )
    db.add(scenario)
    db.flush()
    return scenario


def get_or_create_scenario_pod_state(
    db: Session, scenario: IncidentScenario, pod: Pod, **attrs
) -> ScenarioPodState:
    state = (
        db.query(ScenarioPodState)
        .filter_by(scenario_id=scenario.id, pod_id=pod.id)
        .first()
    )
    if state is not None:
        return state
    if pod.namespace.name != scenario.workload.namespace:
        raise ValueError(
            f"Pod {pod.name} is outside the scenario workload's namespace"
        )
    state = ScenarioPodState(scenario_id=scenario.id, pod_id=pod.id, **attrs)
    db.add(state)
    db.flush()
    return state


def get_or_create_deployment_revision(
    db: Session,
    workload: Workload,
    revision: int,
    scenario: IncidentScenario | None = None,
    **attrs,
) -> DeploymentRevision:
    record = (
        db.query(DeploymentRevision)
        .filter_by(workload_id=workload.id, revision=revision)
        .first()
    )
    if record is None:
        record = DeploymentRevision(
            workload_id=workload.id,
            revision=revision,
            scenario_id=scenario.id if scenario else None,
            **attrs,
        )
        db.add(record)
        db.flush()
    return record


def get_or_create_platform_event(
    db: Session,
    event_key: str,
    namespace: Namespace,
    workload: Workload,
    scenario: IncidentScenario | None = None,
    pod: Pod | None = None,
    **attrs,
) -> PlatformEvent:
    event = db.query(PlatformEvent).filter_by(event_key=event_key).first()
    if event is not None:
        return event
    if workload.namespace != namespace.name:
        raise ValueError(
            f"Workload {workload.name} is not in namespace {namespace.name}"
        )
    if pod is not None and pod.namespace_id != namespace.id:
        raise ValueError(f"Pod {pod.name} is not in namespace {namespace.name}")
    event = PlatformEvent(
        event_key=event_key,
        namespace_id=namespace.id,
        cluster_id=namespace.cluster_id,
        workload_id=workload.id,
        scenario_id=scenario.id if scenario else None,
        pod_id=pod.id if pod else None,
        **attrs,
    )
    db.add(event)
    db.flush()
    return event


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

    customer_portal = get_or_create_workload(
        db,
        name="customer-portal",
        namespace="acme-prod",
        tenant_id=acme.id,
        cluster_id=prod_cluster.id,
        desired_replicas=3,
        ready_replicas=3,
        status="healthy",
    )
    order_management = get_or_create_workload(
        db,
        name="order-management",
        namespace="globex-prod",
        tenant_id=globex.id,
        cluster_id=prod_cluster.id,
        desired_replicas=3,
        ready_replicas=3,
        status="healthy",
    )
    employee_services = get_or_create_workload(
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

    services = [
        (acme_ns, customer_portal, "customer-portal-svc", "10.96.10.11"),
        (globex_ns, order_management, "order-management-svc", "10.96.10.21"),
        (initech_ns, employee_services, "employee-services-svc", "10.97.10.11"),
    ]
    for namespace, workload, name, ip in services:
        get_or_create_service(
            db,
            namespace,
            workload,
            name,
            service_type="ClusterIP",
            port=80,
            target_port=8080,
            protocol="TCP",
            status="healthy",
            cluster_ip=ip,
        )

    ingresses = [
        (
            acme_ns,
            "customer-portal-svc",
            "customer-portal-ingress",
            "portal.acme.example.com",
            True,
            "acme-portal-tls",
        ),
        (
            globex_ns,
            "order-management-svc",
            "order-management-ingress",
            "orders.globex.example.com",
            True,
            "globex-orders-tls",
        ),
        (
            initech_ns,
            "employee-services-svc",
            "employee-services-ingress",
            "employees.initech.example.com",
            False,
            None,
        ),
    ]
    for namespace, service_name, name, host, tls_enabled, tls_secret in ingresses:
        service = (
            db.query(Service)
            .filter_by(namespace_id=namespace.id, name=service_name)
            .one()
        )
        get_or_create_ingress(
            db,
            namespace,
            service,
            name,
            host=host,
            path="/",
            path_type="Prefix",
            ingress_class="nginx",
            tls_enabled=tls_enabled,
            tls_secret_name=tls_secret,
            status="healthy",
        )

    now = datetime.now(timezone.utc)
    incident_now = now
    certificates = [
        (
            acme_ns,
            "customer-portal-ingress",
            "customer-portal-cert",
            "acme-portal-tls",
            "portal.acme.example.com",
            now - timedelta(days=78),
            now + timedelta(days=12),
            "failed",
        ),
        (
            globex_ns,
            "order-management-ingress",
            "order-management-cert",
            "globex-orders-tls",
            "orders.globex.example.com",
            now - timedelta(days=30),
            now + timedelta(days=60),
            "not_due",
        ),
    ]
    for (
        namespace,
        ingress_name,
        name,
        secret,
        subject,
        issued_at,
        expires_at,
        renewal_status,
    ) in certificates:
        ingress = (
            db.query(Ingress)
            .filter_by(namespace_id=namespace.id, name=ingress_name)
            .one()
        )
        get_or_create_certificate(
            db,
            namespace,
            name,
            ingress=ingress,
            tls_secret_name=secret,
            issuer="Let's Encrypt",
            subject=subject,
            issued_at=issued_at,
            expires_at=expires_at,
            status="valid",
            renewal_status=renewal_status,
        )

    ticket = get_or_create_support_ticket(
        db,
        acme,
        customer_portal,
        "TCK-1001",
        title="Customer Portal returning HTTP 503",
        description=(
            "Since this morning our customers get intermittent HTTP 503 "
            "Service Unavailable errors when opening portal.acme.example.com. "
            "Some requests succeed after retrying. Please investigate the "
            "Customer Portal in production."
        ),
        severity="high",
        status="open",
    )

    # Simulated incident for TCK-1001. Baseline pods stay healthy; the scenario
    # overlays effective pod state and adds events and deployment history.
    # Timestamps are relative to first seed and never rewritten afterwards.
    scenario = get_or_create_incident_scenario(
        db,
        "acme-customer-portal-503",
        ticket,
        customer_portal,
        name="Customer Portal unhealthy deployment (HTTP 503)",
        description=(
            "Simulated incident: revision 42 of customer-portal was rolled out "
            "and two of its three pods fail readiness checks."
        ),
        status="active",
    )

    revisions = [
        (40, None, "registry.example.com/acme/customer-portal:2.7.2", "healthy",
         timedelta(days=12), "Routine release 2.7.2"),
        (41, None, "registry.example.com/acme/customer-portal:2.7.3", "healthy",
         timedelta(days=3), "Hotfix release 2.7.3"),
        (42, scenario, "registry.example.com/acme/customer-portal:2.8.0", "failed",
         timedelta(minutes=47), "Release 2.8.0: new checkout service client"),
    ]
    for number, owner, image, rev_status, age, cause in revisions:
        get_or_create_deployment_revision(
            db,
            customer_portal,
            number,
            scenario=owner,
            image=image,
            status=rev_status,
            deployed_at=incident_now - age,
            change_cause=cause,
        )

    deployed_at = incident_now - timedelta(minutes=47)
    portal_pods = {
        p.name: p
        for p in db.query(Pod).filter_by(namespace_id=acme_ns.id).all()
    }
    healthy_pod = portal_pods["customer-portal-7d9f6c-abc12"]
    failing_pods = [
        (portal_pods["customer-portal-7d9f6c-def34"], 3, timedelta(minutes=2)),
        (portal_pods["customer-portal-7d9f6c-ghi56"], 5, timedelta(minutes=3)),
    ]
    for pod, restarts, _ in failing_pods:
        get_or_create_scenario_pod_state(
            db,
            scenario,
            pod,
            status="Running",
            ready_containers=0,
            restart_count=restarts,
            reason="ReadinessProbeFailed",
        )

    events = [
        ("rollout-scale-up", None, "Normal", "ScalingReplicaSet",
         "Scaled up replica set customer-portal-7d9f6c to 3",
         "deployment-controller", 1, timedelta(minutes=0)),
        ("rollout-scale-down", None, "Normal", "ScalingReplicaSet",
         "Scaled down replica set customer-portal-5f8c4d to 0",
         "deployment-controller", 1, timedelta(minutes=1)),
        ("pod-abc12-started", healthy_pod, "Normal", "Started",
         "Started container customer-portal", "kubelet", 1, timedelta(minutes=1)),
    ]
    for pod, _, offset in failing_pods:
        suffix = pod.name.rsplit("-", 1)[1]
        events.append(
            (f"pod-{suffix}-readiness", pod, "Warning", "Unhealthy",
             "Readiness probe failed: HTTP probe failed with statuscode: 500",
             "kubelet", 12, offset)
        )
    for pod, _, offset in failing_pods:
        suffix = pod.name.rsplit("-", 1)[1]
        events.append(
            (f"pod-{suffix}-backoff", pod, "Warning", "BackOff",
             "Back-off restarting failed container customer-portal",
             "kubelet", 4, offset + timedelta(minutes=5))
        )
    for key, pod, event_type, reason, message, component, count, offset in events:
        get_or_create_platform_event(
            db,
            f"tck-1001-{key}",
            acme_ns,
            customer_portal,
            scenario=scenario,
            pod=pod,
            event_type=event_type,
            reason=reason,
            message=message,
            source_component=component,
            count=count,
            occurred_at=deployed_at + offset,
        )

    db.commit()
