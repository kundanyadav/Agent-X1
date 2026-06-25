import unittest
import sys
import pathlib
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.core.workers import CodeWorker, TestWorker, DevOpsWorker
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.integrations.ado import AzureDevOpsClient

class TestWorkers(unittest.TestCase):
    def setUp(self):
        self.mock_router = MagicMock(spec=InferenceRouter)
        self.mock_tools = MagicMock(spec=ToolRunner)
        self.mock_memory = MagicMock(spec=MemoryManager)
        self.mock_ado = MagicMock(spec=AzureDevOpsClient)

    def test_code_worker_write_file(self):
        """Verifies CodeWorker triggers write_file tool when LLM returns write_file action."""
        # Mock LLM to return JSON to write file
        mock_response = {
            "choices": [{
                "message": {
                    "content": '{"action": "write_file", "path": "tmp/new_code.py", "content": "print(123)", "justification": "Create new module"}'
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response

        worker = CodeWorker(router=self.mock_router, tools=self.mock_tools, memory=self.mock_memory)
        res = worker.execute_task("Create mock file", "correlation-123")
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "write_file")
        self.assertEqual(res["path"], "tmp/new_code.py")
        
        self.mock_tools.write_file.assert_called_once_with(
            path="tmp/new_code.py",
            content="print(123)",
            correlation_id="correlation-123",
            agent_owner="codeworker",
            justification="Create new module"
        )

    def test_code_worker_patch_file(self):
        """Verifies CodeWorker triggers patch_file tool when LLM returns patch_file action."""
        # Mock LLM to return JSON to patch file
        mock_response = {
            "choices": [{
                "message": {
                    "content": '{"action": "patch_file", "path": "tmp/existing.py", "search": "old_code", "replace": "new_code", "justification": "Update function name"}'
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response

        worker = CodeWorker(router=self.mock_router, tools=self.mock_tools, memory=self.mock_memory)
        res = worker.execute_task("Update code block", "correlation-123")
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "patch_file")
        self.assertEqual(res["path"], "tmp/existing.py")
        
        self.mock_tools.patch_file.assert_called_once_with(
            path="tmp/existing.py",
            search="old_code",
            replace="new_code",
            correlation_id="correlation-123",
            agent_owner="codeworker",
            justification="Update function name"
        )

    def test_test_worker_success(self):
        """Verifies TestWorker status on successful test execution."""
        self.mock_tools.run_shell.return_value = {
            "stdout": "All tests passed",
            "stderr": "",
            "exit_code": 0
        }
        
        worker = TestWorker(router=self.mock_router, tools=self.mock_tools, memory=self.mock_memory)
        res = worker.execute_task("pytest tests/", "correlation-123")
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("All tests passed", res["stdout"])

    def test_test_worker_failure_diagnosis(self):
        """Verifies TestWorker executes diagnostic routing when test fails."""
        self.mock_tools.run_shell.return_value = {
            "stdout": "",
            "stderr": "AssertionError: expected True but got False",
            "exit_code": 1
        }
        
        # Mock LLM diagnostic response
        mock_completion = {
            "choices": [{
                "message": {
                    "content": "Diagnosis: The assertion failed because state variable was incorrect."
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_completion
        
        worker = TestWorker(router=self.mock_router, tools=self.mock_tools, memory=self.mock_memory)
        res = worker.execute_task("pytest tests/", "correlation-123")
        
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["exit_code"], 1)
        self.assertEqual(res["diagnosis"], "Diagnosis: The assertion failed because state variable was incorrect.")

    def test_devops_worker_git_commit(self):
        """Verifies DevOpsWorker routes git stages and commits correctly."""
        self.mock_tools.run_shell.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # git add
            {"stdout": "committed successfully", "stderr": "", "exit_code": 0},  # git commit
            {"stdout": "commit_hash_12345\n", "stderr": "", "exit_code": 0}  # git rev-parse HEAD
        ]
        
        worker = DevOpsWorker(router=self.mock_router, tools=self.mock_tools, memory=self.mock_memory)
        res = worker.execute_task(
            operation="git_commit",
            params={"message": "docs update", "files": "README.md"},
            correlation_id="correlation-123"
        )
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["commit_hash"], "commit_hash_12345")
        
        # Check call arguments
        self.assertEqual(self.mock_tools.run_shell.call_count, 3)

    def test_devops_worker_ado_operations(self):
        """Verifies DevOpsWorker routes pull request creation, work item updates, and backlog syncs."""
        self.mock_ado.query_backlog.return_value = [{"id": 45}]
        self.mock_ado.update_work_item_state.return_value = {"id": 45, "state": "PR Raised"}
        self.mock_ado.create_pull_request.return_value = {"pullRequestId": 99}
        
        worker = DevOpsWorker(
            router=self.mock_router,
            tools=self.mock_tools,
            memory=self.mock_memory,
            ado_client=self.mock_ado
        )
        
        # 1. Backlog sync
        sync_res = worker.execute_task("ado_sync_backlog", {"wiql": "SELECT ..."}, "correlation-123")
        self.assertEqual(sync_res["status"], "success")
        self.assertEqual(sync_res["work_items"][0]["id"], 45)
        
        # 2. Update state
        state_res = worker.execute_task(
            operation="ado_update_state",
            params={"work_item_id": 45, "state": "PR Raised"},
            correlation_id="correlation-123"
        )
        self.assertEqual(state_res["status"], "success")
        self.assertEqual(state_res["work_item"]["state"], "PR Raised")
        
        # 3. Create PR
        pr_res = worker.execute_task(
            operation="ado_create_pr",
            params={
                "source_branch": "feature/test",
                "target_branch": "main",
                "title": "Merge feature",
                "description": "desc"
            },
            correlation_id="correlation-123"
        )
        self.assertEqual(pr_res["status"], "success")
        self.assertEqual(pr_res["pull_request"]["pullRequestId"], 99)

if __name__ == "__main__":
    unittest.main()
