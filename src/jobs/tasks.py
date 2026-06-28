import os
import pathlib
import datetime
from typing import Dict, Any, List
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.integrations.ado import AzureDevOpsClient
from src.core.orchestrator import OrchestrationEngine

def load_resources(config_path: str = "config.yaml"):
    """Loads all engine resources and clients."""
    router = InferenceRouter(config_path=config_path)
    storage_cfg = router.config.get("storage", {})
    db_path = storage_cfg.get("db_path", "tmp/memory.db")
    audit_path = storage_cfg.get("audit_log_path", "logs/audit_lineage.jsonl")
    encrypt_logs = router.config.get("security", {}).get("encrypt_logs", False)
    
    from src.audit.lineage import LineageLogger
    lineage = LineageLogger(log_path=audit_path, encrypt=encrypt_logs)
    memory = MemoryManager(db_path=db_path, router=router)
    tools = ToolRunner(lineage_logger=lineage, memory_manager=memory)
    
    # Load ADO configuration
    ado_cfg = router.config.get("ado", {})
    ado_client = None
    if ado_cfg and ado_cfg.get("organization") != "your-organization":
        ado_client = AzureDevOpsClient(
            organization=ado_cfg.get("organization"),
            project=ado_cfg.get("project"),
            repository_id=ado_cfg.get("repository_id"),
            personal_access_token=ado_cfg.get("personal_access_token")
        )
        
    engine = OrchestrationEngine(
        router=router,
        tools=tools,
        memory=memory,
        ado_client=ado_client
    )
    return engine, ado_client

def sync_devops_backlog(config_path: str = "config.yaml"):
    """Background task polling ADO, creating branches, executing tasks, and opening PRs using configuration mapping."""
    print(f"[*] Starting DevOps backlog sync...")
    try:
        engine, ado_client = load_resources(config_path)
    except Exception as e:
        print(f"[!] Failed to initialize sync resources: {e}")
        return
        
    if not ado_client:
        print("[!] Azure DevOps client is not configured in config.yaml. Skipping sync.")
        return

    # Load ADO mappings from configuration
    ado_cfg = engine.router.config.get("ado", {})
    target_branch = ado_cfg.get("target_branch", "main")
    branch_prefix = ado_cfg.get("branch_prefix", "feature/task-")
    state_cfg = ado_cfg.get("state_mapping", {})
    
    state_todo = state_cfg.get("backlog", "To Do")
    state_doing = state_cfg.get("in_progress", "Doing")
    state_pr_raised = state_cfg.get("review_required", "PR Raised")

    # 1. Poll backlog for items assigned to Agent-X1 in the TODO state
    wiql = (
        "SELECT [System.Id], [System.Title], [System.Description] "
        "FROM WorkItems "
        "WHERE [System.AssignedTo] = 'Agent-X1' "
        f"AND [System.State] = '{state_todo}'"
    )
    
    try:
        work_items = ado_client.query_backlog(wiql)
    except Exception as e:
        print(f"[!] Error querying ADO backlog: {e}")
        return

    print(f"[*] Discovered {len(work_items)} pending work items.")

    for item in work_items:
        wi_id = item["id"]
        
        try:
            # Fetch full details
            wi_details = ado_client.get_work_item(wi_id)
            fields = wi_details.get("fields", {})
            title = fields.get("System.Title", "DevOps task")
            description = fields.get("System.Description", "Automated code edits")
            
            print(f"[*] Processing Work Item #{wi_id}: {title}")
            
            # 2. Update Kanban state to "Doing"
            ado_client.update_work_item_state(wi_id, state_doing)
            
            # Start execution session
            correlation_id = engine.generate_correlation_id()
            engine.memory.start_session(correlation_id, f"Resolve ADO work item #{wi_id}")
            
            # 3. Create Git Feature Branch
            branch_name = f"{branch_prefix}{wi_id}"
            engine.devops_worker.execute_task(
                operation="git_branch",
                params={"branch_name": branch_name, "justification": f"Create branch for work item #{wi_id}"},
                correlation_id=correlation_id
            )
            
            # 4. Decompose and execute goal task DAG
            goal_prompt = f"Implement work item changes: {title}. Details: {description}"
            tasks = engine.decompose_goal(goal_prompt)
            
            # Execute tasks (Code edits and Test verifications)
            status = engine.execute_loop(correlation_id, tasks, auto_approve=True)
            
            if status == "completed":
                # 5. Commit and Push
                commit_msg = f"feat: resolve work item #{wi_id} - {title}"
                commit_res = engine.devops_worker.execute_task(
                    operation="git_commit",
                    params={"message": commit_msg, "files": ".", "justification": f"Commit solution for task #{wi_id}"},
                    correlation_id=correlation_id
                )
                
                # Push branch
                engine.devops_worker.execute_task(
                    operation="git_push",
                    params={"branch_name": branch_name, "justification": f"Push feature branch to remote origin"},
                    correlation_id=correlation_id
                )
                
                # 6. Raise Pull Request
                pr_res = engine.devops_worker.execute_task(
                    operation="ado_create_pr",
                    params={
                        "source_branch": branch_name,
                        "target_branch": target_branch,
                        "title": f"Merge feature: {title} (Resolves #{wi_id})",
                        "description": f"Automated pull request resolving Work Item #{wi_id}.\nCorrelation ID: {correlation_id}"
                    },
                    correlation_id=correlation_id
                )
                
                # 7. Update Kanban state to "PR Raised"
                ado_client.update_work_item_state(wi_id, state_pr_raised)
                print(f"[+] Successfully completed and submitted PR for Work Item #{wi_id}")
                
            else:
                print(f"[!] Goal execution failed with status: {status}. Leaving branch intact.")
                
            engine.memory.end_session(correlation_id, status)
            
        except Exception as ex:
            print(f"[!] Error processing work item #{wi_id}: {ex}")
            # Try to roll back state if failed
            try:
                ado_client.update_work_item_state(wi_id, state_todo)
            except Exception:
                pass

