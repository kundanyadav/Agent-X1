import unittest
import sys
import pathlib
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.integrations.ado import AzureDevOpsClient

class TestAzureDevOpsClient(unittest.TestCase):
    def setUp(self):
        self.client = AzureDevOpsClient(
            organization="test-org",
            project="test-proj",
            repository_id="test-repo-uuid",
            personal_access_token="test-pat-123"
        )

    @patch("requests.post")
    def test_query_backlog(self, mock_post):
        """Verifies query_backlog formats request and basic auth headers correctly."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "workItems": [
                {"id": 101, "url": "https://dev.azure.com/..."},
                {"id": 102, "url": "https://dev.azure.com/..."},
            ]
        }
        mock_post.return_value = mock_response
        
        wiql = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'To Do'"
        work_items = self.client.query_backlog(wiql)
        
        self.assertEqual(len(work_items), 2)
        self.assertEqual(work_items[0]["id"], 101)
        
        # Verify the POST details
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("wit/wiql", args[0])
        self.assertEqual(kwargs["json"], {"query": wiql})
        self.assertIn("Authorization", kwargs["headers"])

    @patch("requests.get")
    def test_get_work_item(self, mock_get):
        """Verifies retrieval of work items."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": 101,
            "fields": {"System.Title": "Implement audit log"}
        }
        mock_get.return_value = mock_response
        
        wi = self.client.get_work_item(101)
        
        self.assertEqual(wi["id"], 101)
        self.assertEqual(wi["fields"]["System.Title"], "Implement audit log")
        
        mock_get.assert_called_once_with(
            "https://dev.azure.com/test-org/test-proj/_apis/wit/workitems/101?api-version=7.1",
            headers=self.client.auth_header
        )

    @patch("requests.patch")
    def test_update_work_item_state(self, mock_patch):
        """Verifies patching work item System.State using application/json-patch+json."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 101}
        mock_patch.return_value = mock_response
        
        res = self.client.update_work_item_state(101, "Doing")
        
        self.assertEqual(res["id"], 101)
        mock_patch.assert_called_once()
        args, kwargs = mock_patch.call_args
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json-patch+json")
        self.assertEqual(kwargs["json"], [
            {
                "op": "add",
                "path": "/fields/System.State",
                "value": "Doing"
            }
        ])

    @patch("requests.post")
    def test_create_pull_request(self, mock_post):
        """Verifies posting a new PR to ADO repository."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"pullRequestId": 45}
        mock_post.return_value = mock_response
        
        res = self.client.create_pull_request(
            source_branch="feature/test-101",
            target_branch="main",
            title="Update audit logging",
            description="Adds lineage.py"
        )
        
        self.assertEqual(res["pullRequestId"], 45)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("git/repositories/test-repo-uuid/pullrequests", args[0])
        self.assertEqual(kwargs["json"], {
            "sourceRefName": "refs/heads/feature/test-101",
            "targetRefName": "refs/heads/main",
            "title": "Update audit logging",
            "description": "Adds lineage.py"
        })

if __name__ == "__main__":
    unittest.main()
