import unittest
import json
import pathlib
import sys
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.core.orchestrator import OrchestrationEngine
from src.gateways.gateway import CliGateway
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager

class TestInteractivePlanning(unittest.TestCase):
    def setUp(self):
        self.mock_router = MagicMock(spec=InferenceRouter)
        self.mock_tools = MagicMock(spec=ToolRunner)
        self.mock_memory = MagicMock(spec=MemoryManager)
        
        self.engine = OrchestrationEngine(
            router=self.mock_router,
            tools=self.mock_tools,
            memory=self.mock_memory
        )

    def test_generate_planning_proposal_initial(self):
        """Verifies that generate_planning_proposal submits initial prompt to LLM when history is empty."""
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Proposed plan details here..."
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response
        
        history = []
        proposal = self.engine.generate_planning_proposal("Create a new feature", history)
        
        self.assertEqual(proposal, "Proposed plan details here...")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "system")
        self.assertEqual(history[1]["role"], "user")
        self.assertIn("Create a new feature", history[1]["content"])

    def test_generate_planning_proposal_with_history(self):
        """Verifies that generate_planning_proposal submits full history to LLM."""
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Refined plan details here..."
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response
        
        history = [
            {"role": "system", "content": "System instructions"},
            {"role": "user", "content": "Init request"},
            {"role": "assistant", "content": "Init proposal"},
            {"role": "user", "content": "Make X faster"}
        ]
        
        proposal = self.engine.generate_planning_proposal("Goal", history)
        
        self.assertEqual(proposal, "Refined plan details here...")
        self.assertEqual(len(history), 4)  # No extra elements added, just passed directly
        self.mock_router.chat_completions.assert_called_once_with(messages=history, temperature=0.2)

    def test_decompose_plan_into_tasks(self):
        """Verifies decomposing a markdown plan into structured JSON tasks."""
        mock_tasks = [
            {
                "id": "t-1",
                "name": "Add caching",
                "depends_on": [],
                "worker": "codeworker",
                "args": {"task_description": "implement cache model"}
            }
        ]
        
        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps(mock_tasks)
                }
            }]
        }
        self.mock_router.chat_completions.return_value = mock_response
        
        tasks = self.engine.decompose_plan_into_tasks("Finalized plan content")
        
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "t-1")
        self.assertEqual(tasks[0]["worker"], "codeworker")

    def test_decompose_plan_into_tasks_fallback(self):
        """Verifies fallback tasks when LLM fails to return valid JSON."""
        self.mock_router.chat_completions.return_value = {
            "choices": [{
                "message": {
                    "content": "Not valid JSON at all!"
                }
            }]
        }
        
        tasks = self.engine.decompose_plan_into_tasks("My Finalized Plan")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "task-fallback-plan-1")
        self.assertIn("My Finalized Plan", tasks[0]["args"]["task_description"])

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_cli_gateway_interactive_planning_flow(self, mock_engine_class, mock_router_class, mock_input):
        """Mocks interactive feedback/approval inside the CliGateway planning loop."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-123"
        mock_engine_instance.generate_planning_proposal.side_effect = [
            "Initial Plan Proposed",
            "Refined Plan Proposed"
        ]
        mock_engine_instance.decompose_plan_into_tasks.return_value = [
            {"id": "t-1", "name": "Task 1", "worker": "codeworker", "args": {}}
        ]
        
        # Scenario: User submits 1 feedback comment, then approves
        mock_input.side_effect = [
            "make it better",  # First prompt: feedback
            "approved for build" # Second prompt: strict approve
        ]
        
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal("Test Goal", dry_run=True, interactive=True)
        
        # Verify result is completed (due to dry_run=True after approval)
        self.assertEqual(status, "completed")
        self.assertEqual(mock_input.call_count, 2)
        mock_engine_instance.decompose_plan_into_tasks.assert_called_once_with("Refined Plan Proposed")

    @patch("builtins.input")
    @patch("src.gateways.gateway.InferenceRouter")
    @patch("src.gateways.gateway.OrchestrationEngine")
    def test_cli_gateway_interactive_planning_missing_goal(self, mock_engine_class, mock_router_class, mock_input):
        """Verifies that gateway prompts for goal if omitted in interactive mode."""
        mock_router_instance = MagicMock()
        mock_router_class.return_value = mock_router_instance
        mock_router_instance.config = {
            "storage": {"db_path": "tmp/test_mem.db", "audit_log_path": "tmp/test_audit.jsonl"}
        }
        
        mock_engine_instance = MagicMock()
        mock_engine_class.return_value = mock_engine_instance
        mock_engine_instance.generate_correlation_id.return_value = "mock-id-123"
        mock_engine_instance.generate_planning_proposal.return_value = "Plan Proposed"
        mock_engine_instance.decompose_plan_into_tasks.return_value = []
        
        # Scenario: First input is the goal, second input is approval
        mock_input.side_effect = [
            "My Dynamic Goal",
            "approved for build"
        ]
        
        # Simulate gateway.py main parsing logic
        goal = None
        interactive = True
        
        if not goal and interactive:
            goal = mock_input() # Mimics the input prompt for goal in main()
            
        gateway = CliGateway(config_path="config.yaml")
        status = gateway.run_goal(goal, dry_run=True, interactive=interactive)
        
        self.assertEqual(status, "completed")
        self.assertEqual(goal, "My Dynamic Goal")

    def test_learn_user_fact_with_triggers(self):
        """Verifies learn_user_fact_if_needed commits fact to memory for trigger terms."""
        # Setup trigger messages
        test_inputs = [
            "important: we must run builds first",
            "This is interesting, we need to double check files",
            "Let's keep in mind the environment requirements",
            "make sure to install dependencies",
            "Remember that this has a 30 second timeout"
        ]
        
        for user_input in test_inputs:
            self.mock_memory.reset_mock()
            saved = self.engine.learn_user_fact_if_needed(user_input)
            self.assertTrue(saved)
            self.mock_memory.learn_fact.assert_called_once_with(
                agent_owner="orchestrator",
                category="user_preference",
                issue="user preference during planning",
                solution=user_input
            )

    def test_learn_user_fact_without_triggers(self):
        """Verifies learn_user_fact_if_needed does not commit standard feedback to memory."""
        test_input = "can you make it faster"
        saved = self.engine.learn_user_fact_if_needed(test_input)
        self.assertFalse(saved)
        self.mock_memory.learn_fact.assert_not_called()

if __name__ == "__main__":
    unittest.main()
