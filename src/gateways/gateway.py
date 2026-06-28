import sys
import argparse
import pathlib
from typing import Optional
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.core.orchestrator import OrchestrationEngine

class CliGateway:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path

    def save_scheduled_job(self, goal: str, cron: str, correlation_id: str):
        """Appends the scheduled job to jobs.yaml and writes the goal config to tmp/scheduled_goal_<id>.json."""
        import yaml
        # 1. Save config to tmp/scheduled_goal_<id>.json
        job_id = correlation_id[:8]
        pathlib.Path("tmp").mkdir(parents=True, exist_ok=True)
        job_file = pathlib.Path("tmp") / f"scheduled_goal_{job_id}.json"
        
        try:
            router = InferenceRouter(config_path=self.config_path)
            memory = MemoryManager(db_path=router.config.get("storage", {}).get("db_path", "tmp/memory.db"), router=router)
            engine = OrchestrationEngine(router=router, tools=None, memory=memory)
            tasks = engine.decompose_plan_into_tasks(f"Goal: {goal}")
        except Exception as e:
            print(f"[!] Warning: Failed to pre-decompose goal during scheduling: {e}")
            tasks = []
            
        job_data = {
            "correlation_id": correlation_id,
            "goal": goal,
            "cron": cron,
            "tasks": tasks
        }
        with open(job_file, "w") as f:
            import json
            json.dump(job_data, f, indent=2)
            
        # 2. Append new job to jobs.yaml
        jobs_path = pathlib.Path("jobs.yaml")
        jobs_list = []
        if jobs_path.exists():
            try:
                with open(jobs_path, "r") as f:
                    yaml_data = yaml.safe_load(f) or {}
                    jobs_list = yaml_data.get("jobs", [])
            except Exception as e:
                print(f"[!] Warning: Failed to read jobs.yaml: {e}")
                
        job_name = f"scheduled_goal_{job_id}"
        new_job = {
            "name": job_name,
            "cron": cron,
            "task": "tasks.run_scheduled_goal",
            "description": f"Scheduled execution of goal: {goal}"
        }
        jobs_list.append(new_job)
        
        try:
            with open(jobs_path, "w") as f:
                yaml.safe_dump({"jobs": jobs_list}, f, default_flow_style=False)
            print(f"[+] Successfully scheduled! Job '{job_name}' written to jobs.yaml.")
        except Exception as e:
            print(f"[!] Error: Failed to write to jobs.yaml: {e}")

    def interactive_planning_loop(self, goal: str, engine: OrchestrationEngine) -> str:
        """Runs a back-and-forth interactive CLI chat loop to refine the implementation plan."""
        print("\n=== Entering Interactive Planning Phase ===")
        print(f"Goal: {goal}")
        
        history = []
        scheduled_cron = None
        
        # Get first proposal
        print("\n[*] Generating initial proposal...")
        proposal = engine.generate_planning_proposal(goal, history)
        history.append({"role": "assistant", "content": proposal})
        
        while True:
            print("\n" + "=" * 60)
            print(proposal)
            print("=" * 60 + "\n")
            if scheduled_cron:
                print(f"[Scheduled]: Job will be scheduled to run on: '{scheduled_cron}'")
            
            print("Options:")
            print("  - Type your feedback to refine the plan.")
            print("  - Type '/btw <question>' to ask a question without changing the plan.")
            print("  - Type '/compact' to distill conversation history and save tokens.")
            print("  - Type '/goal <new_goal>' to pivot and reset the session.")
            print("  - Type '/schedule \"<cron>\"' to schedule execution.")
            print("  - Type '/pin [name]' to save current session context.")
            print("  - Type '/resume' to choose and restore a pinned session.")
            print("  - Type 'approved for build' to approve the plan and start execution.")
            print("  - Type '/exit' to cancel.")
            
            try:
                user_input = input("\nYour feedback/decision: ").strip()
            except KeyboardInterrupt:
                print("\n[!] Execution aborted by keyboard interrupt.")
                return ""
                
            if not user_input:
                continue
                
            # Slash Command Parsing
            if user_input.startswith("/"):
                parts = user_input.split(" ", 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                
                if cmd == "/exit":
                    print("\n[!] Execution aborted.")
                    return ""
                elif cmd == "/pin":
                    pin_name = arg
                    if not pin_name:
                        import time
                        pin_name = f"pin_{int(time.time())}"
                    try:
                        engine.memory.pin_session(pin_name, goal, history, scheduled_cron)
                        print(f"[+] Pinned session saved as '{pin_name}'.")
                    except Exception as e:
                        print(f"[!] Error pinning session: {e}")
                    continue
                elif cmd == "/resume":
                    try:
                        pinned = engine.memory.get_pinned_sessions()
                        if not pinned:
                            print("[!] No pinned sessions found.")
                            continue
                        
                        print("\nAvailable pinned sessions:")
                        for idx, session in enumerate(pinned, 1):
                            name = session.get("name")
                            session_goal = session.get("goal")
                            ts = session.get("timestamp")
                            import datetime
                            dt_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                            print(f"  {idx}. {name} - Goal: {session_goal} (Pinned: {dt_str})")
                        
                        choice_input = input("\nChoose a session number or name to resume (or type '/exit' to cancel): ").strip()
                        if not choice_input or choice_input.lower() == "/exit":
                            print("[*] Resume cancelled.")
                            continue
                            
                        target_session = None
                        try:
                            choice_idx = int(choice_input) - 1
                            if 0 <= choice_idx < len(pinned):
                                target_session = pinned[choice_idx]
                        except ValueError:
                            pass
                            
                        if not target_session:
                            for session in pinned:
                                if session.get("name") == choice_input:
                                    target_session = session
                                    break
                                    
                        if not target_session:
                            print("[!] Error: Invalid selection or name.")
                            continue
                            
                        name = target_session.get("name")
                        full_context = engine.memory.get_pinned_session(name)
                        if not full_context:
                            print(f"[!] Error: Failed to load pinned session '{name}'.")
                            continue
                            
                        goal = full_context.get("goal")
                        history = full_context.get("history", [])
                        scheduled_cron = full_context.get("scheduled_cron")
                        
                        proposal = "No plan proposal found. Please type '/compact' or write feedback to generate one."
                        for msg in reversed(history):
                            if msg.get("role") == "assistant":
                                proposal = msg.get("content")
                                break
                                
                        print(f"\n[+] Resumed session '{name}' successfully.")
                        print(f"[*] Restored Goal: '{goal}'")
                        if scheduled_cron:
                            print(f"[*] Restored Schedule: '{scheduled_cron}'")
                            
                    except Exception as e:
                        print(f"[!] Error resuming session: {e}")
                    continue
                elif cmd == "/compact":
                    print("\n[*] Distilling and compacting chat history to save tokens...")
                    history = engine.compact_planning_history(goal, history)
                    print("[*] Regenerating proposal based on compacted history...")
                    proposal = engine.generate_planning_proposal(goal, history)
                    history.append({"role": "assistant", "content": proposal})
                    print(f"[+] Chat history successfully compacted! New history contains {len(history)} items.")
                    continue
                elif cmd == "/goal":
                    if not arg:
                        print("[!] Error: Please specify a new goal after /goal.")
                        continue
                    print(f"\n[*] Pivoting goal to: {arg}")
                    goal = arg
                    history = []
                    print("[*] Generating new proposal...")
                    proposal = engine.generate_planning_proposal(goal, history)
                    history.append({"role": "assistant", "content": proposal})
                    continue
                elif cmd == "/schedule":
                    if not arg:
                        print("[!] Error: Please specify a cron expression after /schedule.")
                        continue
                    cron_expr = arg.strip('"').strip("'")
                    scheduled_cron = cron_expr
                    print(f"[+] Goal set to run on schedule: '{cron_expr}' (will be written to jobs.yaml on approval).")
                    continue
                elif cmd == "/btw":
                    if not arg:
                        print("[!] Error: Please specify a question after /btw.")
                        continue
                    print(f"\n[*] Running secondary Q/A thread for: '{arg}'...")
                    answer = engine.answer_planning_qa(arg)
                    print("\n" + "-" * 60)
                    print(f"[BTW Q&A Answer]\n{answer}")
                    print("-" * 60 + "\n")
                    continue
                else:
                    print(f"[!] Unknown slash command: {cmd}")
                    continue
                    
            if user_input.lower() == "approved for build":
                print("\n[+] Plan approved!")
                if scheduled_cron:
                    correlation_id = engine.generate_correlation_id()
                    self.save_scheduled_job(goal, scheduled_cron, correlation_id)
                    return "scheduled"
                return proposal
            elif user_input.lower() in ["abort", "no", "n"]:
                print("\n[!] Execution aborted.")
                return ""
            
            # Check if user input contains important preferences to save to memory
            engine.learn_user_fact_if_needed(user_input)
            
            # User provided feedback
            print("\n[*] Refining plan based on feedback...")
            history.append({"role": "user", "content": user_input})
            proposal = engine.generate_planning_proposal(goal, history)
            history.append({"role": "assistant", "content": proposal})

    def run_goal(self, goal: str, auto_approve: bool = True, dry_run: bool = False, interactive: bool = False) -> str:
        """Initializes dependencies and runs the orchestrator execution loop for a goal."""
        print(f"[*] Initializing Agent-X1 Harness with config: {self.config_path}")
        
        try:
            router = InferenceRouter(config_path=self.config_path)
        except Exception as e:
            print(f"[!] Error loading config: {e}")
            return "failed"
            
        storage_cfg = router.config.get("storage", {})
        db_path = storage_cfg.get("db_path", "tmp/memory.db")
        audit_path = storage_cfg.get("audit_log_path", "logs/audit_lineage.jsonl")
        encrypt_logs = router.config.get("security", {}).get("encrypt_logs", False)
        
        # Load Audit and Memory managers
        from src.audit.lineage import LineageLogger
        lineage = LineageLogger(log_path=audit_path, encrypt=encrypt_logs)
        memory = MemoryManager(db_path=db_path, router=router)
        tools = ToolRunner(lineage_logger=lineage, memory_manager=memory)
        
        engine = OrchestrationEngine(router=router, tools=tools, memory=memory)
        
        correlation_id = engine.generate_correlation_id()
        print(f"[*] Generated correlation ID: {correlation_id}")
        
        if interactive:
            finalized_plan = self.interactive_planning_loop(goal, engine)
            if not finalized_plan:
                return "aborted"
            if finalized_plan == "scheduled":
                return "completed"
            print("[*] Decomposing finalized plan into task DAG...")
            tasks = engine.decompose_plan_into_tasks(finalized_plan)
        else:
            if dry_run:
                print("[*] Running in DRY-RUN simulation mode.")
                tasks = [
                    {
                        "id": "t-dry-1",
                        "name": "Dry run analysis",
                        "depends_on": [],
                        "worker": "codeworker",
                        "args": {"task_description": f"Simulating goal: {goal}"}
                    }
                ]
            else:
                print("[*] Decomposing goal into tasks...")
                tasks = engine.decompose_goal(goal)
            
        print("\n=== GENERATED TASK DAG ===")
        for t in tasks:
            deps = f" (depends on: {', '.join(t['depends_on'])})" if t.get("depends_on") else ""
            print(f"- [{t['id']}] {t['name']} - Worker: {t['worker']}{deps}")
        print("==========================\n")
        
        if dry_run:
            print("[*] Dry run completed successfully.")
            return "completed"

        # Goal Planning Gate
        if auto_approve:
            engine.write_tasks_plan(goal, tasks, correlation_id, status="Approved")
        else:
            engine.write_tasks_plan(goal, tasks, correlation_id, status="Awaiting Approval")
            print(f"[*] Goal Planning Gate: Task execution plan written to tasks_plan.md.")
            print(f"[*] You can approve by checking the box in the file and saving it, or via console.")
            
            user_input = input("Do you approve the execution plan? (y/n): ").strip().lower()
            if user_input == "y" or engine.check_plan_file_approved():
                print("[*] Plan approved. Resuming execution...")
                engine.update_tasks_plan_status(status="Approved")
            else:
                print("[!] Plan rejected. Aborting execution.")
                engine.update_tasks_plan_status(status="Aborted")
                return "aborted"
            
        print("[*] Starting execution loop...")
        status = engine.execute_loop(correlation_id, tasks, auto_approve=auto_approve)
        
        print(f"\n[*] Execution loop completed with status: {status}")
        print(f"[*] Audit logs saved at: {audit_path}")
        return status

def main():
    parser = argparse.ArgumentParser(description="Agent-X1 CLI Gateway")
    parser.add_argument("--goal", type=str, help="The goal for Agent-X1 to achieve")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config.yaml file")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Auto approve Major task shifts")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying files")
    parser.add_argument("--interactive", "-i", action="store_true", help="Enable back-and-forth interactive planning chat")
    parser.add_argument("--chat", "-c", action="store_true", help="Start conversational chat mode with the agent")
    
    args = parser.parse_args()
    
    interactive = args.interactive or args.chat
    goal = args.goal
    
    if not goal:
        if interactive:
            try:
                goal = input("Please enter your goal: ").strip()
            except KeyboardInterrupt:
                print("\n[!] Exiting.")
                sys.exit(0)
            if not goal:
                print("[!] Error: A goal is required to run the agent.")
                sys.exit(1)
        else:
            parser.error("the following arguments are required: --goal (or run interactively with --interactive or --chat)")
            
    gateway = CliGateway(config_path=args.config)
    status = gateway.run_goal(goal, auto_approve=args.auto_approve, dry_run=args.dry_run, interactive=interactive)
    
    sys.exit(0 if status == "completed" else 1)

if __name__ == "__main__":
    main()
