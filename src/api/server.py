import os
import queue
import threading
import pathlib
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, Header, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.core.orchestrator import OrchestrationEngine

app = FastAPI(title="Agent-X1 Gateway REST API")

# Thread-safe execution queue
execution_queue = queue.Queue()
active_sessions: Dict[str, Dict[str, Any]] = {}

# Global engine references
router: Optional[InferenceRouter] = None
tools: Optional[ToolRunner] = None
memory: Optional[MemoryManager] = None
engine: Optional[OrchestrationEngine] = None

# Initialize resources
def init_resources(config_path: str = "config.yaml"):
    global router, tools, memory, engine, execution_queue, active_sessions
    
    # Reset queue and active sessions between test runs
    with execution_queue.mutex:
        execution_queue.queue.clear()
        execution_queue.unfinished_tasks = 0
    active_sessions.clear()
    
    router = InferenceRouter(config_path=config_path)
    # Extract storage paths from config
    storage_cfg = router.config.get("storage", {})
    db_path = storage_cfg.get("db_path", "tmp/memory.db")
    
    # Optional dependency loggers
    from src.audit.lineage import LineageLogger
    audit_path = storage_cfg.get("audit_log_path", "logs/audit_lineage.jsonl")
    encrypt_logs = router.config.get("security", {}).get("encrypt_logs", False)
    
    lineage = LineageLogger(log_path=audit_path, encrypt=encrypt_logs)
    memory = MemoryManager(db_path=db_path, router=router)
    tools = ToolRunner(lineage_logger=lineage, memory_manager=memory)
    engine = OrchestrationEngine(router=router, tools=tools, memory=memory)

# Auth dependency
async def verify_api_key(x_agent_api_key: Optional[str] = Header(None)):
    if not router:
        return
    expected_key = router.config.get("api", {}).get("api_key")
    
    if expected_key is not None:
        if not expected_key:
            raise HTTPException(status_code=401, detail="API Key is configured but unset in environment")
        if x_agent_api_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid API Key")

# Background thread worker loop
def queue_worker_loop():
    while True:
        try:
            task = execution_queue.get()
            if task is None:
                break
                
            session_id = task["session_id"]
            goal = task["goal"]
            
            active_sessions[session_id]["status"] = "decomposing"
            
            # Start session in database
            memory.start_session(session_id, goal)
            
            # Decompose goal
            tasks = engine.decompose_goal(goal)
            active_sessions[session_id]["tasks"] = tasks
            
            # Write initial task plan to workspace
            engine.write_tasks_plan(goal, tasks, session_id, status="Awaiting Approval")
            
            # Get gating settings
            gating_cfg = {}
            if router and router.config:
                gating_cfg = router.config.get("gating", {})
            auto_approve_plan = gating_cfg.get("auto_approve_planning", False)
            auto_approve_tasks = gating_cfg.get("auto_approve_tasks", auto_approve_plan)
            
            event = active_sessions[session_id].get("approval_event")
            
            approved = True
            if not auto_approve_plan:
                active_sessions[session_id]["status"] = "awaiting_approval"
                if event:
                    timeout = gating_cfg.get("timeout_hours", 1) * 3600
                    # Wait for user approval via POST /approve or Teams
                    signaled = event.wait(timeout=timeout)
                    
                    if not signaled:
                        # Timeout
                        active_sessions[session_id]["approval_decision"] = "rejected"
                        active_sessions[session_id]["status"] = "aborted"
                        engine.update_tasks_plan_status(status="Aborted due to timeout")
                        approved = False
                    elif active_sessions[session_id].get("approval_decision") != "approved":
                        active_sessions[session_id]["status"] = "aborted"
                        engine.update_tasks_plan_status(status="Rejected")
                        approved = False
                    else:
                        active_sessions[session_id]["status"] = "running"
                        engine.update_tasks_plan_status(status="Approved")
            else:
                active_sessions[session_id]["status"] = "running"
                engine.update_tasks_plan_status(status="Approved")
            
            if approved:
                # Execute loop (tasks execution)
                status = engine.execute_loop(
                    session_id,
                    tasks,
                    auto_approve=auto_approve_tasks,
                    plan_path="tasks_plan.md",
                    approval_event=event,
                    active_session=active_sessions[session_id]
                )
                # End session in database
                memory.end_session(session_id, status)
                active_sessions[session_id]["status"] = status
            else:
                memory.end_session(session_id, "aborted")
                
            execution_queue.task_done()
        except Exception as e:
            print(f"Error in background execution thread: {e}")
            if "session_id" in locals() and session_id in active_sessions:
                active_sessions[session_id]["status"] = "failed"
                active_sessions[session_id]["error"] = str(e)

# Start background thread
worker_thread = threading.Thread(target=queue_worker_loop, daemon=True)
worker_thread.start()


# --- Pydantic Models ---
class GoalRequest(BaseModel):
    goal: str

class ApprovalRequest(BaseModel):
    approved: bool
    notes: Optional[str] = None

class TeamsMessage(BaseModel):
    text: str
    from_user: str


