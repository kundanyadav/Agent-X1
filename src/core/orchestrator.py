import json
import uuid
import time
from typing import List, Dict, Any, Optional
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.core.workers import CodeWorker, TestWorker, DevOpsWorker
from src.integrations.ado import AzureDevOpsClient

class OrchestrationEngine:
    def __init__(
        self,
        router: InferenceRouter,
        tools: ToolRunner,
        memory: MemoryManager,
        ado_client: Optional[AzureDevOpsClient] = None
    ):
        self.router = router
        self.tools = tools
        self.memory = memory
        self.ado_client = ado_client
        
        # Instantiate workers
        self.code_worker = CodeWorker(router, tools, memory)
        self.test_worker = TestWorker(router, tools, memory)
        self.devops_worker = DevOpsWorker(router, tools, memory, ado_client)

    def generate_correlation_id(self) -> str:
        return str(uuid.uuid4())

    def decompose_goal(self, goal: str) -> List[Dict[str, Any]]:
        """Invokes LLM to decompose a high-level goal into a JSON task DAG."""
        system_prompt = (
            "You are the Manager Orchestrator of Agent-X1.\n"
            "Decompose the user's goal into a structured JSON list of task dictionaries.\n"
            "Each task MUST have the following keys:\n"
            "  - \"id\": unique string identifier (e.g. \"task-1\")\n"
            "  - \"name\": short descriptive name\n"
            "  - \"depends_on\": list of IDs of tasks that must finish before this task starts\n"
            "  - \"worker\": one of: \"codeworker\", \"testworker\", \"devopsworker\"\n"
            "  - \"args\": dictionary of arguments to pass to that worker\n"
            "    - For codeworker: {\"task_description\": \"...\"}\n"
            "    - For testworker: {\"test_command\": \"...\"}\n"
            "    - For devopsworker: {\"operation\": \"git_branch|git_commit|git_push|ado_sync_backlog|ado_update_state|ado_create_pr\", \"params\": {...}}\n"
            "Ensure the dependency DAG is acyclic. Do NOT output extra text or markdown wrapping, output raw JSON only."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Goal: {goal}"}
        ]
        
        resp = self.router.chat_completions(messages=messages, temperature=0.1)
        content = resp["choices"][0]["message"]["content"].strip()
        
        # Clean potential markdown wrappers
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            tasks = json.loads(content)
            # Ensure it is a list
            if not isinstance(tasks, list):
                tasks = [tasks]
            return tasks
        except Exception as e:
            # Fallback task list if LLM output fails to parse
            return [
                {
                    "id": "task-fallback-1",
                    "name": "Analyze goal requirements",
                    "depends_on": [],
                    "worker": "codeworker",
                    "args": {"task_description": f"Identify steps needed to achieve: {goal}"}
                }
            ]

    def is_major_change(self, task: Dict[str, Any]) -> bool:
        """Classifies if a task constitutes a 'Major' change requiring human approval."""
        worker = task.get("worker")
        args = task.get("args", {})
        
        # 1. Dependency install check
        if worker == "testworker":
            cmd = args.get("test_command", "").lower()
            if any(x in cmd for x in ["pip install", "npm install", "apt-get", "yum install", "apk add"]):
                return True
                
        # 2. Dangerous DevOps / git actions
        if worker == "devopsworker":
            op = args.get("operation")
            params = args.get("params", {})
            if op == "git_push" and params.get("force", False):
                return True
                
        # 3. File deletions / database migrations in instructions
        task_desc = str(args.get("task_description", "")).lower()
        if any(x in task_desc for x in ["delete file", "remove file", "rm ", "unlink", "migration", "alter table", "drop table"]):
            return True
            
        return False

    def execute_loop(self, correlation_id: str, tasks: List[Dict[str, Any]], auto_approve: bool = True) -> str:
        """Runs the state machine loop, executing ready tasks in the DAG and handling failures/re-planning."""
        task_states = {t["id"]: "pending" for t in tasks}
        task_map = {t["id"]: t for t in tasks}
        task_retries = {t["id"]: 0 for t in tasks}
        max_retries = 2

        while "pending" in task_states.values() or "running" in task_states.values():
            # Find tasks with all dependencies satisfied that are still pending
            ready_tasks = []
            for t_id, state in task_states.items():
                if state == "pending":
                    deps = task_map[t_id].get("depends_on", [])
                    if all(task_states.get(d) == "completed" for d in deps):
                        ready_tasks.append(task_map[t_id])

            if not ready_tasks:
                if "running" in task_states.values():
                    # Wait/continue if tasks were running asynchronously (in this synchronous loop, we run sequentially)
                    pass
                else:
                    # Deadlock or dependency issue
                    return "deadlocked"

            for task in ready_tasks:
                t_id = task["id"]
                
                # Check for adaptive human approval gate on Major actions
                if self.is_major_change(task) and not auto_approve:
                    # Mark as blocked
                    task_states[t_id] = "blocked"
                    self.memory.write_action(
                        session_id=correlation_id,
                        agent_owner="orchestrator",
                        tool_called="gate_approval",
                        arguments=task,
                        stdout="",
                        stderr="Task classified as MAJOR change, requiring human approval",
                        status="blocked"
                    )
                    return "paused_for_approval"

                task_states[t_id] = "running"
                worker_type = task["worker"]
                args = task["args"]
                
                # Log dispatch to memory
                self.memory.write_action(
                    session_id=correlation_id,
                    agent_owner="orchestrator",
                    tool_called="dispatch_task",
                    arguments={"task_id": t_id, "worker": worker_type, "args": args},
                    stdout=f"Dispatched task {t_id} to {worker_type}",
                    stderr="",
                    status="success"
                )

                # Execute task using appropriate worker
                worker_result = {"status": "failed", "error": "No worker ran"}
                if worker_type == "codeworker":
                    worker_result = self.code_worker.execute_task(args.get("task_description", ""), correlation_id)
                elif worker_type == "testworker":
                    worker_result = self.test_worker.execute_task(args.get("test_command", ""), correlation_id)
                elif worker_type == "devopsworker":
                    worker_result = self.devops_worker.execute_task(args.get("operation", ""), args.get("params", {}), correlation_id)

                if worker_result.get("status") == "success":
                    task_states[t_id] = "completed"
                else:
                    # Handle failure: retry or trigger dynamic re-planning
                    task_retries[t_id] += 1
                    if task_retries[t_id] <= max_retries:
                        # Mark back as pending to retry on next loop
                        task_states[t_id] = "pending"
                    else:
                        # Retries exhausted. Trigger dynamic re-planning!
                        error_msg = worker_result.get("error") or worker_result.get("diagnosis") or "Unknown error"
                        print(f"Task {t_id} failed after {max_retries} retries. Re-planning...")
                        
                        replanned_tasks = self.replan_failed_task(correlation_id, task, error_msg, tasks)
                        
                        # Integrate new tasks into states/maps
                        new_tasks = []
                        for rt in replanned_tasks:
                            if rt["id"] not in task_states:
                                new_tasks.append(rt)
                                task_states[rt["id"]] = "pending"
                                task_map[rt["id"]] = rt
                                task_retries[rt["id"]] = 0
                                
                        if not new_tasks:
                            # Re-planning did not add new corrective tasks, stop and fail
                            task_states[t_id] = "failed"
                            return "failed"
                        else:
                            # Current task resolved by replacement tasks, mark current as completed
                            task_states[t_id] = "completed"

        # Log final learning facts if errors were encountered and resolved
        failed_count = sum(1 for t, s in task_states.items() if s == "failed")
        if failed_count == 0:
            self.memory.learn_fact(
                agent_owner="orchestrator",
                category="execution_success",
                issue="goal execution finished",
                solution=f"All DAG tasks executed successfully for run {correlation_id}"
            )
            return "completed"
        return "failed"

    def replan_failed_task(
        self,
        correlation_id: str,
        failed_task: Dict[str, Any],
        error_msg: str,
        all_tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Asks the LLM to generate new corrective tasks to recover from a worker failure."""
        system_prompt = (
            "You are the Orchestration Re-planner of Agent-X1.\n"
            "A worker task has failed. You must analyze the failure and output a list of corrective tasks.\n"
            "Return a JSON list of tasks that should be added to the execution plan.\n"
            "Each corrective task must follow the standard keys: \"id\", \"name\", \"depends_on\", \"worker\", \"args\".\n"
            "Ensure the corrective tasks depend on each other and resolve the error, and that downstream tasks depend on them.\n"
            "Output RAW JSON list only, no explanation."
        )
        
        user_prompt = (
            f"Failed Task: {json.dumps(failed_task)}\n"
            f"Error details: {error_msg}\n"
            f"Current plan: {json.dumps(all_tasks)}"
        )
        
        try:
            resp = self.router.chat_completions(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1
            )
            content = resp["choices"][0]["message"]["content"].strip()
            
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            new_tasks = json.loads(content)
            if not isinstance(new_tasks, list):
                new_tasks = [new_tasks]
            return new_tasks
        except Exception:
            # Fallback recovery task
            return [
                {
                    "id": f"recovery-{failed_task['id']}",
                    "name": "Fallback error handler",
                    "depends_on": [],
                    "worker": "codeworker",
                    "args": {"task_description": f"Investigate test or shell command failures: {error_msg}"}
                }
            ]
