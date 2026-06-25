# Agent-X1: Project Details & Persistent Memory Log

This document serves as a persistent context store and memory log for Antigravity (AI Coding Assistant) across design, development, and execution sessions.

---

## 1. Project Context & Objectives

* **Project Name**: Agent-X1
* **Objective**: Build a Hermes-style autonomous agentic harness optimized for Windows and Linux cross-platform environments.
* **Environment**: VS Code integration, terminal CLI, REST API, APScheduler background jobs, and MS Teams gateway.
* **Orchestration**: Structured State Machine with DAG task execution, built-in error auto-recovery, and escalation gating.
* **Inference**: High-efficiency extraction/handshake of local GitHub Copilot tokens combined with plug-and-play BYOK options.
* **Security & Auditability**: Pre- and post-mutation file SHA-256 hashing, transaction correlation IDs, and human-approver tracking serialized in append-only JSONL files.

---

## 2. Technical Specs & Discovered Endpoints

### 2.1 GitHub Copilot Token Handshake
* **Config Paths**:
  - Windows: `%APPDATA%\github-copilot\hosts.json`
  - macOS/Linux: `~/.config/github-copilot/hosts.json`
* **OAuth token identifier**: `github.com` -> `oauth_token` (typically starts with `ghu_`).
* **Handshake endpoint**: `GET https://api.github.com/copilot_internal/v2/token`
* **Inference Endpoint**: `POST https://api.githubcopilot.com/chat/completions` (OpenAI format).

### 2.2 Azure DevOps (ADO) REST API
* **Base URL**: `https://dev.azure.com/{organization}/{project}/_apis`
* **Backlog WIQL Endpoint**: `/wit/wiql?api-version=7.1`
* **Work Item Patch Endpoint**: `/wit/workitems/{id}?api-version=7.1`
* **Pull Request Endpoint**: `/git/repositories/{repositoryId}/pullrequests?api-version=7.1`
* **Authentication**: Personal Access Token (PAT) as a Basic Auth header: `Basic Base64(":" + PAT)`.

### 2.3 Local API & Logging
* **Server**: FastAPI on `127.0.0.1:8000` (by default, with configuration for host/port).
* **Audit File**: `logs/audit_lineage.jsonl` (JSON Lines format).
* **Memory Store**: SQLite `memory.db` for episodic transactions, NumPy/LanceDB for semantic code search vectors.

---

## 3. Project File Roadmap

| File Path | Purpose |
| :--- | :--- |
| `config.yaml` | Application configuration (provider selections, API bindings, secrets config) |
| `jobs.yaml` | Scheduled background task intervals and tasks |
| `src/core/orchestrator.py` | State machine executing the Plan -> Execute -> Evaluate -> Learn cycle |
| `src/inference/router.py` | Inference client mapping inputs to Copilot or BYOK endpoints |
| `src/integrations/ado.py` | Azure DevOps REST client for Kanban syncing, commit integration, and PRs |
| `src/api/server.py` | FastAPI endpoint handler for remote triggers (with queuing feedback) and human-in-the-loop actions |
| `src/jobs/scheduler.py` | APScheduler background thread |
| `src/audit/lineage.py` | Structured audit log parser and pre/post hashing utility |
| `src/memory/memory.py` | SQLite and Vector DB managers |
| `src/skills/skills.py` | Skill loader and Creator engine |
| `.agents/skills/agent-harness-builder/SKILL.md` | Persistent workspace skill for agent-harness construction and refinement |

---

## 4. Session History & Progress Memory

### 2026-06-25 (Initial Design Phase)
* **Goal**: Establish full architecture and design.
* **Outcome**: 
  - Created [architecture_and_design.md](file:///Users/kundanyadav/SourceCode/Agent-X1/docs/architecture_and_design.md) and [project_details.md](file:///Users/kundanyadav/SourceCode/Agent-X1/project_details.md).
  - Formulated the modular system topology, Copilot token integration, error classification recovery matrix, scheduled background tasks, Azure DevOps sync paths, and audit logging parameters.
  - Refined the setup to support a **Manager-Worker** multi-agent topology (`CodeWorker`, `TestWorker`, `DevOpsWorker`).
  - Added a **Partitioned & Cross-Referencable Memory System** specifying database/vector schemas and API lookup queries.
  - Implemented **Linux & macOS cross-platform** routing abstractions for shell execution and environment configurations.
  - Fixed all Mermaid diagrams to use double-quotes on labels for GitHub rendering compatibility.
  - Built and updated the workspace developer skill [SKILL.md](file:///Users/kundanyadav/SourceCode/Agent-X1/.agents/skills/agent-harness-builder/SKILL.md) to log TDD policies, temp hygiene directories (`tmp/`), and planning approval requirements.
* **Next Step**: Awaiting user approval of the implementation plan. Once approved, initialize `config.yaml` and build the inference client `router.py`.

