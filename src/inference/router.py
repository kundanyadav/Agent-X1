import os
import sys
import json
import time
import re
import pathlib
import requests
from typing import List, Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class InferenceRouter:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = pathlib.Path(config_path)
        self.config = self._load_config()
        self.copilot_token_cache = None
        self.copilot_token_expires = 0

    def _load_config(self) -> Dict[str, Any]:
        """Loads yaml config and resolves environment variables like ${ENV_VAR}."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found at: {self.config_path}")
            
        with open(self.config_path, "r") as f:
            content = f.read()
            
        # Match and replace environment variables ${VAR_NAME}
        pattern = re.compile(r"\$\{(\w+)\}")
        matches = pattern.findall(content)
        for var in matches:
            val = os.environ.get(var, "")
            content = content.replace(f"${{{var}}}", val)
            
        import yaml
        return yaml.safe_load(content)

    def _get_copilot_oauth_token(self) -> str:
        """Locates and extracts the GitHub oauth token (ghu_ token) from local host files."""
        home = pathlib.Path.home()
        
        # Determine GHE host if configured
        inf_cfg = self.config.get("inference", {})
        copilot_cfg = inf_cfg.get("copilot", {})
        ghe_host = os.environ.get("GITHUB_ENTERPRISE_HOST") or copilot_cfg.get("enterprise_host") or "github.com"
        
        # Check standard config file paths based on host OS
        paths = []
        if sys.platform.startswith("win"):
            # Windows config paths
            appdata = os.environ.get("APPDATA")
            if appdata:
                paths.append(pathlib.Path(appdata) / "github-copilot" / "hosts.json")
            paths.append(home / ".config" / "gh" / "hosts.yml")
        else:
            # Linux and macOS config paths
            paths.append(home / ".config" / "github-copilot" / "hosts.json")
            paths.append(home / ".config" / "gh" / "hosts.yml")
            
        for path in paths:
            if not path.exists():
                continue
                
            try:
                if path.suffix == ".json":
                    with open(path, "r") as f:
                        data = json.load(f)
                    github_config = data.get(ghe_host) or data.get("github.com", {})
                    token = github_config.get("oauth_token")
                    if token:
                        return token
                elif path.suffix in [".yml", ".yaml"]:
                    with open(path, "r") as f:
                        import yaml
                        data = yaml.safe_load(f)
                    github_config = data.get(ghe_host) or data.get("github.com", {})
                    token = github_config.get("oauth_token")
                    if token:
                        return token
            except Exception as e:
                # Log error silently and try fallback paths
                continue
                
        # Fallback to environment variable if configuration files are missing
        token = os.environ.get("GITHUB_COPILOT_OAUTH_TOKEN")
        if token:
            return token
            
        raise ValueError(
            f"GitHub Copilot OAuth token not found for host '{ghe_host}' in config files or GITHUB_COPILOT_OAUTH_TOKEN environment variable. "
            "Please run 'gh auth login' or ensure you are signed into GitHub Copilot in VS Code."
        )

    def _refresh_copilot_session_token(self) -> str:
        """Swaps the ghu_ oauth token for an ephemeral Copilot JWT token."""
        # Use cached token if it has not expired yet (with a 5 minute safety margin)
        current_time = time.time()
        if self.copilot_token_cache and current_time < (self.copilot_token_expires - 300):
            return self.copilot_token_cache
            
        oauth_token = self._get_copilot_oauth_token()
        headers = {
            "Authorization": f"Bearer {oauth_token}",
            "User-Agent": "GithubCopilot/1.250.0",
            "Accept": "application/json"
        }
        
        # Determine GHE host if configured
        inf_cfg = self.config.get("inference", {})
        copilot_cfg = inf_cfg.get("copilot", {})
        ghe_host = os.environ.get("GITHUB_ENTERPRISE_HOST") or copilot_cfg.get("enterprise_host") or "github.com"
        
        if ghe_host == "github.com":
            url = "https://api.github.com/copilot_internal/v2/token"
        elif ghe_host.endswith(".ghe.com"):
            if ghe_host.startswith("api."):
                url = f"https://{ghe_host}/copilot_internal/v2/token"
            else:
                url = f"https://api.{ghe_host}/copilot_internal/v2/token"
        else:
            # Standard GHES endpoint
            url = f"https://{ghe_host}/api/v3/copilot_internal/v2/token"
            
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to authenticate with GitHub Copilot token exchange endpoint ({url}): "
                f"HTTP {response.status_code} - {response.text}"
            )
            
        data = response.json()
        token = data.get("token")
        expires_at = data.get("expires_at", current_time + 1500)  # Default 25 min fallback
        
        self.copilot_token_cache = token
        self.copilot_token_expires = expires_at
        
        return token

    def chat_completions(self, messages: List[Dict[str, str]], stream: bool = False, **kwargs) -> Any:
        """Routes chat completions request to active provider, with rate-limit fallback to BYOK."""
        inf_cfg = self.config.get("inference", {})
        active_provider = inf_cfg.get("active_provider", "copilot")
        
        try:
            return self._execute_chat_completions(active_provider, messages, stream, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                print(f"API Error Response: {e.response.text}", file=sys.stderr)
            # Check if we hit rate limits (HTTP 429) or other API exceptions
            if e.response is not None and e.response.status_code == 429:
                fallback_provider = inf_cfg.get("fallback_provider")
                fallback_model = inf_cfg.get("fallback_model")
                if fallback_provider and fallback_provider != active_provider:
                    print(f"Warning: Primary provider '{active_provider}' rate-limited. Falling back to '{fallback_provider}'...", file=sys.stderr)
                    if fallback_model:
                        kwargs["model"] = fallback_model
                    return self._execute_chat_completions(fallback_provider, messages, stream, **kwargs)
            raise e

    def _execute_chat_completions(self, provider: str, messages: List[Dict[str, str]], stream: bool = False, **kwargs) -> Any:
        """Low-level driver handling specific LLM API payloads."""
        inf_config = self.config.get("inference", {})
        model = kwargs.get("model", inf_config.get("model", "gpt-4o"))
        temperature = kwargs.get("temperature", 0.1)
        
        if provider == "copilot":
            session_token = self._refresh_copilot_session_token()
            headers = {
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
                "editor-version": "vscode/1.86.0",
                "editor-plugin-version": "copilot/1.250.0",
                "user-agent": "GithubCopilot/1.250.0"
            }
            # Allow API URL override for custom GHE proxy settings if necessary
            copilot_cfg = inf_config.get("copilot", {})
            ghe_host = os.environ.get("GITHUB_ENTERPRISE_HOST") or copilot_cfg.get("enterprise_host") or "github.com"
            if os.environ.get("GITHUB_COPILOT_API_URL") or copilot_cfg.get("api_url"):
                url = os.environ.get("GITHUB_COPILOT_API_URL") or copilot_cfg.get("api_url")
            elif ghe_host.endswith(".ghe.com"):
                if ghe_host.startswith("copilot-api."):
                    url = f"https://{ghe_host}/chat/completions"
                else:
                    url = f"https://copilot-api.{ghe_host}/chat/completions"
            else:
                url = "https://api.githubcopilot.com/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            response = requests.post(url, json=payload, headers=headers, stream=stream)
            response.raise_for_status()
            
            if stream:
                return response
            return response.json()
            
        elif provider == "openai":
            api_key = inf_config.get("openai", {}).get("api_key")
            base_url = inf_config.get("openai", {}).get("base_url", "https://api.openai.com/v1")
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            response = requests.post(url, json=payload, headers=headers, stream=stream)
            response.raise_for_status()
            
            if stream:
                return response
            return response.json()
            
        elif provider == "ollama":
            base_url = inf_config.get("ollama", {}).get("base_url", "http://localhost:11434/v1")
            headers = {
                "Content-Type": "application/json"
            }
            url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            response = requests.post(url, json=payload, headers=headers, stream=stream)
            response.raise_for_status()
            
            if stream:
                return response
            return response.json()
            
        elif provider == "gemini":
            api_key = inf_config.get("gemini", {}).get("api_key")
            # Wrap Gemini API completion to match OpenAI schema
            headers = {
                "Content-Type": "application/json"
            }
            # Standard OpenAI compatibility endpoint for Gemini API
            url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={api_key}"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            response = requests.post(url, json=payload, headers=headers, stream=stream)
            response.raise_for_status()
            
            if stream:
                return response
            return response.json()
            
        elif provider == "openrouter":
            api_key = inf_config.get("openrouter", {}).get("api_key")
            base_url = inf_config.get("openrouter", {}).get("base_url", "https://openrouter.ai/api/v1")
            preset = inf_config.get("openrouter", {}).get("preset")
            
            req_model = model
            if preset:
                if preset.startswith("@preset/") or preset.startswith("preset:"):
                    req_model = preset
                else:
                    req_model = f"@preset/{preset}"
                    
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/google-deepmind/Agent-X1",
                "X-Title": "Agent-X1"
            }
            url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": req_model,
                "messages": messages,
                "temperature": temperature,
                "stream": stream
            }
            response = requests.post(url, json=payload, headers=headers, stream=stream)
            response.raise_for_status()
            
            if stream:
                return response
            return response.json()
            
        else:
            raise ValueError(f"Unknown or unsupported inference provider: {provider}")
