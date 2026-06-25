import unittest
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient

class TestAPIServer(unittest.TestCase):
    def setUp(self):
        # We must initialize resources with a test configuration
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.mock_config_path = self.tmp_dir / "test_api_config.yaml"
        
        mock_config = """
inference:
  active_provider: "openai"
  model: "gpt-4"
  model_fallback: "copilot"
  openai:
    api_key: "sk-mock"
storage:
  db_path: "tmp/test_api_memory.db"
  audit_log_path: "tmp/test_api_lineage.jsonl"
api:
  api_key: "secret-key"
"""
        with open(self.mock_config_path, "w") as f:
            f.write(mock_config)
            
        # Initialize resources in server
        from src.api.server import app, init_resources
        init_resources(config_path=str(self.mock_config_path))
        self.client = TestClient(app)

    def tearDown(self):
        if self.mock_config_path.exists():
            self.mock_config_path.unlink()
            
        # Clean up database files if created
        db_file = self.tmp_dir / "test_api_memory.db"
        if db_file.exists():
            db_file.unlink()
        log_file = self.tmp_dir / "test_api_lineage.jsonl"
        if log_file.exists():
            log_file.unlink()

    def test_auth_failure(self):
        """Verifies endpoints reject requests with missing or invalid keys."""
        resp = self.client.post("/submit_goal", json={"goal": "some task"})
        self.assertEqual(resp.status_code, 401)
        
        resp_invalid = self.client.post(
            "/submit_goal",
            json={"goal": "some task"},
            headers={"X-Agent-API-Key": "wrong-key"}
        )
        self.assertEqual(resp_invalid.status_code, 401)

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.orchestrator.OrchestrationEngine.execute_loop")
    def test_submit_goal_success(self, mock_execute, mock_decompose):
        """Verifies submission of goals with correct auth keys returns queued status."""
        mock_decompose.return_value = [{"id": "t1"}]
        mock_execute.return_value = "completed"
        
        headers = {"X-Agent-API-Key": "secret-key"}
        resp = self.client.post(
            "/submit_goal",
            json={"goal": "Build app module"},
            headers=headers
        )
        
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "queued")
        self.assertIn("session_id", data)
        self.assertGreaterEqual(data["queue_position"], 1)
        
        # Test status endpoint
        session_id = data["session_id"]
        status_resp = self.client.get(f"/status/{session_id}", headers=headers)
        self.assertEqual(status_resp.status_code, 200)
        self.assertIn(status_resp.json()["status"], ["queued", "running", "completed", "decomposing"])

    def test_approve_gated_task(self):
        """Verifies submitting human gating decisions."""
        headers = {"X-Agent-API-Key": "secret-key"}
        
        # Setup session in active memory
        from src.api.server import active_sessions
        active_sessions["mock-uuid-99"] = {
            "session_id": "mock-uuid-99",
            "status": "blocked",
            "goal": "gated run"
        }
        
        resp = self.client.post(
            "/approve/mock-uuid-99",
            json={"approved": True, "notes": "Approved by user"},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["approved"])
        self.assertEqual(data["status"], "approval_registered")

    def test_teams_webhook(self):
        """Verifies webhook formatting from MS Teams commands."""
        headers = {"X-Agent-API-Key": "secret-key"}
        
        # 1. Normal prompt
        resp = self.client.post(
            "/teams/webhook",
            json={"text": "hello agent", "from_user": "User1"},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Received: 'hello agent'", resp.json()["reply"])
        
        # 2. Goal submission prompt
        resp_goal = self.client.post(
            "/teams/webhook",
            json={"text": "goal: Compile codebase", "from_user": "User1"},
            headers=headers
        )
        self.assertEqual(resp_goal.status_code, 200)
        self.assertIn("Goal queued successfully", resp_goal.json()["reply"])

if __name__ == "__main__":
    unittest.main()
