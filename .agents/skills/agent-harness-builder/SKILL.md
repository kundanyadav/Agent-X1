---
name: agent-harness-builder
description: "Instructions, workflows, patterns, and best practices for building, extending, and refining the Agent-X1 autonomous agentic harness (Copilot token swapper, orchestrator, integrations, memory, logging)."
---

# Agent Harness Builder Skill

This skill provides the core workflows, guidelines, and patterns for developing, extending, and maintaining the **Agent-X1** autonomous agentic harness. Use these instructions when adding tools, updating orchestrator states, debugging connection issues, or refining lineage logging.

---

## 1. Extension Workflows

When implementing new features or resolving issues in the harness, follow these systematic steps:

```mermaid
graph TD
    Goal[Request: New Integration/Feature] --> Plan[0. Planning & Architecture Review]
    Plan --> UserApprove{User Approved?}
    UserApprove -- Yes --> Spec[1. Define Schema & Config in config.yaml]
    UserApprove -- No --> Plan
    Spec --> CoreDev[2. Implement Module in src/]
    CoreDev --> AuditDev[3. Register Actions in src/audit/lineage.py]
    AuditDev --> ToolDev[4. Add Tool Wrapper in src/core/orchestrator.py]
    ToolDev --> MockTest[5. Run Mock Unit Tests]
```

### 1.0 Step 0: Planning, Architecture Review, & Design Finalization (MANDATORY)
Before writing any code or modifying configurations, you **MUST** first finalize the design details and implementation steps with the user:
1. Research the codebase context and gather OS-specific implications.
2. Outline the exact components and files to modify or add.
3. Formulate the requirements clarification questionnaire (using Section 4).
4. Create/update the `implementation_plan.md` artifact and present it to the user.
5. **STOP and wait for the user's explicit review and approval.** Do not start the build phase (Step 1-5) until sign-off is given.

### 1.1 Step 1: Configuration Spec
Before coding, specify the necessary YAML keys in `config.yaml`.
* Decouple credentials (tokens/keys) from code using environment variable templates (e.g., `api_key: ${ENV_VAR}`).

### 1.2 Step 2: Implement the Core Module
* Create isolated modules under `src/<module_name>/`.
* Write OS-agnostic code using Python's `pathlib`.
* Ensure that all internal methods handle errors gracefully and raise structured exception classes (e.g., `InferenceError`, `IntegrationError`).

### 1.3 Step 3: Integrate with Lineage Logging
Every file-writing action, database transaction, or external API mutation must log audit trail events.
* Invoke `LineageLogger.log_action()` containing:
  - `correlation_id` (passed through context).
  - `action` (e.g., `file_write`, `api_post`).
  - File hashes (`pre_hash` and `post_hash`) for mutations.
  - LLM justification text.

### 1.4 Step 4: Expose to the Orchestrator
* Register the new functionality as a tool within the `ToolRunner` class.
* Define precise parameter JSON schemas for the LLM to inspect.

---

## 2. Design Patterns & Best Practices

### 2.1 OS-Agnostic Command Execution
Always route shell execution using system checks:
```python
import sys
import subprocess

def run_command(command: str):
    if sys.platform.startswith("win"):
        # Wrap command in powershell execution bypass
        shell_cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
    else:
        # Linux/macOS standard bash command
        shell_cmd = ["/bin/bash", "-c", command]
        
    return subprocess.run(shell_cmd, capture_output=True, text=True)
```

### 2.2 Token-Swapper Safeguards
When calling the Copilot internal endpoints:
1. **Never hardcode paths**: Use `os.path.expandvars` and `pathlib.Path.home()` to locate `hosts.json`.
2. **Token TTL Caching**: Store the swapped JWT along with its expiration timestamp. Check `time.time() < token_expiry - 300` before every call. If expired, perform the handshake.
3. **User-Agent Integrity**: Always use a standard IDE client user agent (e.g., `GithubCopilot/1.250.0`) to avoid token rejection.

### 2.3 Memory & Local Database Management

When implementing or updating the memory manager (`src/memory/memory.py`):
1. **SQLite Episodic Memory**:
   - Always use connection context managers: `with sqlite3.connect(db_path) as conn:`.
   - Set `timeout=30.0` to prevent database locks from concurrent API server and background job daemon operations.
   - Enforce database schemas at boot time (e.g. run `CREATE TABLE IF NOT EXISTS` commands on initialization).
   - Create indexes on lookup fields: `correlation_id` in action tables and `task_id` in feedback tables.
