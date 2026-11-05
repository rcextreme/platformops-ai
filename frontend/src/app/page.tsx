
type Tenant = {
  id: number;
  name: string;
  slug: string;
  status: string;
};

type Cluster = {
  id: number;
  name: string;
  environment: string;
  region: string;
  version: string;
  status: string;
  provider: string;
};

type Workload = {
  id: number;
  name: string;
  namespace: string;
  tenant: string;
  cluster: string;
  desired_replicas: number;
  ready_replicas: number;
  status: string;
};

type PlatformOverview = {
  summary: {
    tenant_count: number;
    cluster_count: number;
    workload_count: number;
    healthy_workload_count: number;
  };
  tenants: Tenant[];
  clusters: Cluster[];
  workloads: Workload[];
};

export const instant = false;

async function getOverview(): Promise<PlatformOverview> {
  const apiUrl =
    process.env.INTERNAL_API_URL || "http://localhost:8000";

  const response = await fetch(`${apiUrl}/api/platform/overview`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("Unable to retrieve platform overview");
  }

  return response.json();
}

function Status({ value }: { value: string }) {
  const healthy = value.toLowerCase() === "healthy" ||
    value.toLowerCase() === "active";

  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${
        healthy
          ? "bg-emerald-50 text-emerald-700"
          : "bg-amber-50 text-amber-700"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${
        healthy ? "bg-emerald-500" : "bg-amber-500"
      }`} />
      {value}
    </span>
  );
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: number;
  detail: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
        {value}
      </p>
      <p className="mt-2 text-xs text-slate-500">{detail}</p>
    </div>
  );
}

export default async function Home() {
  const data = await getOverview();
  const { summary } = data;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="flex min-h-screen">
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-slate-950 text-white lg:flex lg:flex-col">
          <div className="border-b border-slate-800 px-6 py-6">
            <div className="text-xl font-bold tracking-tight">
              PlatformOps <span className="text-cyan-400">AI</span>
            </div>
            <p className="mt-1 text-xs text-slate-400">
              Autonomous Platform Operations
            </p>
          </div>

          <nav className="flex-1 space-y-2 px-4 py-6">
            <p className="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
              Operations
            </p>
            {[
              ["▦", "Platform Overview"],
              ["◈", "Infrastructure"],
              ["◎", "Support Tickets"],
              ["✧", "AI Investigations"],
              ["✓", "Remediation Approvals"],
              ["◷", "Maintenance Roadmap"],
              ["☷", "Engineering Backlog"],
              ["≡", "Audit History"],
            ].map(([icon, label], index) => (
              <div
                key={label}
                className={`flex items-center gap-3 rounded-lg px-3 py-3 text-sm ${
                  index === 0
                    ? "bg-cyan-500/15 font-medium text-cyan-300"
                    : "text-slate-400"
                }`}
              >
                <span>{icon}</span>
                {label}
              </div>
            ))}
          </nav>

          <div className="border-t border-slate-800 p-5">
            <p className="text-xs text-slate-500">Environment</p>
            <p className="mt-1 text-sm font-medium">Vultr · Simulation</p>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5 lg:px-9">
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
                Platform Operations
              </p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight">
                Platform Overview
              </h1>
            </div>
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                Platform Online
              </span>
              <span className="hidden text-sm text-slate-500 sm:block">
                Demo Environment
              </span>
            </div>
          </header>

          <div className="space-y-8 p-6 lg:p-9">
            <section>
              <div className="mb-4">
                <h2 className="text-lg font-semibold">Environment Health</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Overview of your managed PaaS infrastructure
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Metric
                  label="Active Tenants"
                  value={summary.tenant_count}
                  detail="Registered platform customers"
                />
                <Metric
                  label="Kubernetes Clusters"
                  value={summary.cluster_count}
                  detail="Production and development"
                />
                <Metric
                  label="Application Workloads"
                  value={summary.workload_count}
                  detail="Across all tenants"
                />
                <Metric
                  label="Healthy Workloads"
                  value={summary.healthy_workload_count}
                  detail="Reporting healthy status"
                />
              </div>
            </section>

            <section className="grid gap-6 xl:grid-cols-2">
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 px-5 py-4">
                  <h2 className="font-semibold">Kubernetes Clusters</h2>
                  <p className="mt-1 text-xs text-slate-500">
                    Managed cluster inventory
                  </p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Cluster</th>
                        <th className="px-5 py-3">Version</th>
                        <th className="px-5 py-3">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.clusters.map((cluster) => (
                        <tr key={cluster.id} className="border-t border-slate-100">
                          <td className="px-5 py-4">
                            <p className="font-medium">{cluster.name}</p>
                            <p className="mt-1 text-xs capitalize text-slate-500">
                              {cluster.environment} · {cluster.region}
                            </p>
                          </td>
                          <td className="px-5 py-4 text-slate-600">
                            {cluster.version}
                          </td>
                          <td className="px-5 py-4">
                            <Status value={cluster.status} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 px-5 py-4">
                  <h2 className="font-semibold">Platform Tenants</h2>
                  <p className="mt-1 text-xs text-slate-500">
                    Customer environments
                  </p>
                </div>
                <div className="divide-y divide-slate-100">
                  {data.tenants.map((tenant) => (
                    <div
                      key={tenant.id}
                      className="flex items-center justify-between px-5 py-4"
                    >
                      <div>
                        <p className="font-medium">{tenant.name}</p>
                        <p className="mt-1 text-xs text-slate-500">
                          Tenant ID: {tenant.slug}
                        </p>
                      </div>
                      <Status value={tenant.status} />
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-200 px-5 py-4">
                <h2 className="font-semibold">Application Workloads</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Application readiness across managed clusters
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-500">
                    <tr>
                      <th className="px-5 py-3">Application</th>
                      <th className="px-5 py-3">Tenant</th>
                      <th className="px-5 py-3">Cluster</th>
                      <th className="px-5 py-3">Replicas</th>
                      <th className="px-5 py-3">Health</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.workloads.map((workload) => (
                      <tr key={workload.id} className="border-t border-slate-100">
                        <td className="px-5 py-4">
                          <p className="font-medium">{workload.name}</p>
                          <p className="mt-1 text-xs text-slate-500">
                            {workload.namespace}
                          </p>
                        </td>
                        <td className="px-5 py-4 text-slate-600">
                          {workload.tenant}
                        </td>
                        <td className="px-5 py-4 text-slate-600">
                          {workload.cluster}
                        </td>
                        <td className="px-5 py-4 font-mono text-xs">
                          {workload.ready_replicas}/{workload.desired_replicas}
                        </td>
                        <td className="px-5 py-4">
                          <Status value={workload.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <footer className="border-t border-slate-200 pt-5 text-xs text-slate-400">
              PlatformOps AI · Autonomous Platform Operations · Hackathon Demo
            </footer>
          </div>
        </main>
      </div>
    </div>
  );
}

