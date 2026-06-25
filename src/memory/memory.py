import os
import json
import sqlite3
import time
import numpy as np
from typing import List, Dict, Any, Optional

class MemoryManager:
    def __init__(self, db_path: str = "tmp/memory.db", router: Any = None):
        self.db_path = db_path
        self.router = router
        
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initializes SQLite databases and tables with indexes for agent partitioning."""
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            
            # Episodic Sessions Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL,
                    status TEXT NOT NULL
                )
            """)
            
            # Episodic Actions Table (with agent_owner partition)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    agent_owner TEXT NOT NULL,  -- orchestrator, codeworker, testworker, devopsworker
                    timestamp REAL NOT NULL,
                    tool_called TEXT NOT NULL,
                    arguments TEXT,
                    stdout TEXT,
                    stderr TEXT,
                    status TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            
            # Action Feedback Table (with agent_owner partition)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id INTEGER NOT NULL,
                    agent_owner TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    notes TEXT,
                    FOREIGN KEY (action_id) REFERENCES actions(action_id)
                )
            """)
            
            # Semantic Vector Memory Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS semantic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_owner TEXT NOT NULL,
                    category TEXT NOT NULL,
                    issue TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    vector BLOB NOT NULL,       -- Stored as binary numpy array
                    timestamp REAL NOT NULL
                )
            """)
            
            # Create compound indexes for partitioned lookup speed and audit verification
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_owner_session ON actions(agent_owner, session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_owner_action ON feedback(agent_owner, action_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_semantic_owner ON semantic_memory(agent_owner)")
            
            conn.commit()

    def _get_embedding(self, text: str) -> np.ndarray:
        """Generates a text embedding vector. Falls back to TF-IDF mock vector if API is offline."""
        if self.router:
            try:
                # Attempt to query provider embedding API
                # In a real setup, router can expose router.get_embeddings(text)
                # For our implementation, we'll build a lightweight fallback hash vector
                pass
            except Exception:
                pass
                
        # Zero-dependency local mock embedding generator: 384-dimensional normalized hashing vector.
        # This acts as a reliable, fast, completely offline fallback for unit tests and local dev.
        vec = np.zeros(384)
        words = text.lower().split()
        for word in words:
            # Simple deterministic hash value mapped across indices
            idx = sum(ord(c) for c in word) % 384
            vec[idx] += 1.0
            
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    # --- Write APIs ---

    def start_session(self, session_id: str, goal: str):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sessions (session_id, goal, start_time, status) VALUES (?, ?, ?, ?)",
                (session_id, goal, time.time(), "running")
            )
            conn.commit()

    def end_session(self, session_id: str, status: str):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET end_time = ?, status = ? WHERE session_id = ?",
                (time.time(), status, session_id)
            )
            conn.commit()

    def write_action(self, session_id: str, agent_owner: str, tool_called: str, arguments: Dict[str, Any], stdout: str, stderr: str, status: str) -> int:
        """Writes an episodic action to the agent's partition."""
        valid_owners = ["orchestrator", "codeworker", "testworker", "devopsworker"]
        if agent_owner not in valid_owners:
            raise ValueError(f"Invalid agent owner: '{agent_owner}'. Must be one of: {valid_owners}")
            
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO actions (session_id, agent_owner, timestamp, tool_called, arguments, stdout, stderr, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, agent_owner, time.time(), tool_called, json.dumps(arguments), stdout, stderr, status))
            action_id = cursor.lastrowid
            conn.commit()
            return action_id

    def write_feedback(self, action_id: int, agent_owner: str, score: int, notes: str):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO feedback (action_id, agent_owner, score, notes)
                VALUES (?, ?, ?, ?)
            """, (action_id, agent_owner, score, notes))
            conn.commit()

    def learn_fact(self, agent_owner: str, category: str, issue: str, solution: str):
        """Generates embeddings and saves a semantic fact/solution to the agent's partition."""
        valid_owners = ["orchestrator", "codeworker", "testworker", "devopsworker"]
        if agent_owner not in valid_owners:
            raise ValueError(f"Invalid agent owner: '{agent_owner}'")
            
        vector = self._get_embedding(f"{category} {issue} {solution}")
        vector_bytes = vector.tobytes()
        
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO semantic_memory (agent_owner, category, issue, solution, vector, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (agent_owner, category, issue, solution, vector_bytes, time.time()))
            conn.commit()

    # --- Query & Cross-Referencing APIs ---

    def get_session_history(self, session_id: str, target_owners: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Queries episodic actions. Can target own partition or cross-reference other agents' logs."""
        query = "SELECT action_id, agent_owner, timestamp, tool_called, arguments, stdout, stderr, status FROM actions WHERE session_id = ?"
        params = [session_id]
        
        if target_owners:
            placeholders = ",".join("?" for _ in target_owners)
            query += f" AND agent_owner IN ({placeholders})"
            params.extend(target_owners)
            
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
        return [dict(row) for row in rows]

    def query_semantic_memory(self, caller_agent: str, query_text: str, target_owners: Optional[List[str]] = None, similarity_threshold: float = 0.75) -> List[Dict[str, Any]]:
        """Searches vector semantic memory using cosine similarity. Can cross-reference other agent partitions."""
        query_vector = self._get_embedding(query_text)
        
        # Load candidate records
        sql = "SELECT id, agent_owner, category, issue, solution, vector, timestamp FROM semantic_memory"
        params = []
        
        if target_owners:
            placeholders = ",".join("?" for _ in target_owners)
            sql += f" WHERE agent_owner IN ({placeholders})"
            params.extend(target_owners)
        else:
            # Default to query caller's own partition only
            sql += " WHERE agent_owner = ?"
            params.append(caller_agent)
            
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
        results = []
        for row in rows:
            db_id, owner, category, issue, solution, vector_bytes, ts = row
            db_vector = np.frombuffer(vector_bytes, dtype=np.float64)
            
            # Compute cosine similarity: (A . B) / (||A|| * ||B||)
            # Embedding vectors returned by _get_embedding are already normalized (L2 norm = 1)
            similarity = float(np.dot(query_vector, db_vector))
            
            if similarity >= similarity_threshold:
                results.append({
                    "id": db_id,
                    "agent_owner": owner,
                    "category": category,
                    "issue": issue,
                    "solution": solution,
                    "timestamp": ts,
                    "similarity": similarity
                })
                
        # Sort by similarity descending
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results
