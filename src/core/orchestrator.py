import json
import uuid
import time
import pathlib
import threading
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

    def write_tasks_plan(
        self,
        goal: str,
        tasks: List[Dict[str, Any]],
        correlation_id: str,
        status: str = "Awaiting Approval",
        plan_path: str = "tasks_plan.md"
    ) -> None:
        """Writes a structured markdown task execution plan to the workspace."""
        tasks_list = ""
        for t in tasks:
            deps = f" (depends on: {', '.join(t.get('depends_on', []))})" if t.get("depends_on") else ""
            args_str = json.dumps(t.get("args", {}))
            tasks_list += f"- [ ] **{t.get('id', 'task-id')}**: {t.get('name', 'Unnamed task')} (Worker: {t.get('worker', 'unknown')}){deps}\n  - Args: `{args_str}`\n"

        approved_checkbox = "[x]" if "Approved" in status or "Completed" in status else "[ ]"

        content = (
            f"# Agent-X1 Task Execution Plan\n"
            f"**Correlation ID**: {correlation_id}\n"
            f"**Goal**: {goal}\n"
            f"**Status**: {status}\n\n"
            f"## Decomposed Tasks\n"
            f"{tasks_list}\n"
            f"## Approval Instructions\n"
            f"To approve this plan, change the checkbox below from `[ ]` to `[x]` and save this file, "
            f"or approve through your active gateway (CLI / Teams / API).\n\n"
            f"- {approved_checkbox} I approve this plan and authorize Agent-X1 to execute these tasks.\n"
        )
        if self.tools:
            try:
                self.tools.write_file(plan_path, content, correlation_id, "orchestrator", "Write initial task plan to workspace")
            except Exception:
                # Fallback to direct write if tool is not fully set up or fails
                p = pathlib.Path(plan_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
        else:
            p = pathlib.Path(plan_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def check_plan_file_approved(self, plan_path: str = "tasks_plan.md") -> bool:
        """Checks if the user has approved the plan inside the tasks_plan.md file."""
        p = pathlib.Path(plan_path)
        if not p.is_file():
            return False
        try:
            content = p.read_text(encoding="utf-8")
            return "- [x] i approve" in content.lower()
        except Exception:
            return False

    def update_tasks_plan_status(self, plan_path: str = "tasks_plan.md", status: str = "Completed") -> None:
        """Updates the status line in the tasks_plan.md file."""
        p = pathlib.Path(plan_path)
        if not p.is_file():
            return
        try:
            content = p.read_text(encoding="utf-8")
            import re
            content = re.sub(r"\*\*Status\*\*:\s*.*", f"**Status**: {status}", content)
            if status in ["Completed", "Approved"]:
                content = content.replace("- [ ] I approve", "- [x] I approve")
            elif status in ["Aborted", "Rejected"]:
                content = content.replace("- [x] I approve", "- [ ] I approve")
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"[!] Failed to update tasks_plan.md status: {e}")

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
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"[!] Warning: Failed to parse LLM goal decomposition JSON: {e}. Falling back to default analysis task.")
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

    def execute_loop(
        self,
        correlation_id: str,
        tasks: List[Dict[str, Any]],
        auto_approve: bool = True,
        plan_path: str = "tasks_plan.md",
        approval_event: Optional[threading.Event] = None,
        active_session: Optional[Dict[str, Any]] = None
    ) -> str:
        """Runs the state machine loop, executing ready tasks in the DAG and handling failures/re-planning."""
        task_states = {t["id"]: "pending" for t in tasks}
        task_map = {t["id"]: t for t in tasks}
        task_retries = {t["id"]: 0 for t in tasks}
        max_retries = 3

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
                    self.update_tasks_plan_status(plan_path, "Deadlocked")
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
                    self.update_tasks_plan_status(plan_path, "Paused for Action Approval")
                    
                    import sys
                    if approval_event:
                        approval_event.clear()
                        print(f"[*] Task execution paused. Awaiting approval for task: {task.get('name')}")
                        if active_session:
                            active_session["status"] = "paused_for_task_approval"
                        signaled = approval_event.wait()
                        
                        decision = "rejected"
                        if active_session:
                            decision = active_session.get("approval_decision")
                            
                        if signaled and decision == "approved":
                            print(f"[*] Task approved. Continuing execution...")
                            task_states[t_id] = "running"
                            if active_session:
                                active_session["status"] = "running"
                                active_session["approval_decision"] = None
                        else:
                            print(f"[!] Task rejected. Aborting execution loop.")
                            self.update_tasks_plan_status(plan_path, "Aborted")
                            return "failed"
                    elif sys.stdin.isatty():
                        user_input = input(f"Task '{task.get('name')}' is a MAJOR change. Do you approve execution? (y/n): ").strip().lower()
                        if user_input == "y":
                            print("[*] Task approved. Continuing...")
                            task_states[t_id] = "running"
                        else:
                            print("[!] Task rejected. Aborting.")
                            self.update_tasks_plan_status(plan_path, "Aborted")
                            return "failed"
                    else:
                        # Fallback for non-interactive unit test runs
                        return "paused_for_approval"

                if task_states[t_id] == "blocked":
                    # If it was blocked and not resumed, skip/continue
                    continue

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
                            self.update_tasks_plan_status(plan_path, "Failed")
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
            self.update_tasks_plan_status(plan_path, "Completed")
            return "completed"
        self.update_tasks_plan_status(plan_path, "Failed")
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
        
        try:
            new_tasks = json.loads(content)
            if not isinstance(new_tasks, list):
                new_tasks = [new_tasks]
            return new_tasks
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"[!] Warning: Failed to parse LLM replan JSON: {e}. Falling back to default recovery task.")
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

    def generate_planning_proposal(self, goal: str, history: List[Dict[str, str]]) -> str:
        """Generates or refines a proposed implementation plan and DAG tasks based on conversation history."""
        if not history:
            system_prompt = (
                "You are the Manager Orchestrator of Agent-X1.\n"
                "Your job is to work with the user to design an Implementation Plan and define a Proposed DAG of Tasks to achieve their goal.\n"
                "You should respond with a clear, detailed Markdown document containing:\n"
                "1. **Implementation Plan**: High-level design, architectural decisions, and files to modify/create.\n"
                "2. **Proposed DAG Tasks**: A list of tasks showing:\n"
                "   - Task ID (e.g. task-1, task-2)\n"
                "   - Task Name\n"
                "   - Worker (codeworker, testworker, or devopsworker)\n"
                "   - Description of what the worker will do\n"
                "   - Dependencies (which task IDs must complete first)\n\n"
                "Keep the proposal clean, structured, and easy to read.\n"
                "If the user provides feedback, adjust the design and the tasks accordingly."
            )
            history.append({"role": "system", "content": system_prompt})
            history.append({"role": "user", "content": f"Please propose an implementation plan and DAG tasks for the goal: {goal}"})
            
        resp = self.router.chat_completions(messages=history, temperature=0.2)
        return resp["choices"][0]["message"]["content"].strip()

    def decompose_plan_into_tasks(self, finalized_plan: str) -> List[Dict[str, Any]]:
        """Takes the finalized implementation plan markdown and decomposes it into a structured JSON task DAG."""
        system_prompt = (
            "You are the Manager Orchestrator of Agent-X1.\n"
            "Your task is to take the finalized Implementation Plan and Proposed Tasks, and translate them into a structured JSON list of task dictionaries.\n"
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
            {"role": "user", "content": f"Finalized Plan:\n{finalized_plan}"}
        ]
        
        resp = self.router.chat_completions(messages=messages, temperature=0.1)
        content = resp["choices"][0]["message"]["content"].strip()
        
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        try:
            tasks = json.loads(content)
            if not isinstance(tasks, list):
                tasks = [tasks]
            return tasks
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"[!] Warning: Failed to parse LLM plan decomposition JSON: {e}. Falling back to default task list.")
            return [
                {
                    "id": "task-fallback-plan-1",
                    "name": "Execute finalized plan",
                    "depends_on": [],
                    "worker": "codeworker",
                    "args": {"task_description": f"Implement the finalized plan: {finalized_plan[:200]}..."}
                }
            ]

    def learn_user_fact_if_needed(self, user_input: str) -> bool:
        """Scans user input for key trigger phrases and logs useful insights/preferences to semantic memory."""
        triggers = ["important", "interesting", "keep in mind", "let's keep in mind", "useful", "remember", "make sure to", "note that"]
        user_input_lower = user_input.lower()
        if any(trigger in user_input_lower for trigger in triggers):
            try:
                self.memory.learn_fact(
                    agent_owner="orchestrator",
                    category="user_preference",
                    issue="user preference during planning",
                    solution=user_input
                )
                print("[+] Saved important context to semantic memory.")
                return True
            except Exception as e:
                print(f"[!] Warning: Failed to write user fact to memory: {e}")
        return False

    def answer_planning_qa(self, question: str) -> str:
        """Runs a secondary Q/A completions query using relevant facts from semantic memory."""
        # 1. Retrieve related facts from semantic memory
        relevant_memories = []
        try:
            relevant_memories = self.memory.query_semantic_memory("orchestrator", question, similarity_threshold=0.7)
        except Exception as e:
            print(f"[!] Warning: Failed to query semantic memory: {e}")
            
        memory_context = ""
        if relevant_memories:
            memory_context = "\nRelevant Context from Memory:\n"
            for mem in relevant_memories:
                memory_context += f"- Category: {mem.get('category')}, Fact: {mem.get('solution')}\n"
                
        # 2. Call LLM to generate the answer
        system_prompt = (
            "You are the Manager Orchestrator of Agent-X1.\n"
            "The user is asking a direct question during the planning phase.\n"
            "Provide a concise, helpful, and technically accurate answer based on the question and any provided context.\n"
            "Do NOT output plan code, just directly answer the question."
        )
        
        user_prompt = f"Question: {question}\n{memory_context}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            resp = self.router.chat_completions(messages=messages, temperature=0.2)
            answer = resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            answer = f"Failed to get answer from LLM: {e}"
            
        # 3. Save exchange to semantic memory
        try:
            self.memory.learn_fact(
                agent_owner="orchestrator",
                category="user_qa",
                issue=question,
                solution=answer
            )
            print("[+] Saved Q&A exchange to semantic memory.")
        except Exception as e:
            print(f"[!] Warning: Failed to log Q&A to memory: {e}")
            
        return answer

    def compact_planning_history(self, goal: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Summarizes and distills the back-and-forth planning history to lower token consumption."""
        compaction_prompt = (
            "You are the Manager Orchestrator of Agent-X1.\n"
            "Your job is to review the back-and-forth planning conversation so far, and consolidate it into a single, concise summary of agreed-upon design requirements, decisions, and constraints for the goal: \"{goal}\".\n"
            "Ensure that all key feedback, technical requirements, and decisions made by the user are preserved in this summary.\n"
            "Do NOT output plan code, just output the concise summary of the design decisions."
        ).format(goal=goal)
        
        messages = history + [{"role": "user", "content": compaction_prompt}]
        try:
            resp = self.router.chat_completions(messages=messages, temperature=0.1)
            distilled_summary = resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[!] Warning: Failed to compact history using LLM: {e}")
            distilled_summary = "User and agent are currently refining the plan. Please output a plan matching the user's latest goals."
            
        system_prompt = (
            "You are the Manager Orchestrator of Agent-X1.\n"
            "Your job is to work with the user to design an Implementation Plan and define a Proposed DAG of Tasks to achieve their goal.\n"
            "You should respond with a clear, detailed Markdown document containing:\n"
            "1. **Implementation Plan**: High-level design, architectural decisions, and files to modify/create.\n"
            "2. **Proposed DAG Tasks**: A list of tasks showing:\n"
            "   - Task ID (e.g. task-1, task-2)\n"
            "   - Task Name\n"
            "   - Worker (codeworker, testworker, or devopsworker)\n"
            "   - Description of what the worker will do\n"
            "   - Dependencies (which task IDs must complete first)\n\n"
            "Keep the proposal clean, structured, and easy to read.\n"
            "If the user provides feedback, adjust the design and the tasks accordingly."
        )
        
        new_history = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please propose an implementation plan and DAG tasks for the goal: {goal}"},
            {"role": "assistant", "content": f"Summary of current design agreements and constraints:\n{distilled_summary}"}
        ]
        return new_history
