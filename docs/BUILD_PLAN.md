# PlatformOps AI — Master Build Plan
**Project:** PlatformOps AI  
**Team:** Team Extreme  
**Hackathon:** Vultr — Agent Rush  
**Challenge:** Challenge 2 — Powering the Future of Work  
**Focus Area:** Customer Support  
**Development Environment:** macOS, VS Code, GitHub Copilot Agent  
**Deployment Target:** Vultr  

## 1. Project Vision

PlatformOps AI simulates an intelligent Platform-as-a-Service (PaaS) operations team responsible for maintaining Kubernetes environments where tenants deploy and operate their applications.

The platform provides a centralized operational view of infrastructure health and uses AI to investigate tenant support issues, identify likely causes, recommend corrective actions, and safely coordinate remediation.

The goal is to demonstrate how AI can help a platform engineering team move from reactive ticket handling to intelligent, evidence-driven operations.

### Core capabilities

- Monitor the health of simulated cloud infrastructure and Kubernetes environments.
- Monitor clusters, nodes, workloads, pods, services, ingress, gateways, certificates, and deployment status.
- Investigate tenant-submitted support tickets.
- Correlate infrastructure evidence, deployment history, and platform events.
- Use AI to explain likely causes and recommend next steps.
- Automatically execute approved low-risk remediation.
- Require human approval for sensitive production changes.
- Escalate unresolved issues to engineering through Jira-style tickets.
- Identify proactive maintenance risks, including certificate expiration, vulnerabilities, deprecated APIs, and version upgrades.

## 2. Architecture and Design Principles

### Architecture

- **Frontend:** Next.js, React, TypeScript, Tailwind CSS.
- **Backend:** Python, FastAPI, Pydantic, SQLAlchemy.
- **Database:** PostgreSQL.
- **AI:** Configurable LLM provider through a provider abstraction.
- **Infrastructure:** Simulated cloud and Kubernetes environment for the hackathon.
- **Deployment:** Docker Compose locally; Vultr for hosted deployment.

### AI and automation boundaries

The system must maintain a clear separation between AI reasoning and operational execution.

1. The investigation engine collects and evaluates available evidence.
2. The AI reasoning layer interprets the investigation and communicates findings.
3. The policy engine determines which actions are permitted.
4. The approval workflow obtains human authorization when required.
5. The remediation executor performs only authorized actions.
6. Verification checks whether the remediation resolved the issue.
7. The audit history records decisions and actions.

**Important:** The AI must never independently authorize or execute production changes. The deterministic policy engine remains authoritative for permissions and safety.

### Implementation principles

- Build a working end-to-end demonstration before expanding functionality.
- Keep simulated infrastructure realistic but lightweight.
- Use real AI reasoning and real application workflows, even when infrastructure is simulated.
- Never fabricate evidence or present missing telemetry as observed data.
- Keep all remediation actions auditable.
- Prefer a small number of well-defined components over an unnecessarily complex multi-agent architecture.

---

# 3. Development Milestones

## Milestone 1 — Foundation and Development Environment

<status>COMPLETED</status>

Establish the application foundation and local development environment.

Deliverables:
- GitHub repository and project structure.
- Next.js frontend.
- FastAPI backend.
- PostgreSQL database.
- Docker Compose environment.
- Initial platform dashboard.
- Tenant, cluster, and workload inventory.
- Health and overview API endpoints.

## Milestone 2 — Simulated Platform Infrastructure

<status>COMPLETED</status>

Build the simulated infrastructure inventory and certificate monitoring capabilities.

Deliverables:
- Tenants, clusters, nodes, namespaces, workloads, and pods.
- Services and ingress resources.
- TLS certificate inventory.
- Certificate expiration monitoring.
- Certificate renewal status monitoring.
- Severity classification and filtering.
- Read-only certificate APIs.
- Automated tests.

## Milestone 3 — Support Tickets and Investigation Engine

<status>COMPLETED</status>

Build the deterministic investigation engine and create a realistic simulated production incident.

Demonstration incident:
- Tenant: ACME Corp.
- Application: Customer Portal.
- Ticket: TCK-1001.
- Problem: HTTP 503 responses.
- Deployment revision 42 introduced an unhealthy application state.
- Two of three pods fail readiness checks.
- Previous revision 41 was healthy.
- Service and ingress are recorded as healthy.
- Certificate renewal has failed but is a separate risk.

