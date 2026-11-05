from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from . import models
from .seed import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed_database(db)

    yield


app = FastAPI(
    title="PlatformOps AI",
    description="Autonomous Platform Operations Center",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "platformops-api"}


@app.get("/api/platform/overview")
def platform_overview(db: Session = Depends(get_db)):
    tenants = db.query(models.Tenant).all()
    clusters = db.query(models.Cluster).all()
    workloads = db.query(models.Workload).all()

    return {
        "summary": {
            "tenant_count": len(tenants),
            "cluster_count": len(clusters),
            "workload_count": len(workloads),
            "healthy_workload_count": sum(
                1 for w in workloads if w.status == "healthy"
            ),
        },
        "tenants": [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "status": t.status,
            }
            for t in tenants
        ],
        "clusters": [
            {
                "id": c.id,
                "name": c.name,
                "environment": c.environment,
                "region": c.region,
                "version": c.version,
                "status": c.status,
                "provider": c.provider,
            }
            for c in clusters
        ],
        "workloads": [
            {
                "id": w.id,
                "name": w.name,
                "namespace": w.namespace,
                "tenant": w.tenant.name if w.tenant else None,
                "cluster": db.get(models.Cluster, w.cluster_id).name
                if w.cluster_id else None,
                "desired_replicas": w.desired_replicas,
                "ready_replicas": w.ready_replicas,
                "status": w.status,
            }
            for w in workloads
        ],
    }
