# Agent-X1: System Requirements Specification

This document details the functional, non-functional, security, and operational requirements for the **Agent-X1** autonomous agentic harness.

---

## 1. Operating Environment & Scope
* **Target Operating Systems**: Windows 10/11 (Primary execution target) and Linux (Ubuntu/RHEL). Cross-platform compatibility with macOS is required for development.
* **Workspace Integration**: Operates within active VS Code project workspaces, reading/writing local files and executing local tooling suites.
* **Shell Abstraction**: Must support dynamic execution routing. 
  - Windows hosts: `powershell.exe -ExecutionPolicy Bypass` or `cmd.exe`.
  - Linux/macOS hosts: `/bin/bash` or `/bin/sh`.

---

## 2. Inference & Model Router Requirements (BYOK)
* **GitHub Copilot Token Exchange (Default)**:
  - Must automatically extract active GitHub `oauth_token` (prefixed with `ghu_`) from:
    - Windows: `%APPDATA%\github-copilot\hosts.json` or `%USERPROFILE%\.config\gh\hosts.yml`
    - Linux/macOS: `~/.config/github-copilot/hosts.json` or `~/.config/gh/hosts.yml`
  - Must perform the handshake exchange against `GET https://api.github.com/copilot_internal/v2/token` using a valid IDE User-Agent (`GithubCopilot/1.250.0`).
  - Must route completion requests to `https://api.githubcopilot.com/chat/completions`.
  - Must cache the JWT token and perform background refreshes when remaining Time-To-Live (TTL) is less than 5 minutes.
* **Bring Your Own Key (BYOK) Fallbacks**:
  - Must expose a plug-and-play abstraction to route completions to standard commercial APIs (OpenAI, Anthropic, Gemini, or local Ollama endpoints).
  - Must read credentials dynamically from environment variables (e.g. `OPENAI_API_KEY`) to prevent key exposure.
  - Must support automatic fallback to the configured BYOK model if Copilot CLI queries exhaust rate-limits (HTTP 429).

---

## 3. Multi-Agent Orchestration (Manager-Worker)
* **Topology**: Enforces a strict hierarchical Manager-Worker structure.
* **Manager Role (Orchestrator)**:
  - Receives user goals and utilizes the active LLM provider to decompose them into a structured Directed Acyclic Graph (DAG) of task nodes (specifying worker assignment, parameters, and verification triggers).
  - Enforces a **Mandatory Goal-Planning Gate**: Once the task decomposition and implementation plan are generated, the Orchestrator must write the plan to a local workspace file (e.g., `tasks_plan.md`) and notify the user via active gateways (CLI, MS Teams, API). It **MUST** pause and wait for the user's explicit approval. No worker code execution is allowed to start until the plan is approved.
  - Enforces **Dynamic Re-planning**: If a task fails or verification metrics are not met after maximum auto-recovery retries, the Orchestrator feeds the error trace back to the LLM to re-evaluate context and dynamically adjust the remaining task nodes in the DAG.
  - Enforces **Adaptive Re-planning Gating**: During re-planning, the Orchestrator classifies the proposed change:
    - **Minor Changes** (e.g., tweaking local compiler flags, local function variable name changes, modifying helper lines) are auto-executed to avoid user fatigue.
    - **Major Changes** (e.g., adding third-party package dependencies, deleting existing files, modifying core API contracts, adding new high-level task steps) are paused and require user re-approval.
    - **Maturing Gating Boundary**: The agent writes user feedback on re-planning turns (approvals vs. declines) to its memory store, utilizing this history to iteratively refine and mature its classification boundary over time.
  - Manages the execution state machine, coordinates worker agents, evaluates task outcomes, and triggers human gating approvals.
* **Worker Roles (Specialized Subagents)**:
  - **CodeWorker**: Reads workspace source files, edits files, and applies line-by-line diff patches.
  - **TestWorker**: Executes compilers, builds solutions, triggers tests, and parses stdout/stderr to inspect outcomes.
  - **DevOpsWorker**: Manages local git actions (branching, committing) and interfaces with cloud platforms (Azure DevOps).
* **Execution Mutex**: Enforces a session lock on the workspace. Only one active goal/DAG task sequence can run at any given time.

---

## 4. Local Persisted Memory & RAG Requirements
* **Episodic Memory**: SQLite database (`memory.db`) storing transaction sessions, tool actions, and evaluations.
* **Semantic Memory**: NumPy-based cosine similarity index or an in-process LanceDB database storing codebase facts, troubleshooting recipes, and successful file diffs.
* **Memory Partitioning**:
  - All written records must contain an `agent_owner` field (`orchestrator`, `codeworker`, `testworker`, `devopsworker`).
  - Write access is strictly sandboxed: an agent can write *only* to its own memory partition to prevent state contamination.
  - Read access is globally open: any agent can run queries against other agents' partitions (e.g. the `CodeWorker` can query `TestWorker`'s log history to see past compilation errors and fixes on the same code file).
