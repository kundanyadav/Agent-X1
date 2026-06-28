# Agent-X1: Autonomous Developer Harness

Agent-X1 is an autonomous, self-improving developer harness optimized for macOS, Linux, and Windows. It executes goals by automatically decomposing them into a Directed Acyclic Graph (DAG) of tasks assigned to specialized worker agents (`CodeWorker`, `TestWorker`, `DevOpsWorker`).

---

## Features
* **Manager-Worker Topology**: Decouples high-level task planning from execution.
* **Interactive CLI Planning Loop**: Run `--chat` or `--interactive` to review proposals, refine plans back-and-forth, and explicitly authorize execution.
* **Inference Router**: Swap seamlessly between zero-configuration local GitHub Copilot OAuth tokens or Bring Your Own Key (BYOK) providers (OpenAI, Gemini, Anthropic, Ollama).
* **Semantic Context Harvesting**: Automatically logs developer preferences and QA sessions directly into SQLite semantic memory.
* **Background Task Scheduler**: Schedule execution loops to run in the background using cron configurations.
* **Strict Human Sign-off**: Gate build execution behind the phrase `"approved for build"`.

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
   Edit [config.yaml](file:///Users/kundanyadav/SourceCode/Agent-X1/config.yaml) to customize model parameters, active providers, DB paths, and git repo locations.

---

## Usage

Start the agent CLI using python:

```bash
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

**1. Run a goal in interactive planning mode (with dry-run simulation):**
```bash
python3 -m src.gateways.gateway --chat --dry-run
```
*(If you omit the goal, the CLI prompts you dynamically: `Please enter your goal:`)*

**2. Direct execution of a goal with auto-approval:**
```bash
python3 -m src.gateways.gateway --goal "Implement user authentication"
```

---

## Interactive Planning Slash Commands

When in `--chat` or `--interactive` mode, you can type special slash commands directly into the prompt:

* **`/exit`**: Aborts the planning session and exits cleanly.
* **`/goal <new_goal>`**: Dynamically updates the active goal of the session, wipes previous chat logs, and requests a fresh initial proposal from the LLM.
* **`/btw <question>`**: Runs a **secondary Q&A thread** that queries semantic memory and the LLM. It prints the answer in your console and logs the exchange to memory, but **does not** pollute the chat history or modify the main proposal plan.
* **`/compact`**: Condenses the previous conversational turns into a single distilled summary of agreed requirements to save token counts and prevent context drift.
* **`/pin [name]`**: Saves the active goal, history, and cron schedule into SQLite memory (defaults to timestamp-based name if name is omitted).
* **`/resume`**: Lists available pinned sessions and prompts you to select and load an active context.
* **`/schedule "<cron_expression>"`**: Configures a background cron schedule for the goal (e.g. `/schedule "0 2 * * *"`). Once you type `"approved for build"`, the goal is saved under `tmp/` and written to [jobs.yaml](file:///Users/kundanyadav/SourceCode/Agent-X1/jobs.yaml).

---

## Directory Structure

* `src/core/orchestrator.py`: Manager state machine coordinating execution loops and memory updates.
* `src/gateways/gateway.py`: Terminal command console and REPL planner gateway.
* `src/inference/router.py`: Handles token extraction, BYOK expansion, and LLM requests.
* `src/jobs/`: Background cron daemon (`scheduler.py`) and job execution definitions (`tasks.py`).
* `src/memory/`: Vector and SQLite databases tracking session traces and semantic preference facts.
* `tests/`: Automated unit tests suite.

---

## Developer API & Orchestrator Methods

To extend or integrate with the planning and context APIs, leverage these core methods:

### Orchestration Engine (`src/core/orchestrator.py`)
* **`generate_planning_proposal(goal, history)`**: Prompts the active LLM to generate a structured design proposal.
* **`decompose_plan_into_tasks(finalized_plan)`**: Compiles the finalized markdown plan into a structured JSON task DAG list.
* **`learn_user_fact_if_needed(user_input)`**: Scans input for trigger keywords (e.g. `important`, `keep in mind`) to save design preferences to SQLite.
* **`answer_planning_qa(question)`**: Executes an isolated Q&A thread query against semantic memory and the LLM.
* **`compact_planning_history(goal, history)`**: Distills conversational loops to lower token count and prevent context drift.

### Memory Manager (`src/memory/memory.py`)
* **`pin_session(name, goal, history, scheduled_cron)`**: Serializes and stores active planning logs in SQLite.
* **`get_pinned_sessions()`**: Retrieves all pinned sessions ordered by timestamp.
* **`get_pinned_session(name)`**: Loads and parses a single pinned session's state.

### CLI Planner Gateway (`src/gateways/gateway.py`)
* **`interactive_planning_loop(goal, engine)`**: Runs the stateful interactive CLI planning loop (REPL).
* **`save_scheduled_job(goal, cron, correlation_id)`**: Appends scheduled goal tasks to `jobs.yaml` and writes JSON specs to `tmp/`.

### Background Scheduler Tasks (`src/jobs/tasks.py`)
* **`run_scheduled_goal(config_path)`**: Locates and executes the most recently scheduled background goal.