2. **Semantic Memory (Vector DB)**:
   - For a lightweight Windows/Linux environment, use a NumPy-based cosine similarity matrix or `LanceDB` (which runs in-process). Avoid heavy JVM-based or local service databases (like Qdrant or Milvus) to ease installation.
   - Decouple the embedding generator: wrap the embedding model in a provider interface so we can swap between a local CPU-based sentence-transformer model and external embedding endpoints.
   - Return scores alongside vectors; discard hits below a configurable similarity threshold (e.g., `similarity < 0.75`).

---

## 3. Verification & Testing Playbook

### 3.1 Unit Testing
Every new integration must have mock tests located in `tests/`:
* Use `unittest.mock` to patch API requests (e.g., Azure DevOps board queries, Copilot token handshake).
* Verify file lineage logging outputs to verify that correct SHA-256 hashes are recorded for mutated test files.

### 3.2 Dynamic Integration Tests
Run a dry-run task execution loop:
```bash
python -m src.core.orchestrator --dry-run --goal "Build workspace and verify tests pass"
```
Check the output:
- Verify that a `Correlation-ID` was generated.
- Inspect `logs/audit_lineage.jsonl` to ensure trace actions are correctly captured.

### 3.3 Test-Driven Development & Temp Folder Hygiene
To maintain a clean codebase and avoid file littering:
1. **Concurrent Testing**: Write unit tests *during* the build phase of every module rather than waiting until the end of the project.
2. **Project-Local Temp Space**: All temporary files, database files created during tests (e.g., `test_memory.db`), mock files, and intermediate log output files must be written strictly to the `<project_root>/tmp/` directory.
3. **Standardizing Pathing in Tests**: Configure all test setup hooks to resolve paths relative to the project root's `tmp` folder:
   ```python
   project_tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
   project_tmp_dir.mkdir(exist_ok=True)
   ```
4. **Teardown Cleanup**: Every test case must clean up its specific temporary files in its `tearDown` or fixture teardown phase, leaving the workspace clean after runs.
5. **Git Ignored**: Ensure that `<project_root>/tmp/` is added to the project's `.gitignore` file so that temporary test artifacts are never tracked.

---

## 4. Requirements Gathering & Clarification Checklist

When implementing new requirements or integrations, you MUST ask the user the following clarifying questions to prevent incorrect design assumptions:

### 4.1 Security & Human-in-the-Loop Gating
* **Command Safelists / Blocklists**: "Are there specific commands or tools that must be blocked entirely, or do all execution actions require human approval?"
* **Timeout Gating Actions**: "If the user does not respond to a verification or approval prompt (e.g., via MS Teams) within a certain timeout, should the task be automatically rolled back (git checkout), aborted, or kept paused?"
* **Audit File Protections**: "Do audit logs need to be encrypted at rest (e.g., using a local secret key) or rotated at specific intervals?"

### 4.2 Azure DevOps Workspace Details
* **Kanban Board Column Mappings**: "What are the exact column names for your To Do, In Progress, Review, and Completed phases?"
* **Reviewers**: "Should the agent assign specific reviewers when creating pull requests?"
* **Branch Policy**: "What is the target integration branch (e.g., `main`, `master`, or `develop`) and what naming convention should feature branches use?"

### 4.3 LLM & Inference Parameters
* **BYOK Backends**: "Which provider and model identifier (e.g. Gemini 1.5 Pro, Claude 3.5 Sonnet) should be configured as the fallback for BYOK?"
* **Fallback Strategy**: "Should the router immediately switch to BYOK on Copilot rate limits (HTTP 429), or wait/backoff?"

### 4.4 Memory & Local Learning Settings
* **Isolation Scope**: "Should long-term semantic memory be globally shared across all projects and workspaces, or isolated strictly by folder/repository?"
* **Embedding Model Choice**: "Do you prefer using a local CPU-based embedding library (such as sentence-transformers, which is completely offline) or calling an external endpoint?"
* **Context Window Density**: "How many past solved tasks should the orchestrator retrieve and inject into the prompt context for new planning runs?"

### 4.5 Multi-Agent Orchestration Topology
* **Delegation Hierarchy**: "What sort of multi-agent setup is this? Do you prefer a Hierarchical Manager-Worker topology (where a single orchestrator parses the task and spawns specialized subagents for coding, testing, and git operations), a Peer-to-Peer Collaborative network (where agents pass message payloads sequentially), or a centralized Router network?"
* **Communication Protocol**: "How should agents communicate with one another (e.g., via direct function parameters, an in-memory message queue, or writing shared metadata files in the workspace)?"
