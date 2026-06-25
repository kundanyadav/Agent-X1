import unittest
import sys
import pathlib
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.jobs.tasks import sync_devops_backlog
from src.core.orchestrator import OrchestrationEngine
from src.integrations.ado import AzureDevOpsClient

class TestScheduledTasks(unittest.TestCase):

    @patch("src.jobs.tasks.load_resources")
    def test_sync_devops_backlog_workflow(self, mock_load):
        """Verifies the end-to-end ADO backlog sync workflow: poll, branch, code, test, commit, push, PR."""
        mock_engine = MagicMock(spec=OrchestrationEngine)
        mock_engine.memory = MagicMock(spec=MemoryManager)
        mock_engine.devops_worker = MagicMock()
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

if __name__ == "__main__":
    unittest.main()
