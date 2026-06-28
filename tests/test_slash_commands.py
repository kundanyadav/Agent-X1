import os
import unittest
import json
import pathlib
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.core.orchestrator import OrchestrationEngine
from src.gateways.gateway import CliGateway
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager

class TestSlashCommands(unittest.TestCase):
    def setUp(self):
        self.mock_router = MagicMock(spec=InferenceRouter)
        self.mock_tools = MagicMock(spec=ToolRunner)
        self.mock_memory = MagicMock(spec=MemoryManager)
        
        self.engine = OrchestrationEngine(
            router=self.mock_router,
            tools=self.mock_tools,
            memory=self.mock_memory
        )

    def test_answer_planning_qa(self):
        """Verifies that answer_planning_qa retrieves memory context, queries the LLM, and writes to memory."""
        self.mock_memory.query_semantic_memory.return_value = [
            {"category": "user_preference", "solution": "We use Redis for DB indexing"}
        ]
        
        self.mock_router.chat_completions.return_value = {
            "choices": [{
                "message": {
                    "content": "Redis caching is indeed used."
                }
            }]
        }
        
        answer = self.engine.answer_planning_qa("Do we use Redis?")
        
        self.assertEqual(answer, "Redis caching is indeed used.")
        self.mock_memory.query_semantic_memory.assert_called_once_with(
            "orchestrator", "Do we use Redis?", similarity_threshold=0.7
        )
        self.mock_memory.learn_fact.assert_called_once_with(
            agent_owner="orchestrator",
            category="user_qa",
            issue="Do we use Redis?",
            solution="Redis caching is indeed used."
        )

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_exit(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /exit terminates the interactive loop immediately."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-123"
        mock_engine_instance.generate_planning_proposal.return_value = "Plan Proposal"
        
        mock_input.side_effect = ["/exit"]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "aborted")
        self.assertEqual(mock_input.call_count, 1)

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_goal_pivot(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /goal <new_goal> pivots the goal and resets history."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-123"
        mock_engine_instance.generate_planning_proposal.side_effect = [
            "Proposal 1", # First goal proposal
            "Proposal 2"  # Pivoted goal proposal
        ]
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/goal Build a compiler", # Pivot command
            "approved for build"      # Approve the new proposal
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Build a site", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        self.assertEqual(mock_engine_instance.generate_planning_proposal.call_count, 2)
        mock_engine_instance.decompose_plan_into_tasks.assert_called_once_with("Proposal 2")

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_btw_qa(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /btw <question> executes a Q/A query without altering history."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-123"
        mock_engine_instance.generate_planning_proposal.return_value = "My Single Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/btw what is 2+2?", # Side question
            "approved for build" # Approve the original plan
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        mock_engine_instance.answer_planning_qa.assert_called_once_with("what is 2+2?")
        # generate_planning_proposal was only called once (for the initial proposal)
        mock_engine_instance.generate_planning_proposal.assert_called_once()
        mock_engine_instance.decompose_plan_into_tasks.assert_called_once_with("My Single Proposal")

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_schedule(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /schedule <cron> postpones execution and saves configs."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-12345"
        mock_engine_instance.generate_planning_proposal.return_value = "Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/schedule 0 2 * * *", # Schedule it
            "approved for build"   # Save & exit
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        with patch.object(gateway, "save_scheduled_job") as mock_save:
            status = gateway.run_goal("Goal", dry_run=True, interactive=True)
            self.assertEqual(status, "completed")
            mock_save.assert_called_once_with("Goal", "0 2 * * *", "mock-id-12345")

    def test_compact_planning_history(self):
        """Verifies that compact_planning_history distills history correctly and replaces it."""
        self.mock_router.chat_completions.return_value = {
            "choices": [{
                "message": {
                    "content": "Distilled Summary: Agreed to use gpt-4o."
                }
            }]
        }
        
        history = [
            {"role": "assistant", "content": "Initial Proposal"},
            {"role": "user", "content": "Use gpt-4o"}
        ]
        
        new_history = self.engine.compact_planning_history("Goal", history)
        
        self.assertEqual(len(new_history), 3)
        self.assertEqual(new_history[0]["role"], "system")
        self.assertEqual(new_history[2]["content"], "Summary of current design agreements and constraints:\nDistilled Summary: Agreed to use gpt-4o.")
        self.mock_router.chat_completions.assert_called_once()

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_compact(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /compact triggers history distillation and regenerates proposal."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-12345"
        mock_engine_instance.generate_planning_proposal.side_effect = [
            "Proposal 1", # Initial proposal
            "Proposal 2"  # Regenerated proposal after compaction
        ]
        mock_engine_instance.compact_planning_history.return_value = [
            {"role": "assistant", "content": "Compacted history base"}
        ]
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/compact",           # Compact history command
            "approved for build"   # Approve
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        mock_engine_instance.compact_planning_history.assert_called_once()
        self.assertEqual(mock_engine_instance.generate_planning_proposal.call_count, 2)

    def test_db_pin_and_resume(self):
        """Verifies that pin_session and get_pinned_session insert and retrieve correct sqlite records."""
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_pin_mem.db")
        
        try:
            mem = MemoryManager(db_path=db_path)
            history = [{"role": "user", "content": "hello"}]
            mem.pin_session("my_pin", "My Pinned Goal", history, "0 0 * * *")
            
            sessions = mem.get_pinned_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["name"], "my_pin")
            self.assertEqual(sessions[0]["goal"], "My Pinned Goal")
            
            single = mem.get_pinned_session("my_pin")
            self.assertIsNotNone(single)
            self.assertEqual(single["goal"], "My Pinned Goal")
            self.assertEqual(single["history"], history)
            self.assertEqual(single["scheduled_cron"], "0 0 * * *")
        finally:
            shutil.rmtree(temp_dir)

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_pin(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /pin command triggers memory pin_session."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-12345"
        mock_engine_instance.generate_planning_proposal.return_value = "Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/pin custom_pin_name",
            "approved for build"
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        mock_engine_instance.memory.pin_session.assert_called_once_with(
            "custom_pin_name", "Goal", [{"role": "assistant", "content": "Proposal"}], None
        )

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_resume(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /resume selects and loads a pinned session context."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-12345"
        mock_engine_instance.generate_planning_proposal.return_value = "Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        # Mock memory returning pinned sessions list
        mock_engine_instance.memory.get_pinned_sessions.return_value = [
            {"name": "my_pin", "goal": "Old Goal", "timestamp": 1719602511}
        ]
        mock_engine_instance.memory.get_pinned_session.return_value = {
            "name": "my_pin",
            "goal": "Old Goal",
            "history": [{"role": "assistant", "content": "Old Pinned Proposal"}],
            "scheduled_cron": None
        }
        
        mock_input.side_effect = [
            "/resume",            # Type resume
            "1",                  # Select session #1
            "approved for build"   # Approve the resumed plan
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        mock_engine_instance.memory.get_pinned_sessions.assert_called_once()
        mock_engine_instance.memory.get_pinned_session.assert_called_once_with("my_pin")
        # Decompose was called with the restored proposal content
        mock_engine_instance.decompose_plan_into_tasks.assert_called_once_with("Old Pinned Proposal")

    def test_db_delete_pinned_session(self):
        """Verifies that delete_pinned_session deletes the specified session and returns correctness."""
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_delete_mem.db")
        
        try:
            mem = MemoryManager(db_path=db_path)
            history = [{"role": "user", "content": "hello"}]
            mem.pin_session("my_pin", "My Pinned Goal", history, "0 0 * * *")
            
            # Verify it exists
            self.assertEqual(len(mem.get_pinned_sessions()), 1)
            
            # Delete it
            success = mem.delete_pinned_session("my_pin")
            self.assertTrue(success)
            self.assertEqual(len(mem.get_pinned_sessions()), 0)
            
            # Try deleting non-existent session
            success_fail = mem.delete_pinned_session("non_existent")
            self.assertFalse(success_fail)
        finally:
            shutil.rmtree(temp_dir)

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_delete(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /delete <name> command triggers memory delete_pinned_session."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-12345"
        mock_engine_instance.generate_planning_proposal.return_value = "Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        mock_input.side_effect = [
            "/delete custom_pin_name",
            "approved for build"
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)
        
        self.assertEqual(status, "completed")
        mock_engine_instance.memory.delete_pinned_session.assert_called_once_with("custom_pin_name")

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_clear(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /clear resets the screen without reprinting the proposal or changing state."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }

        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-clear"
        mock_engine_instance.generate_planning_proposal.return_value = "My Active Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []

        mock_input.side_effect = [
            "/clear",
            "approved for build"
        ]

        gateway = CliGateway(config_path="config.yaml")
        with patch("os.system") as mock_os_system:
            status = gateway.run_goal("Goal", dry_run=True, interactive=True)
            mock_os_system.assert_called_once()

        self.assertEqual(status, "completed")
        # generate_planning_proposal called once (initial proposal only — /clear does not re-generate)
        mock_engine_instance.generate_planning_proposal.assert_called_once()

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_status(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /status prints session metadata without altering the plan."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"},
            "openrouter": {"enabled": True, "preset": "free-kd1", "model": "ignored"}
        }

        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-status"
        mock_engine_instance.generate_planning_proposal.return_value = "Proposal"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        mock_engine_instance.memory.get_pinned_sessions.return_value = []

        mock_input.side_effect = [
            "/status",
            "approved for build"
        ]

        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)

        self.assertEqual(status, "completed")
        # Status should not cause a new proposal to be generated
        mock_engine_instance.generate_planning_proposal.assert_called_once()

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_interactive_loop_export(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that /export writes the current proposal to a markdown file."""
        import tempfile, shutil
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }

        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-export"
        mock_engine_instance.generate_planning_proposal.return_value = "## Step 1\nDo the thing."
        mock_engine_instance.decompose_plan_into_tasks.return_value = []

        mock_input.side_effect = [
            "/export test_export_plan",
            "approved for build"
        ]

        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Goal", dry_run=True, interactive=True)

        self.assertEqual(status, "completed")
        export_path = os.path.join("tmp", "test_export_plan.md")
        self.assertTrue(os.path.exists(export_path), f"Expected export file at {export_path}")
        with open(export_path, "r") as f:
            content = f.read()
        self.assertIn("## Step 1", content)
        self.assertIn("Goal", content)
        # Cleanup
        if os.path.exists(export_path):
            os.remove(export_path)

if __name__ == "__main__":
    unittest.main()