def run_workspace_tests(config_path: str = "config.yaml"):
    """Scheduled task executing test suite."""
    print(f"[*] Starting nightly tests run...")
    import sys
    try:
        engine, _ = load_resources(config_path)
        # Execute tests via TestWorker
        res = engine.test_worker.execute_task(
            test_command=f"{sys.executable} -m unittest discover -s tests",
            correlation_id=engine.generate_correlation_id()
        )
        print(f"[*] Tests run status: {res.get('status')}")
    except Exception as e:
        print(f"[!] Nightly test task failed: {e}")

def consolidate_memories(config_path: str = "config.yaml"):
    """Scheduled task summarizing learnings from episodic memories into semantic memories."""
    print(f"[*] Starting memory consolidation...")
    import sqlite3
    import json
    
    try:
        engine, _ = load_resources(config_path)
    except Exception as e:
        print(f"[!] Failed to initialize consolidation resources: {e}")
        return

    db_path = engine.memory.db_path
    if not os.path.exists(db_path):
        print("[*] No database found for memory consolidation.")
        return

    try:
        with sqlite3.connect(db_path, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT session_id, goal, status FROM sessions")
            sessions = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"[!] Database access error: {e}")
        return

    print(f"[*] Found {len(sessions)} sessions to consolidate.")
    consolidated_count = 0

    for s in sessions:
        session_id = s["session_id"]
        goal = s["goal"]
        status = s["status"]
        
        # Avoid duplicate consolidation for same session
        try:
            with sqlite3.connect(db_path, timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM semantic_memory WHERE issue LIKE ?", (f"%{session_id}%",))
                exists = cursor.fetchone()[0] > 0
                if exists:
                    continue
        except Exception:
            pass

        history = engine.memory.get_session_history(session_id)
        if not history:
            continue

        # Format history log for LLM
        history_str = f"Session Goal: {goal}\nSession Status: {status}\n\nExecution Logs:\n"
        for act in history:
            history_str += f"- Tool: {act.get('tool_called')} (Status: {act.get('status')})\n"
            if act.get("arguments"):
                history_str += f"  Args: {act.get('arguments')}\n"
            if act.get("stderr"):
                history_str += f"  Error: {act.get('stderr')}\n"
            if act.get("stdout"):
                history_str += f"  Output: {act.get('stdout')[:200]}...\n"

        system_prompt = (
            "You are the Memory Consolidator of Agent-X1.\n"
            "Analyze the following execution history of a session to distill a key lesson or fact.\n"
            "Provide the output in raw JSON format with the following keys:\n"
            "  - \"category\": string (e.g., \"compilation_fix\", \"git_resolve\", \"api_mismatch\")\n"
            "  - \"issue\": brief description of the issue faced (must mention the session_id)\n"
            "  - \"solution\": detail of how it was resolved or should be handled\n"
            "If no clear lesson or fact is found, return an empty dictionary {}."
        )

        user_prompt = f"Session ID: {session_id}\n\n{history_str}"

        try:
            resp = engine.router.chat_completions(
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

            fact = json.loads(content)
            category = fact.get("category")
            issue = fact.get("issue")
            solution = fact.get("solution")

            if category and issue and solution:
                engine.memory.learn_fact(
                    agent_owner="orchestrator",
                    category=category,
                    issue=f"{issue} (Session: {session_id})",
                    solution=solution
                )
                consolidated_count += 1
                print(f"[+] Consolidated memory for session {session_id}: {category}")
        except Exception as ex:
            print(f"[!] Error consolidating session {session_id}: {ex}")

    print(f"[*] Memory consolidation finished. Consolidated {consolidated_count} sessions.")

def run_scheduled_goal(config_path: str = "config.yaml"):
    """Locates and runs the most recently scheduled goal configuration."""
    import glob
    import json
    import pathlib
    files = sorted(glob.glob("tmp/scheduled_goal_*.json"))
    if not files:
        print("[!] No scheduled goals found in tmp/")
        return
        
    target_file = files[-1]
    print(f"[*] Loading scheduled goal from: {target_file}")
    try:
        with open(target_file, "r") as f:
            data = json.load(f)
        goal = data.get("goal")
        correlation_id = data.get("correlation_id")
        tasks = data.get("tasks", [])
        
        print(f"[*] Running scheduled goal: {goal}")
        engine, _ = load_resources(config_path)
        engine.execute_loop(correlation_id, tasks)
    except Exception as e:
        print(f"[!] Error executing scheduled goal: {e}")
