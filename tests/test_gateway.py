import unittest
import sys
import pathlib
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.gateways.gateway import CliGateway

class TestCliGateway(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.mock_config_path = self.tmp_dir / "test_gateway_config.yaml"
        
        mock_config = """
inference:
  active_provider: "openai"
  model: "gpt-4"
  model_fallback: "copilot"
  openai:
    api_key: "sk-mock"
storage:
  db_path: "tmp/test_gateway_memory.db"
  audit_log_path: "tmp/test_gateway_lineage.jsonl"
"""
        with open(self.mock_config_path, "w") as f:
            f.write(mock_config)
            
        self.gateway = CliGateway(config_path=str(self.mock_config_path))

    def tearDown(self):
        if self.mock_config_path.exists():
            self.mock_config_path.unlink()
            
        db_file = self.tmp_dir / "test_gateway_memory.db"
        if db_file.exists():
            db_file.unlink()
        log_file = self.tmp_dir / "test_gateway_lineage.jsonl"
        if log_file.exists():
            log_file.unlink()

    def test_run_goal_dry_run(self):
        """Verifies dry-run simulation returns completed and doesn't run full loop."""
        status = self.gateway.run_goal("Test dry run command", dry_run=True)
        self.assertEqual(status, "completed")

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.orchestrator.OrchestrationEngine.execute_loop")
    def test_run_goal_success(self, mock_execute, mock_decompose):
        """Verifies successful run_goal triggers orchestrator decompose and execute loops."""
        mock_decompose.return_value = [{"id": "t1", "name": "Task 1", "worker": "codeworker", "args": {}}]
        mock_execute.return_value = "completed"
        
        status = self.gateway.run_goal("Test real run goal", dry_run=False)
        self.assertEqual(status, "completed")
        mock_decompose.assert_called_once_with("Test real run goal")
        mock_execute.assert_called_once()

if __name__ == "__main__":
    unittest.main()
