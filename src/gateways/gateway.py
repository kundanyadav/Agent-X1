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

    def run_goal(self, goal: str, auto_approve: bool = True, dry_run: bool = False) -> str:
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
            
        print("[*] Starting execution loop...")
        status = engine.execute_loop(correlation_id, tasks, auto_approve=auto_approve)
        
        print(f"\n[*] Execution loop completed with status: {status}")
        print(f"[*] Audit logs saved at: {audit_path}")
        return status

def main():
    parser = argparse.ArgumentParser(description="Agent-X1 CLI Gateway")
    parser.add_argument("--goal", type=str, required=True, help="The goal for Agent-X1 to achieve")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config.yaml file")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Auto approve Major task shifts")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying files")
    
    args = parser.parse_args()
    
    gateway = CliGateway(config_path=args.config)
    status = gateway.run_goal(args.goal, auto_approve=args.auto_approve, dry_run=args.dry_run)
    
    sys.exit(0 if status == "completed" else 1)

if __name__ == "__main__":
    main()
