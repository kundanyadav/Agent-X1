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
            
        # Clean up tasks_plan.md in workspace root
        tasks_plan = pathlib.Path(__file__).parent.parent / "tasks_plan.md"
        if tasks_plan.exists():
            tasks_plan.unlink()

    def test_auth_failure(self):
        """Verifies endpoints reject requests with missing or invalid keys."""
        resp = self.client.post("/v1/tasks/run", json={"goal": "some task"})
        self.assertEqual(resp.status_code, 401)
        
        resp_invalid = self.client.post(
            "/v1/tasks/run",
            json={"goal": "some task"},
            headers={"X-Agent-API-Key": "wrong-key"}
        )
        self.assertEqual(resp_invalid.status_code, 401)

    def test_auth_fail_secure_when_unset(self):
        """Verifies that if the API key resolves to an empty string (e.g. unset env), it rejects instead of bypassing."""
        # Re-initialize resources with an empty API key in config
        mock_config_empty = """
inference:
  active_provider: "openai"
  model: "gpt-4"
  model_fallback: "copilot"
  openai:
    api_key: "sk-mock"
storage:
  db_path: "tmp/test_api_memory_empty.db"
  audit_log_path: "tmp/test_api_lineage_empty.jsonl"
api:
  api_key: ""
"""
        empty_config_path = self.tmp_dir / "test_api_config_empty.yaml"
        with open(empty_config_path, "w") as f:
            f.write(mock_config_empty)
            
        try:
            from src.api.server import app, init_resources
            init_resources(config_path=str(empty_config_path))
            client_empty = TestClient(app)
            
            # Request should be rejected (401) even if no key is provided, because the key is empty/unset
            resp = client_empty.post("/v1/tasks/run", json={"goal": "some task"})
            self.assertEqual(resp.status_code, 401)
            self.assertIn("unset in environment", resp.json()["detail"])
        finally:
            if empty_config_path.exists():
                empty_config_path.unlink()
            db_file = self.tmp_dir / "test_api_memory_empty.db"
            if db_file.exists():
                db_file.unlink()
            log_file = self.tmp_dir / "test_api_lineage_empty.jsonl"
            if log_file.exists():
                log_file.unlink()
            # Restore the original setup config
            init_resources(config_path=str(self.mock_config_path))

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.orchestrator.OrchestrationEngine.execute_loop")
    def test_submit_goal_success(self, mock_execute, mock_decompose):
        """Verifies submission of goals with correct auth keys returns queued status."""
        mock_decompose.return_value = [{"id": "t1"}]
        mock_execute.return_value = "completed"
        
        headers = {"X-Agent-API-Key": "secret-key"}
        resp = self.client.post(
            "/v1/tasks/run",
            json={"goal": "Build app module"},
            headers=headers
        )
        
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "queued")
        self.assertIn("task_id", data)
        self.assertIn("message", data)
        self.assertGreaterEqual(data["queue_position"], 1)
        
        # Test status endpoint
        task_id = data["task_id"]
        status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
        self.assertEqual(status_resp.status_code, 200)
        self.assertIn(status_resp.json()["status"], ["queued", "running", "completed", "decomposing"])
        self.assertEqual(status_resp.json()["task_id"], task_id)

        # Approve the task to unblock the background thread and allow it to finish
        self.client.post(
            f"/v1/tasks/{task_id}/approve",
            json={"approved": True, "notes": "Unblock test task"},
            headers=headers
        )

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
            "/v1/tasks/mock-uuid-99/approve",
            json={"approved": True, "notes": "Approved by user"},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["approved"])
        self.assertEqual(data["status"], "approval_registered")
        self.assertEqual(data["task_id"], "mock-uuid-99")

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

        # Approve the task to unblock the background thread
        reply = resp_goal.json()["reply"]
        session_id = reply.split("Session ID: ")[1].strip()
        self.client.post(
            f"/v1/tasks/{session_id}/approve",
            json={"approved": True, "notes": "Unblock Teams task"},
            headers=headers
        )

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.orchestrator.OrchestrationEngine.execute_loop")
    def test_planning_gate_synchronization(self, mock_execute, mock_decompose):
        """Verifies that the background thread blocks on the goal-planning gate and resumes on approval."""
        mock_decompose.return_value = [{"id": "t1", "name": "Task 1", "depends_on": [], "worker": "codeworker", "args": {}}]
        mock_execute.return_value = "completed"
        
        headers = {"X-Agent-API-Key": "secret-key"}
        
        # 1. Submit goal
        resp = self.client.post(
            "/v1/tasks/run",
            json={"goal": "Sync Test"},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)
        task_id = resp.json()["task_id"]
        
        # 2. Wait/Poll for status to become awaiting_approval
        import time
        for _ in range(50):
            status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
            if status_resp.json()["status"] == "awaiting_approval":
                break
            time.sleep(0.05)
            
        status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
        self.assertEqual(status_resp.json()["status"], "awaiting_approval")
        
        # 3. Call approve endpoint
        approve_resp = self.client.post(
            f"/v1/tasks/{task_id}/approve",
            json={"approved": True, "notes": "Approved!"},
            headers=headers
        )
        self.assertEqual(approve_resp.status_code, 200)
        
        # 4. Wait/Poll for status to become completed
        for _ in range(50):
            status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
            if status_resp.json()["status"] == "completed":
                break
            time.sleep(0.05)
            
        status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
        self.assertEqual(status_resp.json()["status"], "completed")

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.workers.CodeWorker.execute_task")
    def test_task_level_gate_synchronization(self, mock_execute_task, mock_decompose):
        """Verifies that the execution loop pauses on a Major change and resumes upon task approval."""
        gating_config_path = self.tmp_dir / "test_api_gating_config.yaml"
        mock_gating_config = """
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
gating:
  auto_approve_planning: true
  auto_approve_tasks: false
"""
        with open(gating_config_path, "w") as f:
            f.write(mock_gating_config)

        from src.api.server import init_resources
        init_resources(config_path=str(gating_config_path))

        try:
            # Task triggers `is_major_change` (worker = codeworker, task_description contains "delete file")
            mock_decompose.return_value = [
                {
                    "id": "t1",
                    "name": "Delete Temp Files",
                    "depends_on": [],
                    "worker": "codeworker",
                    "args": {"task_description": "delete file workspace.db"}
                }
            ]
            mock_execute_task.return_value = {"status": "success"}

            headers = {"X-Agent-API-Key": "secret-key"}

            # 1. Submit goal
            resp = self.client.post(
                "/v1/tasks/run",
                json={"goal": "Task Gating Test"},
                headers=headers
            )
            self.assertEqual(resp.status_code, 200)
            task_id = resp.json()["task_id"]

            # 2. Wait/Poll for status to become paused_for_task_approval
            import time
            paused = False
            for i in range(50):
                status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
                status_data = status_resp.json()
                print(f"Polling {i}: {status_data}")
                if status_data["status"] == "paused_for_task_approval":
                    paused = True
                    break
                time.sleep(0.05)

            self.assertTrue(paused, f"Task status should be paused_for_task_approval, got {status_data}")

            # 3. Call approve endpoint for the blocked task
            approve_resp = self.client.post(
                f"/v1/tasks/{task_id}/approve",
                json={"approved": True, "notes": "Approved task execution!"},
                headers=headers
            )
            self.assertEqual(approve_resp.status_code, 200)

            # 4. Wait/Poll for status to become completed
            completed = False
            for _ in range(50):
                status_resp = self.client.get(f"/v1/tasks/{task_id}/status", headers=headers)
                if status_resp.json()["status"] == "completed":
                    completed = True
                    break
                time.sleep(0.05)

            self.assertTrue(completed, "Task status should be completed")
            mock_execute_task.assert_called_once()

        finally:
            if gating_config_path.exists():
                gating_config_path.unlink()
            # Restore original resources configuration
            init_resources(config_path=str(self.mock_config_path))

    def test_get_skills(self):
        """Verifies lists of learned skills returns 200."""
        headers = {"X-Agent-API-Key": "secret-key"}
        resp = self.client.get("/v1/skills", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("skills", resp.json())
        
    def test_search_memory(self):
        """Verifies search memory query calls semantic memory search."""
        headers = {"X-Agent-API-Key": "secret-key"}
        resp = self.client.get("/v1/memory/search?query=test", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.json())

    @patch("src.core.orchestrator.OrchestrationEngine.decompose_goal")
    @patch("src.core.orchestrator.OrchestrationEngine.execute_loop")
    def test_queue_position_updates(self, mock_execute, mock_decompose):
        """Verifies that the queue position adjusts dynamically as tasks move through the queue."""
        mock_decompose.return_value = [{"id": "t1", "name": "Task 1", "depends_on": [], "worker": "codeworker", "args": {}}]
        mock_execute.return_value = "completed"
        
        headers = {"X-Agent-API-Key": "secret-key"}
        
        # 1. Submit first task (will run and block on planning gate)
        resp1 = self.client.post(
            "/v1/tasks/run",
            json={"goal": "Queue Test 1"},
            headers=headers
        )
        self.assertEqual(resp1.status_code, 200)
        task_id1 = resp1.json()["task_id"]
        
        # 2. Wait until first task is active (awaiting_approval status, queue_position = 0)
        import time
        for _ in range(50):
            status_resp1 = self.client.get(f"/v1/tasks/{task_id1}/status", headers=headers)
            if status_resp1.json()["status"] == "awaiting_approval":
                break
            time.sleep(0.05)
            
        status1_initial = self.client.get(f"/v1/tasks/{task_id1}/status", headers=headers).json()
        self.assertEqual(status1_initial["status"], "awaiting_approval")
        self.assertEqual(status1_initial["queue_position"], 0)
        
        # 3. Submit second task (will enter queue, since worker thread is blocked on first task)
        resp2 = self.client.post(
            "/v1/tasks/run",
            json={"goal": "Queue Test 2"},
            headers=headers
        )
        self.assertEqual(resp2.status_code, 200)
        task_id2 = resp2.json()["task_id"]
        
        # Verify second task initially gets queue position 1
        status2_initial = self.client.get(f"/v1/tasks/{task_id2}/status", headers=headers).json()
        self.assertEqual(status2_initial["status"], "queued")
        self.assertEqual(status2_initial["queue_position"], 1)
        
        # 4. Approve first task to unblock it
        self.client.post(
            f"/v1/tasks/{task_id1}/approve",
            json={"approved": True, "notes": "Approve first task"},
            headers=headers
        )
        
        # 5. Wait until second task is picked up by the worker and moves to awaiting_approval (queue_position = 0)
        for _ in range(50):
            status_resp2 = self.client.get(f"/v1/tasks/{task_id2}/status", headers=headers)
            if status_resp2.json()["status"] == "awaiting_approval":
                break
            time.sleep(0.05)
            
        status2_after = self.client.get(f"/v1/tasks/{task_id2}/status", headers=headers).json()
        self.assertEqual(status2_after["status"], "awaiting_approval")
        self.assertEqual(status2_after["queue_position"], 0)
        
        # 6. Approve second task to clean up background thread
        self.client.post(
            f"/v1/tasks/{task_id2}/approve",
            json={"approved": True, "notes": "Approve second task"},
            headers=headers
        )

if __name__ == "__main__":
    unittest.main()
