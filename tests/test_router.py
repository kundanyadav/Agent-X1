import unittest
import os
import json
import pathlib
import sys
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.inference.router import InferenceRouter

class TestInferenceRouter(unittest.TestCase):
    def setUp(self):
        # Ensure tmp folder exists for local test files
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.mock_config_path = self.tmp_dir / "test_config.yaml"
        
        # Write temporary mock configuration
        mock_config = """
inference:
  active_provider: "openai"
  model: "gpt-4"
  model_fallback: "copilot"
  openai:
    api_key: "${MOCK_OPENAI_KEY}"
    base_url: "https://api.openai.com/v1"
"""
        with open(self.mock_config_path, "w") as f:
            f.write(mock_config)
            
        os.environ["MOCK_OPENAI_KEY"] = "sk-mock-12345"

    def tearDown(self):
        # Cleanup mock config file
        if self.mock_config_path.exists():
            self.mock_config_path.unlink()

    def test_config_env_expansion(self):
        """Verifies that environment variables in config.yaml are expanded correctly."""
        router = InferenceRouter(config_path=str(self.mock_config_path))
        api_key = router.config.get("inference", {}).get("openai", {}).get("api_key")
        self.assertEqual(api_key, "sk-mock-12345")

    @patch("pathlib.Path.exists")
    @patch("builtins.open")
    def test_get_copilot_oauth_token(self, mock_open, mock_exists):
        """Verifies oauth token extraction from mock hosts.json file."""
        mock_exists.return_value = True
        
        mock_hosts_json = json.dumps({
            "github.com": {
                "user": "test-user",
                "oauth_token": "ghu_mock_token_abcdef"
            }
        })
        
        # Configure open mock to return the hosts json structure
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = mock_hosts_json
        mock_file.__enter__.return_value.suffix = ".json"
        mock_open.return_value = mock_file
        
        router = InferenceRouter(config_path=str(self.mock_config_path))
        
        with patch.object(pathlib.Path, "suffix", ".json"):
            token = router._get_copilot_oauth_token()
            self.assertEqual(token, "ghu_mock_token_abcdef")

    @patch("requests.get")
    def test_refresh_copilot_session_token(self, mock_get):
        """Verifies token handshake exchange from oauth_token to JWT session token."""
        # Set up mock response from Github API v2 token swap
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "token": "ghu_session_jwt_xxx",
            "expires_at": 1813953600
        }
        mock_get.return_value = mock_resp
        
        router = InferenceRouter(config_path=str(self.mock_config_path))
        
        with patch.object(router, "_get_copilot_oauth_token", return_value="ghu_oauth_abc"):
            session_token = router._refresh_copilot_session_token()
            self.assertEqual(session_token, "ghu_session_jwt_xxx")
            self.assertEqual(router.copilot_token_cache, "ghu_session_jwt_xxx")

if __name__ == "__main__":
    unittest.main()
