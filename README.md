# PlatformOps AI

**Autonomous Platform Operations Engineer**

PlatformOps AI is an AI-powered operations assistant for a simulated Kubernetes-based Platform-as-a-Service (PaaS) provider.

It helps platform engineering teams monitor infrastructure health, investigate tenant support tickets, recommend or execute remediation actions, and coordinate engineering work.

## Project Status

- Milestone 1: Foundation — Complete
- Milestone 2: Simulated Platform Infrastructure — Upcoming
- Milestones 3–7: Planned

## Core Capabilities

- Platform-wide infrastructure health monitoring
- Kubernetes cluster and workload visibility
- Tenant support ticket investigation
- Evidence-based root cause analysis
- Automated remediation with policy controls
- Human approval for sensitive operations
- Jira escalation and engineering work planning
- Proactive maintenance and lifecycle recommendations
- Auditable operations history

## Architecture

| Component | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| Containerization | Docker, Docker Compose |
| AI | LLM-powered investigation and planning |
| Deployment target | Vultr |

## Development Environment

### Prerequisites

- macOS, Linux, or Windows with WSL
- Git
- Node.js 22+
- Python 3.12+
- Docker Desktop with Docker Compose

### Configuration

If you do not already have a local .env file, create one from the example:

cp .env.example .env

Important: If .env already exists, do not overwrite it.

Update .env with your local database credentials. Never commit .env or real credentials.

### Start the Application

Build and start the services:

```bash
docker compose build
docker compose up -d
```

Check service status:

```bash
docker compose ps
```

### Access the Application

| Service | URL |
|---|---|
| PlatformOps AI Dashboard | http://localhost:3000 |
| FastAPI Backend | http://localhost:8000 |
| API Health Check | http://localhost:8000/health |
| API Documentation | http://localhost:8000/docs |
| Platform Overview API | http://localhost:8000/api/platform/overview |

### Stop the Application

```bash
docker compose down
```

Database data is retained in the Docker volume.

## Simulated Environment

The initial demo environment includes:

- ACME Corp — Customer Portal
- Globex — Order Management
- Initech — Employee Services
- Production and development Kubernetes clusters

Infrastructure and incident scenarios will be simulated for the hackathon demonstration.

## Safety and Autonomy

PlatformOps AI uses a balanced autonomy model:

- Safe, predefined actions may execute automatically.
- Sensitive production changes require human approval.
- Issues requiring engineering investigation are escalated.
- Remediation results are verified and recorded in an audit trail.

## Project Roadmap

1. Foundation and development environment
2. Simulated platform infrastructure
3. AI investigation engine
4. Decision engine and remediation
5. Proactive platform operations
6. Final product experience
7. Vultr deployment and hackathon demo

## Hackathon

**Event:** Vultr Agent Rush Hackathon  
**Challenge:** Powering the Future of Work  
**Focus:** Customer Support  
**Team:** Team Extreme

---

*PlatformOps AI — Bringing intelligence and automation to platform operations.*
