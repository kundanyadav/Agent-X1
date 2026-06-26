import unittest
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.core.orchestrator import OrchestrationEngine
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.integrations.ado import AzureDevOpsClient

class TestOrchestrationEngine(unittest.TestCase):
    def setUp(self):
        self.mock_router = MagicMock(spec=InferenceRouter)
        self.mock_tools = MagicMock(spec=ToolRunner)
        self.mock_memory = MagicMock(spec=MemoryManager)
        self.mock_ado = MagicMock(spec=AzureDevOpsClient)
        
        self.engine = OrchestrationEngine(
            router=self.mock_router,
            tools=self.mock_tools,
            memory=self.mock_memory,
            ado_client=self.mock_ado
        )

    def test_generate_correlation_id(self):
        """Verifies correlation ID is a valid UUID string."""
        cid = self.engine.generate_correlation_id()
        self.assertEqual(len(cid), 36)
        self.assertEqual(cid.count("-"), 4)

    def test_decompose_goal(self):
        """Verifies goal decomposition to task lists."""
        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {
                            "id": "t-1",
                            "name": "Init config",
                            "depends_on": [],
                            "worker": "codeworker",
                            "args": {"task_description": "create yaml config"}
                        }
                    ])
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response
        
        tasks = self.engine.decompose_goal("Set up system configurations")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "t-1")
        self.assertEqual(tasks[0]["worker"], "codeworker")

    def test_is_major_change(self):
        """Verifies correct classification of Major vs Minor changes."""
        # 1. Minor tasks
        minor_1 = {
            "worker": "codeworker",
            "args": {"task_description": "implement simple helper function"}
        }
        minor_2 = {
            "worker": "testworker",
            "args": {"test_command": "pytest tests/test_router.py"}
        }
        self.assertFalse(self.engine.is_major_change(minor_1))
        self.assertFalse(self.engine.is_major_change(minor_2))

        # 2. Major tasks: dependency install
        major_dep = {
            "worker": "testworker",
            "args": {"test_command": "pip install cryptography"}
        }
        self.assertTrue(self.engine.is_major_change(major_dep))

        # 3. Major tasks: delete file
        major_del = {
            "worker": "codeworker",
            "args": {"task_description": "delete file /src/temp.txt"}
        }
        self.assertTrue(self.engine.is_major_change(major_del))

        # 4. Major tasks: force git push
        major_push = {
            "worker": "devopsworker",
            "args": {
                "operation": "git_push",
                "params": {"force": True}
            }
        }
        self.assertTrue(self.engine.is_major_change(major_push))

    @patch("src.core.workers.CodeWorker.execute_task")
    @patch("src.core.workers.TestWorker.execute_task")
    def test_execute_loop_success(self, mock_test_run, mock_code_run):
        """Verifies execution loop runs to completion when all tasks succeed."""
        mock_code_run.return_value = {"status": "success"}
        mock_test_run.return_value = {"status": "success"}
        
        tasks = [
            {
                "id": "t-1",
                "name": "Write code",
                "depends_on": [],
                "worker": "codeworker",
                "args": {"task_description": "modify code"}
            },
            {
                "id": "t-2",
                "name": "Run tests",
                "depends_on": ["t-1"],
                "worker": "testworker",
                "args": {"test_command": "pytest"}
            }
        ]
        
        status = self.engine.execute_loop("session-success", tasks, auto_approve=True)
        self.assertEqual(status, "completed")
        self.assertEqual(mock_code_run.call_count, 1)
        self.assertEqual(mock_test_run.call_count, 1)

    @patch("src.core.workers.CodeWorker.execute_task")
    def test_execute_loop_major_gate(self, mock_code_run):
        """Verifies loop blocks execution of Major changes when auto_approve=False."""
        tasks = [
            {
                "id": "t-delete",
                "name": "Cleanup files",
                "depends_on": [],
                "worker": "codeworker",
                "args": {"task_description": "delete file workspace.db"}
            }
        ]
        
        status = self.engine.execute_loop("session-gate", tasks, auto_approve=False)
        self.assertEqual(status, "paused_for_approval")
        mock_code_run.assert_not_called()

    @patch("src.core.workers.CodeWorker.execute_task")
    @patch("src.core.workers.TestWorker.execute_task")
    def test_execute_loop_replan_recovery(self, mock_test_run, mock_code_run):
        """Verifies loop invokes re-planner when worker task fails after all retries."""
        # First task (codeworker) succeeds
        mock_code_run.return_value = {"status": "success"}
        # Second task (testworker) fails on all attempts
        mock_test_run.return_value = {"status": "failed", "error": "ImportError: no module named cryptography"}
        
        tasks = [
            {
                "id": "t-1",
                "name": "Write code",
                "depends_on": [],
                "worker": "codeworker",
                "args": {"task_description": "modify code"}
            },
            {
                "id": "t-2",
                "name": "Run tests",
                "depends_on": ["t-1"],
                "worker": "testworker",
                "args": {"test_command": "pytest"}
            }
        ]
        
        # Mock re-planner to add a corrective task to install dependency and then re-run test
        mock_replan_response = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {
                            "id": "t-corrective",
                            "name": "Install deps",
                            "depends_on": [],
                            "worker": "codeworker",
                            "args": {"task_description": "install cryptography dependency"}
                        }
                    ])
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_replan_response
        
        # Execute run
        status = self.engine.execute_loop("session-replan", tasks, auto_approve=True)
        self.assertEqual(status, "completed")
        
        # 1 run of t-1, 1 run of t-corrective
        self.assertEqual(mock_code_run.call_count, 2)

    def test_write_and_check_tasks_plan(self):
        """Verifies tasks_plan.md generation, approval check, and status update."""
        tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        plan_path = str(tmp_dir / "test_tasks_plan.md")
        
        # Instantiate test engine with tools=None to run direct file fallback path
        test_engine = OrchestrationEngine(
            router=self.mock_router,
            tools=None,
            memory=self.mock_memory
        )
        
        tasks = [
            {"id": "t-1", "name": "Task One", "depends_on": [], "worker": "codeworker", "args": {"a": 1}}
        ]
        
        # 1. Test write
        test_engine.write_tasks_plan(
            goal="Test goal",
            tasks=tasks,
            correlation_id="uuid-test-123",
            status="Awaiting Approval",
            plan_path=plan_path
        )
        
        p = pathlib.Path(plan_path)
        self.assertTrue(p.is_file())
        
        content = p.read_text(encoding="utf-8")
        self.assertIn("uuid-test-123", content)
        self.assertIn("Awaiting Approval", content)
        self.assertIn("t-1", content)
        
        # 2. Test check approval (unapproved initially)
        self.assertFalse(test_engine.check_plan_file_approved(plan_path))
        
        # Write approved status
        p.write_text(content.replace("- [ ] I approve", "- [x] I approve"), encoding="utf-8")
        self.assertTrue(test_engine.check_plan_file_approved(plan_path))
        
        # 3. Test update status
        test_engine.update_tasks_plan_status(plan_path, "Completed")
        updated_content = p.read_text(encoding="utf-8")
        self.assertIn("Status**: Completed", updated_content)
        self.assertIn("- [x] I approve", updated_content)
        
        # Cleanup
        if p.is_file():
            p.unlink()

    @patch("src.core.workers.CodeWorker.execute_task")
    def test_execute_loop_updates_tasks_plan_status(self, mock_code_run):
        """Verifies execute_loop updates status in the tasks plan file."""
        tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        plan_path = str(tmp_dir / "test_exec_tasks_plan.md")
        
        # Instantiate test engine with tools=None to run direct file fallback path
        test_engine = OrchestrationEngine(
            router=self.mock_router,
            tools=None,
            memory=self.mock_memory
        )
        
        # Write initial plan
        tasks = [{"id": "t-1", "name": "Task 1", "depends_on": [], "worker": "codeworker", "args": {}}]
        test_engine.write_tasks_plan("Goal", tasks, "cid", "Approved", plan_path)
        
        mock_code_run.return_value = {"status": "success"}
        
        status = test_engine.execute_loop("cid", tasks, auto_approve=True, plan_path=plan_path)
        self.assertEqual(status, "completed")
        
        # Check that plan file status is updated to Completed
        content = pathlib.Path(plan_path).read_text(encoding="utf-8")
        self.assertIn("Status**: Completed", content)
        
        # Cleanup
        if pathlib.Path(plan_path).exists():
            pathlib.Path(plan_path).unlink()

    def test_replan_failed_task_propagates_provider_exception(self):
        """Verifies that replan_failed_task lets LLM provider and connection errors propagate."""
        self.mock_router.chat_completions.side_effect = RuntimeError("Connection timed out to Copilot API")
        
        failed_task = {"id": "t-2", "name": "Run tests", "worker": "testworker", "args": {}}
        all_tasks = [failed_task]
        
        with self.assertRaises(RuntimeError) as context:
            self.engine.repl_failed_task = self.engine.replan_failed_task(
                correlation_id="cid-123",
                failed_task=failed_task,
                error_msg="ImportError: module not found",
                all_tasks=all_tasks
            )
        self.assertIn("Connection timed out", str(context.exception))

if __name__ == "__main__":
    unittest.main()
