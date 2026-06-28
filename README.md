# Agent-X1: Autonomous Developer Harness

Agent-X1 is an autonomous, self-improving developer harness optimized for macOS, Linux, and Windows. It executes goals by automatically decomposing them into a Directed Acyclic Graph (DAG) of tasks assigned to specialized worker agents (`CodeWorker`, `TestWorker`, `DevOpsWorker`).

---

## Features

* **Manager-Worker Topology**: Decouples high-level task planning from execution.
* **Interactive CLI Planning Loop**: Run `--chat` or `--interactive` to review proposals, refine plans back-and-forth, and explicitly authorize execution.
* **Premium REPL Console**: Arrow-key history, Tab auto-completion, colored output (neon blue user input, mustard yellow agent output), and a full suite of slash commands.
* **Inference Router**: Swap seamlessly between zero-configuration local GitHub Copilot OAuth tokens or Bring Your Own Key (BYOK) providers (OpenAI, OpenRouter, Gemini, Anthropic, Ollama). OpenRouter presets are supported via `@preset/<slug>` routing.
* **Semantic Context Harvesting**: Automatically logs developer preferences and QA sessions directly into SQLite semantic memory.
* **Background Task Scheduler**: Schedule execution loops to run in the background using cron configurations.
* **Strict Human Sign-off**: Gate build execution behind the phrase `"approved for build"`.
* **Pinned Session Management**: Save, restore, and delete planning sessions from SQLite so you never lose context.

---

## Installation & Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure secrets**:
   Copy `.env.template` to `.env` and fill in your API keys (e.g. OpenAI, Anthropic, Gemini, or personal access tokens for Azure DevOps):
   ```bash
   cp .env.template .env
   ```
   *Note: `.env` is ignored by Git to protect your credentials.*

3. **Application configuration**:
   Edit `config.yaml` to customize model parameters, active providers, DB paths, and git repo locations.

---

## Usage

Start the agent CLI using the wrapper script or python directly:

```bash
./agent-x1 --chat
# or
python3 -m src.gateways.gateway [flags]
```

### Command Line Flags

| Flag | Description |
| :--- | :--- |
| `-g`, `--goal` | Specify a goal directly as an argument. |
| `-i`, `--interactive` | Enable interactive planning (REPL mode). |
| `-c`, `--chat` | Enable chat terminal mode (prompts for goal if omitted). |
| `--dry-run` | Run in simulation mode (runs task structures without modifying files). |
| `--config` | Path to custom config file (default: `config.yaml`). |

### Command Examples

**1. Start a chat session (goal prompted interactively):**
```bash
./agent-x1 --chat
```
*(If you omit the goal, the CLI prompts: `Please enter your goal:`. You can also type `/resume` here to restore a saved session.)*

**2. Run a goal in interactive planning mode with dry-run simulation:**
```bash
./agent-x1 --chat --dry-run
```

**3. Direct execution of a goal with auto-approval:**
```bash
./agent-x1 --goal "Implement user authentication"
```

---

## Running with Docker

Three Docker services are available — the REST API, the background scheduler, and an interactive CLI session.

### First-time setup

```bash
# 1. Copy the env template and fill in your API keys
cp .env.template .env

# 2. Build the image
docker compose build
```

### Start services

```bash
# Start the REST API gateway + background scheduler (detached)
docker compose up -d agent-api agent-scheduler

# View live logs
docker compose logs -f

# Check running containers
docker compose ps
```

### Interactive CLI session (`--chat` mode)

```bash
# Drop into a live chat session inside the container
docker compose run --rm agent-cli

# Tip: the container shares ./tmp and ./logs with your host,
# so pinned sessions and audit logs persist between runs.
```

### Stop & clean up

```bash
# Stop all running services
docker compose down

# Stop and remove volumes (clears tmp/ and logs/ mounts)
docker compose down -v
```

### Security note

> **Never commit `.env`** — it is excluded by `.gitignore` and `.dockerignore`.  
> Secrets are injected at runtime via `env_file: .env` in `docker-compose.yml`.  
> A pre-commit git hook is installed locally to block any accidental key commits.

---

## Interactive Planning Slash Commands


When in `--chat` or `--interactive` mode, the REPL supports the following slash commands. Type `/help` at any time to print the full list.

### Session & Navigation

| Command | Description |
| :--- | :--- |
| `/exit` | Aborts the planning session and exits cleanly. |
| `/goal <new_goal>` | Pivots the active goal, clears history, and generates a fresh proposal. |
| `/resume` | Lists all pinned sessions and lets you pick one to restore by number or name. You can also type `/resume` at the initial goal prompt to skip entering a new goal entirely. |

### Plan Management

