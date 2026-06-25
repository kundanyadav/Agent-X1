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
    """Background task polling ADO, creating branches, executing tasks, and opening PRs."""
    print(f"[*] Starting DevOps backlog sync...")
    try:
        engine, ado_client = load_resources(config_path)
    except Exception as e:
        print(f"[!] Failed to initialize sync resources: {e}")
        return
        
    if not ado_client:
        print("[!] Azure DevOps client is not configured in config.yaml. Skipping sync.")
        return

    # 1. Poll backlog for items assigned to Agent-X1 in "To Do" state
    wiql = (
        "SELECT [System.Id], [System.Title], [System.Description] "
        "FROM WorkItems "
        "WHERE [System.AssignedTo] = 'Agent-X1' "
        "AND [System.State] = 'To Do'"
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
            ado_client.update_work_item_state(wi_id, "Doing")
            
            # Start execution session
            correlation_id = engine.generate_correlation_id()
            engine.memory.start_session(correlation_id, f"Resolve ADO work item #{wi_id}")
            
            # 3. Create Git Feature Branch
            branch_name = f"feature/task-{wi_id}"
            engine.devops_worker.execute_task(
                operation="git_branch",
                params={"branch_name": branch_name, "justification": f"Create branch for work item #{wi_id}"},
                correlation_id=correlation_id
            )
            
            # 4. Decompose and execute goal task DAG
            # The goal is to resolve the described requirement
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
                        "target_branch": "main",
                        "title": f"Merge feature: {title} (Resolves #{wi_id})",
                        "description": f"Automated pull request resolving Work Item #{wi_id}.\nCorrelation ID: {correlation_id}"
                    },
                    correlation_id=correlation_id
                )
                
                # 7. Update Kanban state to "PR Raised" (or Closed)
                ado_client.update_work_item_state(wi_id, "PR Raised")
                print(f"[+] Successfully completed and submitted PR for Work Item #{wi_id}")
                
            else:
                print(f"[!] Goal execution failed with status: {status}. Leaving branch intact.")
                
            engine.memory.end_session(correlation_id, status)
            
        except Exception as ex:
            print(f"[!] Error processing work item #{wi_id}: {ex}")
            # Try to roll back state if failed
            try:
                ado_client.update_work_item_state(wi_id, "To Do")
            except Exception:
                pass

def run_workspace_tests(config_path: str = "config.yaml"):
    """Scheduled task executing test suite."""
    print(f"[*] Starting nightly tests run...")
    try:
        engine, _ = load_resources(config_path)
        # Execute tests via TestWorker
        res = engine.test_worker.execute_task(
            test_command="python3 -m unittest discover -s tests",
            correlation_id=engine.generate_correlation_id()
        )
        print(f"[*] Tests run status: {res.get('status')}")
    except Exception as e:
        print(f"[!] Nightly test task failed: {e}")

def consolidate_memories(config_path: str = "config.yaml"):
    """Scheduled task summarizing learnings."""
    print(f"[*] Starting memory consolidation...")
    # Consolidation logic: summarize actions history and write facts
    pass
