import unittest
import sys
import pathlib
import os

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.core.tools import ToolRunner
from src.audit.lineage import LineageLogger
from src.memory.memory import MemoryManager

class TestToolRunner(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        
        self.db_path = self.tmp_dir / "test_tools_memory.db"
        self.log_path = self.tmp_dir / "test_tools_lineage.jsonl"
        self.test_file = self.tmp_dir / "test_tool_file.txt"
        
        # Cleanup
        if self.db_path.exists():
            self.db_path.unlink()
        if self.log_path.exists():
            self.log_path.unlink()
        if self.test_file.exists():
            self.test_file.unlink()
            
        self.memory = MemoryManager(db_path=str(self.db_path))
        self.lineage = LineageLogger(log_path=str(self.log_path))
        self.runner = ToolRunner(lineage_logger=self.lineage, memory_manager=self.memory)

        # Initialize session
        self.memory.start_session("session-tools", "Testing tool runner")

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        if self.log_path.exists():
            self.log_path.unlink()
        if self.test_file.exists():
            self.test_file.unlink()

    def test_run_shell(self):
        """Verifies command execution and audit logging."""
        res = self.runner.run_shell(
            command="echo 'hello world'",
            correlation_id="session-tools",
            agent_owner="orchestrator",
            justification="Verify shell execution"
        )
        
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("hello world", res["stdout"])
        
        # Verify action logged to memory
        actions = self.memory.get_session_history("session-tools")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["tool_called"], "run_shell")
        
        # Verify action logged to audit trail
        logs = self.lineage.read_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "run_shell")
        self.assertEqual(logs[0]["correlation_id"], "session-tools")

    def test_file_operations_lifecycle(self):
        """Verifies write, patch, delete file mutations with hashing and auditing."""
        # 1. Write file
        self.runner.write_file(
            path=str(self.test_file),
            content="Line 1: Initial text\nLine 2: Target text\n",
            correlation_id="session-tools",
            agent_owner="codeworker",
            justification="Write test file"
        )
        
        self.assertTrue(self.test_file.exists())
        self.assertEqual(self.runner.read_file(str(self.test_file)), "Line 1: Initial text\nLine 2: Target text\n")
        
        # Verify write logs
        logs = self.lineage.read_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "write_file")
        self.assertIsNone(logs[0]["pre_hash"])
        self.assertIsNotNone(logs[0]["post_hash"])
        self.assertIn("diff", logs[0]["details"])
        self.assertIn("+Line 1: Initial text", logs[0]["details"]["diff"])
        
        # 2. Patch file
        self.runner.patch_file(
            path=str(self.test_file),
            search="Target text",
            replace="Patched text",
            correlation_id="session-tools",
            agent_owner="codeworker",
            justification="Patch test file"
        )
        
        self.assertIn("Patched text", self.runner.read_file(str(self.test_file)))
        self.assertNotIn("Target text", self.runner.read_file(str(self.test_file)))
        
        # Verify patch logs
        logs = self.lineage.read_logs()
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1]["action"], "patch_file")
        self.assertIsNotNone(logs[1]["pre_hash"])
        self.assertIsNotNone(logs[1]["post_hash"])
        self.assertNotEqual(logs[1]["pre_hash"], logs[1]["post_hash"])
        self.assertIn("diff", logs[1]["details"])
        self.assertIn("-Line 2: Target text", logs[1]["details"]["diff"])
        self.assertIn("+Line 2: Patched text", logs[1]["details"]["diff"])
        
        # 3. Delete file
        self.runner.delete_file(
            path=str(self.test_file),
            correlation_id="session-tools",
            agent_owner="codeworker",
            justification="Delete test file"
        )
        
        self.assertFalse(self.test_file.exists())
        
        # Verify delete logs
        logs = self.lineage.read_logs()
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[2]["action"], "delete_file")
        self.assertIsNotNone(logs[2]["pre_hash"])
        self.assertIsNone(logs[2]["post_hash"])
        self.assertIn("diff", logs[2]["details"])
        self.assertIn("-Line 1: Initial text", logs[2]["details"]["diff"])

    def test_list_dir(self):
        """Verifies listing of a directory."""
        contents = self.runner.list_dir(str(self.tmp_dir))
        self.assertIsInstance(contents, list)

if __name__ == "__main__":
    unittest.main()