| Command | Description |
| :--- | :--- |
| `/pin [name]` | Saves the active goal, conversation history, and schedule to SQLite. Defaults to a timestamp-based name if omitted. |
| `/delete [name]` | Deletes a pinned session by name. If no name is given, shows a numbered list to choose from. |
| `/compact` | Condenses the current conversation into a single distilled summary to save token count and prevent context drift. |
| `/btw <question>` | Runs a **secondary Q&A thread** against semantic memory + the LLM. Prints the answer in-console without polluting the main plan history. |
| `/schedule "<cron>"` | Sets a background cron schedule (e.g. `/schedule "0 2 * * *"`). Written to `jobs.yaml` on approval. |

### REPL Utilities

| Command | Alias | Description |
| :--- | :--- | :--- |
| `/clear` | `/cls` | Clears the terminal screen and reprints the active proposal in a clean framed view. |
| `/status` | `/info` | Prints a live session panel: active goal, LLM provider, model, conversation turn, cron schedule, and pinned session count. |
| `/export [filename]` | — | Exports the current proposal to a Markdown file inside `tmp/` (default: `proposal.md`). Includes goal and timestamp header. |
| `/help` | `/options` | Reprints the full list of available slash commands. |

### Approving & Aborting

| Input | Effect |
| :--- | :--- |
| `approved for build` | Approves the proposal and triggers the execution loop (or writes to `jobs.yaml` if a schedule is set). |
| `abort` / `no` | Aborts the planning session immediately. |

---

## Terminal UX

The CLI uses **ANSI color styling** for a premium terminal experience:

* 🔵 **Neon blue** — your live typing and input prompt
* 🟡 **Mustard yellow** — agent status messages, proposal borders, and session info panels
* ⚪ **White** — proposal content body
* 🟢 **Green** — success confirmations (pin saved, session resumed, etc.)
* 🔴 **Red** — warnings, errors, and abort messages
* 🔵 **Cyan** — help text and command labels

### Arrow-key History & Tab Completion

* **Up / Down arrows** scroll through your previous inputs (persistent across sessions via `tmp/cli_history`).
* **Tab key** auto-completes slash commands — type `/ex` and press Tab to complete `/exit` or `/export`.
* Works on both Linux (GNU readline) and macOS (libedit) automatically.

---

## Directory Structure

```
Agent-X1/
├── src/
│   ├── core/
│   │   ├── orchestrator.py   # Manager state machine (planning, memory, execution)
│   │   └── tools.py          # ToolRunner wiring lineage + memory managers
│   ├── gateways/
│   │   └── gateway.py        # CLI REPL gateway and interactive planning loop
│   ├── inference/
│   │   └── router.py         # Inference router (token extraction, BYOK, LLM dispatch)
│   ├── memory/
│   │   └── memory.py         # SQLite + vector semantic memory manager
│   ├── audit/
│   │   └── lineage.py        # Audit lineage logger (encrypted or plaintext JSONL)
│   └── jobs/
│       ├── scheduler.py      # Background cron daemon
│       └── tasks.py          # Scheduled job execution definitions
├── tests/                    # Automated unit test suite (77 tests)
├── config.yaml               # Application configuration
├── jobs.yaml                 # Scheduled background goals registry
├── .env.template             # Secret key template
└── agent-x1                  # CLI wrapper script
```

---

## Developer API & Orchestrator Methods

To extend or integrate with the planning and context APIs, leverage these core methods:

### Orchestration Engine (`src/core/orchestrator.py`)

* **`generate_planning_proposal(goal, history)`** — Prompts the active LLM to generate a structured design proposal.
* **`decompose_plan_into_tasks(finalized_plan)`** — Compiles the finalized markdown plan into a structured JSON task DAG list.
* **`learn_user_fact_if_needed(user_input)`** — Scans input for trigger keywords (e.g. `important`, `keep in mind`) to save design preferences to SQLite.
* **`answer_planning_qa(question)`** — Executes an isolated Q&A thread query against semantic memory and the LLM.
* **`compact_planning_history(goal, history)`** — Distills conversational loops to lower token count and prevent context drift.

### Memory Manager (`src/memory/memory.py`)

* **`pin_session(name, goal, history, scheduled_cron)`** — Serializes and stores active planning logs in SQLite.
* **`get_pinned_sessions()`** — Retrieves all pinned sessions ordered by timestamp.
* **`get_pinned_session(name)`** — Loads and parses a single pinned session's state.
* **`delete_pinned_session(name)`** — Deletes a pinned session by name. Returns `True` on success, `False` if not found.

### CLI Planner Gateway (`src/gateways/gateway.py`)

* **`interactive_planning_loop(goal, engine)`** — Runs the stateful interactive CLI planning loop (REPL).
* **`save_scheduled_job(goal, cron, correlation_id)`** — Appends scheduled goal tasks to `jobs.yaml` and writes JSON specs to `tmp/`.

### Background Scheduler Tasks (`src/jobs/tasks.py`)

* **`run_scheduled_goal(config_path)`** — Locates and executes the most recently scheduled background goal.