* **RAG Prompt Enrichment**:
  - Before calling the LLM, the system must search the Vector DB for matching entries (cosine similarity threshold $\ge 0.75$).
  - Matches must be formatted into a clean markdown structure containing the source agent, issue description, resolution notes, and the exact git diff/command used.
  - This context block must be dynamically prepended to system instructions.

---

## 5. Interaction Gateways & Interfaces

### 5.1 Terminal CLI Gateway
* Interactive console supporting auto-completing commands (`/goal`, `/status`, `/skills`, `/history`, `/config`).
* Live progress dashboard showing subtask execution trees and status.
* Inline prompts for human approval requests.

### 5.2 FastAPI REST API
* **Task Submission**: `POST /v1/tasks/run` dispatches goals.
  - **Queuing & Acknowledgment**: If the orchestrator is busy, the task is queued. The endpoint must immediately return an HTTP 202 acknowledgment containing the `task_id`, `status: "queued"`, and `queue_position`.
* **State & Gates**: `GET /v1/tasks/{id}/status` retrieves state/position, and `POST /v1/tasks/{id}/approve` registers human approvals.
* **Security**: Binds to `127.0.0.1:8000` (localhost) by default, requiring configurable API key authentication.

### 5.3 MS Teams Integration
* Webhook receiver accepting execution commands.
* Progress notifications pushed to configured Teams channels.
* **Human-in-the-Loop Gating**: Sends Interactive Adaptive Cards with "Approve" / "Decline" buttons. Webhook forwards clicks back to the API server to resume task loops.

---

## 6. Azure DevOps (ADO) Integration
* **Backlog Polling**: Background scheduled job queries the ADO Wit API:
  `[System.AssignedTo] = 'Agent-X1'` and `[System.State] = 'To Do'`.
* **Kanban State Mapping**: Configurable mapping inside `config.yaml` to sync with custom organization boards.
* **Branch Management**: Creates branch `feature/task-{id}` on task initiation, committing code using semantic linkage: `feat: resolve bug #{id} - linked work item`.
* **Pull Request Automation**: Pushes branch to ADO Git and opens a pull request linking the Work Item ID via relations, automatically updating the Kanban card column.

---

## 7. Scheduler & Background Daemon
* **APScheduler cron engine**: Reads cron schedules from `jobs.yaml` to run background syncs (backlog checks) and audits (nightly tests).
* **Daemonization**:
  - Windows: Runs as a Windows Service (`win32serviceutil` wrapper).
  - Linux: Runs as a Systemd unit (`agent-x1.service`).

---

## 8. Security, Auditing & Data Lineage
* **Correlation Tracker**: Genarates UUIDv4 correlation IDs for every session. Transmitted across all logs, prompts, files, and git commits.
* **File Mutation Hashing**: For every file edit:
  - Must record pre-edit SHA-256 hash.
  - Must record post-edit SHA-256 hash.
  - Must record full line-by-line patch/diff.
* **Accountability Logging**: Records human approver emails, gate response timestamps, tool arguments, stdout/stderr, and LLM reasoning.
* **Output**: Written to append-only, SIEM-compliant JSON Lines logs (`logs/audit_lineage.jsonl`).

---

## 9. Development & Code Hygiene Constraints
* **Mandatory Planning Gate (Phase 0)**: No code or configuration edits can begin until an `implementation_plan.md` is approved by the user.
* **Test-Driven Development**: Unit and integration tests must be written concurrently with every module under `tests/`.
* **Temp Folder Hygiene**: All temporary files, mock test databases, run logs, and compiler artifacts generated during development or testing must live strictly in the `<project_root>/tmp/` folder.
* **Git Cleanliness**: The project's `.gitignore` must block the tracking of `/tmp/` and `/logs/` files.

---

## 10. Future Roadmap (Phase 2 - Context Management via Headroom)
To optimize long-running agent execution context and lower token expenses:
* **Context Compression Layer**: Integrate the `Headroom` context compression layer to compress logs, tool outputs, database schemas, and AST-parsed code files by up to 60-95% before submitting prompts to the inference router.
* **Content-Compressed Retrieval (CCR)**: Retain the full, uncompressed payloads in local caching, allowing the LLM to request retrieval of specific uncompressed sections if it requires high-fidelity analysis to resolve coding tasks.
* **KV Cache Aligner**: Incorporate a cache aligner to ensure that system instructions, schemas, and historical prefixes remain structurally stabilized, maximizing prompt cache hits at the model provider level to minimize inference latency and costs.
