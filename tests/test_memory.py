import unittest
import os
import sqlite3
import pathlib
import sys
import time
import numpy as np

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.memory.memory import MemoryManager

class TestMemoryManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.db_path = self.tmp_dir / "test_memory.db"
        # Ensure database is removed before test
        if self.db_path.exists():
            self.db_path.unlink()
        self.memory = MemoryManager(db_path=str(self.db_path))

    def tearDown(self):
        # Cleanup database file
        if self.db_path.exists():
            self.db_path.unlink()

    def test_init_db(self):
        """Verifies database tables and indexes are created successfully."""
        self.assertTrue(self.db_path.exists())
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            self.assertIn("sessions", tables)
            self.assertIn("actions", tables)
            self.assertIn("feedback", tables)
            self.assertIn("semantic_memory", tables)

    def test_session_management(self):
        """Verifies session lifecycle management."""
        self.memory.start_session("session-123", "Build code-worker agent")
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE session_id = ?", ("session-123",))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["goal"], "Build code-worker agent")
            self.assertEqual(row["status"], "running")
            self.assertIsNotNone(row["start_time"])
            self.assertIsNone(row["end_time"])

        time.sleep(0.01)
        self.memory.end_session("session-123", "completed")
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE session_id = ?", ("session-123",))
            row = cursor.fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertIsNotNone(row["end_time"])
            self.assertGreater(row["end_time"], row["start_time"])

    def test_action_partitioning_and_validation(self):
        """Verifies that actions enforce partition validation and get_session_history supports filtering."""
        # Starting session
        self.memory.start_session("session-abc", "Decompose and execute test task")
        
        # Test validation of invalid owner
        with self.assertRaises(ValueError):
            self.memory.write_action(
                session_id="session-abc",
                agent_owner="unknown_agent",
                tool_called="run_command",
                arguments={"cmd": "ls"},
                stdout="file1.txt",
                stderr="",
                status="success"
            )
            
        # Write valid actions to different partitions
        action_id_1 = self.memory.write_action(
            session_id="session-abc",
            agent_owner="orchestrator",
            tool_called="plan_task",
            arguments={"task": "compilation"},
            stdout="Planned",
            stderr="",
            status="success"
        )
        
        action_id_2 = self.memory.write_action(
            session_id="session-abc",
            agent_owner="codeworker",
            tool_called="edit_file",
            arguments={"file": "main.py"},
            stdout="Modified",
            stderr="",
            status="success"
        )
        
        self.assertIsNotNone(action_id_1)
        self.assertIsNotNone(action_id_2)
        
        # Querying with default options (all history)
        history_all = self.memory.get_session_history("session-abc")
        self.assertEqual(len(history_all), 2)
        
        # Querying only orchestrator partition
        history_orch = self.memory.get_session_history("session-abc", target_owners=["orchestrator"])
        self.assertEqual(len(history_orch), 1)
        self.assertEqual(history_orch[0]["agent_owner"], "orchestrator")
        
        # Querying both orchestrator and codeworker
        history_both = self.memory.get_session_history("session-abc", target_owners=["orchestrator", "codeworker"])
        self.assertEqual(len(history_both), 2)

    def test_semantic_memory_vector_search(self):
        """Verifies vector embedding generation, loading facts and searching semantic memory."""
        # Test learning valid owner
        self.memory.learn_fact(
            agent_owner="codeworker",
            category="git_error",
            issue="detached head state warning",
            solution="git checkout main to return to main branch context"
        )
        
        self.memory.learn_fact(
            agent_owner="testworker",
            category="test_failure",
            issue="db connection lock",
            solution="configure higher sqlite connection timeout parameters"
        )
        
        # Querying codeworker's semantic memory only (default)
        matches_default = self.memory.query_semantic_memory(
            caller_agent="codeworker",
            query_text="git warning detached head",
            similarity_threshold=0.4
        )
        self.assertEqual(len(matches_default), 1)
        self.assertEqual(matches_default[0]["category"], "git_error")
        self.assertEqual(matches_default[0]["agent_owner"], "codeworker")
        
        # Querying with cross-referencing other agent partitions
        matches_cross = self.memory.query_semantic_memory(
            caller_agent="codeworker",
            query_text="sqlite connection locked database",
            target_owners=["codeworker", "testworker"],
            similarity_threshold=0.4
        )
        self.assertEqual(len(matches_cross), 1)
        self.assertEqual(matches_cross[0]["category"], "test_failure")
        self.assertEqual(matches_cross[0]["agent_owner"], "testworker")

        # Querying with too high threshold should return empty
        matches_high_threshold = self.memory.query_semantic_memory(
            caller_agent="codeworker",
            query_text="sqlite connection locked database",
            target_owners=["codeworker", "testworker"],
            similarity_threshold=0.99
        )
        self.assertEqual(len(matches_high_threshold), 0)

if __name__ == "__main__":
    unittest.main()