Deliverables:
- Support ticket data model and APIs.
- Incident scenario simulation.
- Deployment revision history.
- Kubernetes-style platform events.
- Effective pod health overrides without changing baseline records.
- Evidence collection service.
- Deterministic investigation analysis.
- Causal sequence and findings.
- Evidence references and confidence levels.
- Proposed rollback recommendation.
- Explicit identification of missing evidence.
- Automated tests.

The investigation must distinguish observed facts from inferred causes.

## Milestone 4 — AI Reasoning and Support Assessment

<status>COMPLETED</status>

Implement an AI reasoning layer that consumes the existing deterministic investigation analysis and produces a structured, human-readable support assessment.

### Implementation requirements

1. Review the existing backend architecture, especially:
   - `services/investigation.py`
   - `services/investigation_analysis.py`
   - API routes and Pydantic schemas.
   - Existing tests and configuration patterns.

2. Create an LLM provider abstraction.
   - Configure provider through environment variables.
   - Support API base URL, model, and API key configuration.
   - Do not hardcode credentials.
   - Keep provider-specific implementation separate from business logic.

3. Keep deterministic investigation analysis authoritative for:
   - Observed facts.
   - Evidence references.
   - Confidence limits.
   - Proposed actions.

4. Have AI generate:
   - Executive incident summary.
   - Plain-language explanation of likely cause.
   - Customer impact assessment, clearly labeling assumptions.
   - Supporting and contradictory evidence.
   - Missing information and investigation limitations.
   - Recommended next steps.
   - Concise customer-facing ticket response.

5. Enforce evidence-grounded reasoning.
   - Do not invent telemetry, evidence, events, or root causes.
   - Distinguish observed facts from inference and uncertainty.
   - Preserve the deterministic confidence limits.
   - Treat missing evidence as missing, not as proof.

6. Preserve remediation safety.
   - AI can recommend actions but cannot authorize or execute them.
   - The proposed rollback remains a recommendation only.
   - Authorization belongs to the policy engine.

7. Add API endpoint:

   `GET /api/platform/tickets/{ticket_id}/investigation/ai`

8. Handle unavailable AI providers explicitly.
   - Return a clear provider-unavailable response if the LLM is not configured or unavailable.
   - Never silently substitute a deterministic response and label it AI-generated.

9. Add tests using a fake/mock provider.
   - No network access or real API key required.
   - Test provider errors and unavailable configuration.
   - Test evidence grounding and output structure.

10. Run the complete backend test suite.

**Acceptance criteria:** TCK-1001 produces a coherent AI-generated investigation assessment, grounded in the existing evidence, with a customer-ready response and no unauthorized remediation.

Do not introduce a multi-agent framework or make unrelated frontend/database changes.

**Live verification (2026-11-06):** The endpoint returned 200 for TCK-1001 using Vultr Serverless Inference (`openai_compatible`, model `deepseek-v4.1-flash-1017`). 17 known evidence refs, 0 dropped refs, 0 dropped statements, 0 redacted fields, rollback not executed. Known cosmetic issue: occasional missing spaces in generated prose (for example "podsare"); not a grounding problem.

## Milestone 5 — Policy Engine, Approval Workflow, and Remediation

<status>COMPLETED</status>

Turn AI recommendations into controlled operational workflows.

### Policy engine

Implement deterministic rules to classify proposed actions into three categories:

1. **Automatic remediation**
   - Preapproved, low-risk actions.
   - Limited to explicitly permitted operations.

2. **Human approval required**
   - Sensitive operations.
   - Production rollback.
   - Actions with potential customer impact.

3. **Engineering escalation**
   - Issues requiring investigation or code changes.
   - Unresolved or unsupported remediation scenarios.

### Approval workflow

- Display proposed action and rationale.
- Show affected tenant, workload, cluster, and namespace.
- Show evidence supporting the action.
- Explain risk and expected impact.
- Allow an authorized user to approve or reject.
- Record who approved the action and when.

### Simulated remediation executor

Implement a simulated rollback for ACME's Customer Portal.

Expected workflow:
- AI recommends rollback from revision 42 to revision 41.
- Policy engine identifies production rollback as requiring approval.
- Platform engineer approves the action.
- Executor performs the simulated rollback.
- Scenario state changes to reflect revision 41.
- Pods return to healthy status.
- Verification confirms recovery.
- Ticket is updated with the result.
- Audit history records the complete workflow.

