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
| `docs/requirements.md` | System requirements specification |
| `docs/architecture_and_design.md` | Comprehensive system design manual |
| `docs/hermes_benchmarking.md` | Capability benchmarking & scoring comparison against Hermes Agent |
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
  - Performed capability benchmarking and scoring against Hermes Agent in [hermes_benchmarking.md](file:///Users/kundanyadav/SourceCode/Agent-X1/docs/hermes_benchmarking.md).
  - Created the formal [requirements.md](file:///Users/kundanyadav/SourceCode/Agent-X1/docs/requirements.md) specification document.
  - Integrated **Adaptive Re-planning Gating** with self-maturing classification boundaries in the design documents.
  - Added a Future Roadmap section to design documents planning for the **Headroom context compression layer** in Phase 2.
* **Next Step**: Awaiting user approval of the implementation plan. Once approved, initialize `config.yaml` and build the inference client `router.py`.

### 2026-06-28 (Stateful Planning, Slash Commands, & Distillation Phase)
* **Goal**: Refine the developer harness to support stateful interactive planning, context preference memory loggers, secure environment config, and slash commands.
* **Outcome**:
  - Implemented stateful back-and-forth CLI planning loop (`interactive_planning_loop`) in [gateway.py](file:///Users/kundanyadav/SourceCode/Agent-X1/src/gateways/gateway.py) with dynamic prompt validation.
  - Moved secrets, keys, and PATs to a git-ignored local `.env` configuration file with a version-controlled `.env.template`.
  - Added strict execution sign-off gating requiring the exact phrase `"approved for build"`.
  - Created automatic context preference semantic harvester (`learn_user_fact_if_needed`) that saves developer preferences to SQLite semantic memory database upon detection of key terms.
  - Implemented planning loop slash commands:
    * `/exit` (replaces `/abort` cleanly).
    * `/goal <new_goal>` (resets planning baseline).
    * `/schedule "<cron>"` (schedules the task asynchronously and appends job parameters to [jobs.yaml](file:///Users/kundanyadav/SourceCode/Agent-X1/jobs.yaml)).
    * `/btw <question>` (secondary Q&A thread using SQLite memory facts that does not pollute main plan context).
    * `/compact` (distills past conversation history into a concise summary of agreed design requirements to save LLM tokens and prevent context drift).
  - Created automated test suites [test_interactive_planning.py](file:///Users/kundanyadav/SourceCode/Agent-X1/tests/test_interactive_planning.py) and [test_slash_commands.py](file:///Users/kundanyadav/SourceCode/Agent-X1/tests/test_slash_commands.py) to cover all new behaviors (68 total tests OK).
* **Next Step**: Ready for further testing or user task delegation.





