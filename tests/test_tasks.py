import unittest
import sys
import pathlib
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.jobs.tasks import sync_devops_backlog, run_workspace_tests
from src.core.orchestrator import OrchestrationEngine
from src.integrations.ado import AzureDevOpsClient
from src.memory.memory import MemoryManager


class TestScheduledTasks(unittest.TestCase):

    @patch("src.jobs.tasks.load_resources")
    def test_sync_devops_backlog_workflow(self, mock_load):
        """Verifies the end-to-end ADO backlog sync workflow: poll, branch, code, test, commit, push, PR."""
        mock_engine = MagicMock()
        mock_engine.memory = MagicMock(spec=MemoryManager)
        mock_engine.devops_worker = MagicMock()
        mock_engine.router.config = {
            "ado": {
                "target_branch": "main",
                "branch_prefix": "feature/task-",
                "state_mapping": {
                    "backlog": "To Do",
                    "in_progress": "Doing",
                    "review_required": "PR Raised",
                    "completed": "Closed"
                }
            }
        }
        mock_ado = MagicMock(spec=AzureDevOpsClient)
        mock_load.return_value = (mock_engine, mock_ado)

        
        # Mock backlog poll response
        mock_ado.query_backlog.return_value = [{"id": 42}]
        mock_ado.get_work_item.return_value = {
            "id": 42,
            "fields": {
                "System.Title": "Add audit key",
                "System.Description": "Configure encryption key"
            }
        }
        
        # Mock orchestrator states
        mock_engine.generate_correlation_id.return_value = "sync-uuid-111"
        mock_engine.decompose_goal.return_value = [{"id": "t1"}]
        mock_engine.execute_loop.return_value = "completed"
        
        # Run sync
        sync_devops_backlog(config_path="config.yaml")
        
        # 1. Verify board states updated to Doing on checkout, and PR Raised on PR post
        mock_ado.update_work_item_state.assert_any_call(42, "Doing")
        mock_ado.update_work_item_state.assert_any_call(42, "PR Raised")
        
        # 2. Verify git checkout branch was called
        mock_engine.devops_worker.execute_task.assert_any_call(
            operation="git_branch",
            params={"branch_name": "feature/task-42", "justification": "Create branch for work item #42"},
            correlation_id="sync-uuid-111"
        )
        
        # 3. Verify git commit was called
        mock_engine.devops_worker.execute_task.assert_any_call(
            operation="git_commit",
            params={"message": "feat: resolve work item #42 - Add audit key", "files": ".", "justification": "Commit solution for task #42"},
            correlation_id="sync-uuid-111"
        )
        
        # 4. Verify git push was called
        mock_engine.devops_worker.execute_task.assert_any_call(
            operation="git_push",
            params={"branch_name": "feature/task-42", "justification": "Push feature branch to remote origin"},
            correlation_id="sync-uuid-111"
        )
        
        # 5. Verify PR creation was called
        mock_engine.devops_worker.execute_task.assert_any_call(
            operation="ado_create_pr",
            params={
                "source_branch": "feature/task-42",
                "target_branch": "main",
                "title": "Merge feature: Add audit key (Resolves #42)",
                "description": "Automated pull request resolving Work Item #42.\nCorrelation ID: sync-uuid-111"
            },
            correlation_id="sync-uuid-111"
        )

    @patch("src.jobs.tasks.load_resources")
    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_consolidate_memories_workflow(self, mock_connect, mock_exists, mock_load):
        """Verifies that consolidate_memories processes recent sessions and saves distilled facts."""
        mock_engine = MagicMock()
        mock_engine.memory = MagicMock(spec=MemoryManager)
        mock_engine.memory.db_path = "tmp/mock_memory.db"
        mock_load.return_value = (mock_engine, None)
        
        mock_exists.return_value = True
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # fetch sessions:
        mock_cursor.fetchall.return_value = [
            {"session_id": "session-123", "goal": "Compile app", "status": "completed"}
        ]
        # check exists:
        mock_cursor.fetchone.return_value = (0,)
        
        # Mock get_session_history
        mock_engine.memory.get_session_history.return_value = [
            {"tool_called": "execute_task", "status": "success", "arguments": {}, "stdout": "build success", "stderr": ""}
        ]
        
        # Mock LLM completions response
        mock_response = {
            "choices": [{
                "message": {
                    "content": '{"category": "compilation_fix", "issue": "missing module", "solution": "install package"}'
                }
            }]
        }
        mock_engine.router.chat_completions.return_value = mock_response
        
        # Run consolidation task
        from src.jobs.tasks import consolidate_memories
        consolidate_memories(config_path="config.yaml")
        
        # Verify LLM was called
        mock_engine.router.chat_completions.assert_called_once()
        
        # Verify fact was registered
        mock_engine.memory.learn_fact.assert_called_once_with(
            agent_owner="orchestrator",
            category="compilation_fix",
            issue="missing module (Session: session-123)",
            solution="install package"
        )

    @patch("src.jobs.tasks.load_resources")
    def test_run_workspace_tests_workflow(self, mock_load):
        """Verifies that run_workspace_tests triggers TestWorker executing test command via sys.executable."""
        mock_engine = MagicMock()
        mock_engine.test_worker = MagicMock()
        mock_engine.generate_correlation_id.return_value = "test-uuid-999"
        mock_load.return_value = (mock_engine, None)
        
        mock_engine.test_worker.execute_task.return_value = {"status": "success"}
        
        run_workspace_tests(config_path="config.yaml")
        
        # Verify dynamic test command uses sys.executable and runs via TestWorker (and thus ToolRunner under the hood)
        mock_engine.test_worker.execute_task.assert_called_once_with(
            test_command=f"{sys.executable} -m unittest discover -s tests",
            correlation_id="test-uuid-999"
        )

if __name__ == "__main__":
    unittest.main()