**Acceptance criteria:** A user can approve the proposed rollback, execute it in the simulated environment, and see the application recover from 1/3 ready replicas to 3/3.

No real production infrastructure changes are permitted in the hackathon demo.

**Implementation note (2026-11-06):** The deterministic policy engine supports `auto_approved`, `approval_required`, and `escalation_required`. The analyzer currently proposes only the ACME production rollback, which requires human approval; no auto-approved operation is currently proposed or introduced. The simulated 42-to-41 rollback records approval, execution, and verification, removes failed scenario pod overrides, and resolves TCK-1001 only when all checks pass. Baseline Workload and Pod rows remain unchanged. The operator identity is user-supplied for demo purposes and is not authentication. Full backend suite: 157 passed.

## Milestone 6 — Operational Dashboard and Demo Experience

<status>PLANNED</status>

Make the application compelling and easy to demonstrate.

### Platform overview

Display:
- Overall platform health.
- Tenant inventory.
- Cluster health.
- Node health.
- Workload and pod status.
- Services and ingress.
- Certificate expiration and renewal risks.
- Active incidents and support tickets.

### AI investigation experience

Display:
- Ticket details.
- Investigation progress.
- Evidence collected.
- Deployment history and events.
- AI-generated assessment.
- Confidence and limitations.
- Recommended actions.
- Approval status.
- Remediation execution history.
- Verification results.

### User experience

- Clear status indicators.
- Easy navigation between platform health and support tickets.
- Evidence details available without overwhelming the dashboard.
- Visible distinction between proposed, approved, executed, and verified actions.

**Acceptance criteria:** A hackathon judge can understand the problem, follow the investigation, see the AI's reasoning, and observe a successful remediation without needing an explanation of the underlying code.

## Milestone 7 — Vultr Deployment and Hackathon Presentation

<status>PLANNED</status>

Deploy the working application to Vultr and prepare the final demonstration.

Deliverables:
- Vultr-hosted application.
- Dockerized services.
- Production environment configuration.
- Secure secrets management.
- Persistent PostgreSQL storage.
- Public application URL.
- Health checks and basic operational logging.
- README with setup and architecture instructions.
- Demo script and presentation materials.

### Final demonstration sequence

1. Introduce PlatformOps AI and the simulated PaaS environment.
2. Show the platform health dashboard.
3. Open ACME's HTTP 503 support ticket.
4. Trigger the investigation.
5. Show the evidence collected from Kubernetes and deployment history.
6. Show the AI-generated root-cause assessment and customer response.
7. Explain why the rollback requires human approval.
8. Approve the rollback.
9. Show simulated remediation execution.
10. Verify that the application has recovered.
11. Show the updated ticket and audit history.

**Final acceptance criteria:** The complete workflow operates reliably from tenant ticket submission through AI investigation, approval, simulated remediation, and verified recovery.

---

# 4. Current Project Status

| Milestone | Description | Status |
|---|---|---|
| 1 | Foundation and development environment | Complete |
| 2 | Simulated infrastructure and certificates | Complete |
| 3 | Support tickets and investigation engine | Complete |
| 4 | AI reasoning and support assessment | Complete (147 tests; live Vultr inference verified) |
| 5 | Policy, approval, and remediation | Complete (157 backend tests passed) |
| 6 | Dashboard and demo experience | Planned |
| 7 | Vultr deployment and presentation | Planned |

## 5. Immediate Development Priority

The immediate priority is to complete one polished, end-to-end incident workflow.

The ACME HTTP 503 scenario is the primary demonstration. Avoid expanding into additional incident scenarios until this workflow is complete.

Priority order:

1. Connect the investigation, approval, simulated rollback, and verification workflow to the dashboard.
2. Deploy to Vultr.
3. Polish the presentation.

## 6. Definition of Success

PlatformOps AI should demonstrate that an AI-powered platform engineering team can:

- Understand a tenant's reported problem.
- Investigate infrastructure and application evidence.
- Identify a plausible cause and explain its confidence.
- Communicate findings in language a customer can understand.
- Recommend a safe corrective action.
- Respect human approval and operational policies.
- Execute and verify an authorized remediation.
- Maintain an auditable record of the entire process.

**The primary objective is not to build a complete commercial PaaS platform. It is to demonstrate a credible, working example of AI-powered platform operations and customer support.**