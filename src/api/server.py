import os
import queue
import threading
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
    global router, tools, memory, engine
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
    if expected_key and x_agent_api_key != expected_key:
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
            active_sessions[session_id]["status"] = "running"
            
            # Execute loop
            status = engine.execute_loop(session_id, tasks, auto_approve=True)
            
            # End session in database
            memory.end_session(session_id, status)
            
            active_sessions[session_id]["status"] = status
            execution_queue.task_done()
        except Exception as e:
            print(f"Error in background execution thread: {e}")
            if "session_id" in locals():
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


# --- FastAPI Routes ---

@app.post("/submit_goal", dependencies=[Depends(verify_api_key)])
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
        "queue_position": queue_pos
    }
    
    # Enqueue task
    execution_queue.put({"session_id": session_id, "goal": request.goal})
    
    return {
        "status": "queued",
        "session_id": session_id,
        "queue_position": queue_pos
    }

@app.get("/status/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_status(session_id: str):
    """Returns the current execution status and tasks for a given session."""
    if session_id not in active_sessions:
        # Fallback: check database history
        if memory:
            history = memory.get_session_history(session_id)
            if history:
                return {
                    "session_id": session_id,
                    "status": "archived",
                    "tasks": history
                }
        raise HTTPException(status_code=404, detail="Session not found")
        
    return active_sessions[session_id]

@app.post("/approve/{session_id}", dependencies=[Depends(verify_api_key)])
async def approve_gated_task(session_id: str, request: ApprovalRequest):
    """Submits user decision for tasks paused on human-in-the-loop gates."""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # In a real environment, this updates a state condition or event that pauses the thread.
    # For this implementation, we return confirmation of approval receipt.
    return {
        "session_id": session_id,
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
        execution_queue.put({"session_id": session_id, "goal": goal_text})
        
        active_sessions[session_id] = {
            "session_id": session_id,
            "goal": goal_text,
            "status": "queued",
            "tasks": []
        }
        return {"reply": f"Goal queued successfully. Session ID: {session_id}"}
        
    return {"reply": f"Received: '{text}'. Format message as 'goal: <your task>' to run."}