# Helper to calculate queue position dynamically
def get_current_queue_position(session_id: str) -> int:
    """Returns the current position of a session in the execution queue.
    If the session is currently active/running/awaiting, returns 0.
    """
    if session_id not in active_sessions:
        return -1
    status = active_sessions[session_id].get("status")
    if status != "queued":
        return 0
    with execution_queue.mutex:
        items = list(execution_queue.queue)
    for index, item in enumerate(items):
        if item.get("session_id") == session_id:
            return index + 1
    return -1


# --- FastAPI Routes ---

@app.post("/v1/tasks/run", dependencies=[Depends(verify_api_key)])
async def submit_goal(request: GoalRequest):
    """Submits a new goal, queueing it for asynchronous processing."""
    if not engine:
        raise HTTPException(status_code=500, detail="Orchestration engine not initialized")
        
    session_id = engine.generate_correlation_id()
    queue_pos = execution_queue.qsize() + 1
    
    # Store initial session state
    active_sessions[session_id] = {
        "session_id": session_id,
        "goal": request.goal,
        "status": "queued",
        "tasks": [],
        "queue_position": queue_pos,
        "approval_event": threading.Event(),
        "approval_decision": None
    }
    
    # Enqueue task
    execution_queue.put({"session_id": session_id, "goal": request.goal})
    
    return {
        "task_id": session_id,
        "status": "queued",
        "queue_position": queue_pos,
        "message": f"Task queued successfully. Current queue position: {queue_pos}."
    }

@app.get("/v1/tasks/{task_id}/status", dependencies=[Depends(verify_api_key)])
async def get_status(task_id: str):
    """Returns the current execution status and tasks for a given session."""
    if task_id not in active_sessions:
        # Fallback: check database history
        if memory:
            history = memory.get_session_history(task_id)
            if history:
                return {
                    "task_id": task_id,
                    "status": "archived",
                    "tasks": history
                }
        raise HTTPException(status_code=404, detail="Task not found")
        
    # Return serializable dict
    res = dict(active_sessions[task_id])
    res.pop("approval_event", None)
    res["task_id"] = res.pop("session_id", task_id)
    res["queue_position"] = get_current_queue_position(task_id)
    return res

@app.post("/v1/tasks/{task_id}/approve", dependencies=[Depends(verify_api_key)])
async def approve_gated_task(task_id: str, request: ApprovalRequest):
    """Submits user decision for tasks paused on human-in-the-loop gates."""
    if task_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Task not found")
        
    session = active_sessions[task_id]
    session["approval_decision"] = "approved" if request.approved else "rejected"
    
    # Update tasks_plan.md status in workspace
    if engine:
        status_str = "Approved" if request.approved else "Rejected"
        engine.update_tasks_plan_status(status=status_str)
        
    # Signal resume to background worker thread
    event = session.get("approval_event")
    if event:
        event.set()
        
    return {
        "task_id": task_id,
        "approved": request.approved,
        "notes": request.notes,
        "status": "approval_registered"
    }

@app.post("/teams/webhook", dependencies=[Depends(verify_api_key)])
async def teams_webhook(message: TeamsMessage):
    """Receives incoming messages from MS Teams webhook integration."""
    # Run simple command extraction
    text = message.text.strip()
    
    if text.lower().startswith("goal:"):
        goal_text = text[5:].strip()
        session_id = engine.generate_correlation_id()
        
        active_sessions[session_id] = {
            "session_id": session_id,
            "goal": goal_text,
            "status": "queued",
            "tasks": [],
            "approval_event": threading.Event(),
            "approval_decision": None
        }
        
        execution_queue.put({"session_id": session_id, "goal": goal_text})
        return {"reply": f"Goal queued successfully. Session ID: {session_id}"}
        
    return {"reply": f"Received: '{text}'. Format message as 'goal: <your task>' to run."}

@app.get("/v1/skills", dependencies=[Depends(verify_api_key)])
async def get_skills():
    """Lists all dynamically learned skills from the skills directory."""
    home = pathlib.Path.home()
    skills_dir = home / ".agent-x1" / "skills"
    
    skills = []
    if skills_dir.is_dir():
        import yaml
        try:
            for yaml_file in skills_dir.glob("*.yaml"):
                with open(yaml_file, "r") as f:
                    meta = yaml.safe_load(f)
                    if meta:
                        skills.append(meta)
        except Exception as e:
            print(f"[!] Error reading skills: {e}")
            
    return {"skills": skills}

@app.get("/v1/memory/search", dependencies=[Depends(verify_api_key)])
async def search_memory(query: str, target_owners: Optional[str] = None, similarity_threshold: float = 0.75):
    """Searches semantic vector memory using cosine similarity."""
    if not memory:
        raise HTTPException(status_code=500, detail="Memory manager not initialized")
        
    owners = None
    if target_owners:
        owners = [o.strip() for o in target_owners.split(",") if o.strip()]
        
    results = memory.query_semantic_memory(
        caller_agent="orchestrator",
        query_text=query,
        target_owners=owners,
        similarity_threshold=similarity_threshold
    )
    return {"results": results}
