import os
import base64
import requests
from typing import Dict, Any, List, Optional

class AzureDevOpsClient:
    def __init__(
        self,
        organization: str,
        project: str,
        repository_id: str,
        personal_access_token: Optional[str] = None
    ):
        self.organization = organization
        self.project = project
        self.repository_id = repository_id
        
        # Load PAT from arguments or environment variable
        pat = personal_access_token or os.environ.get("ADO_PERSONAL_ACCESS_TOKEN", "")
        
        # Basic auth requires Base64 of ":PAT"
        auth_str = f":{pat}"
        self.auth_header = {
            "Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}"
        }
        self.base_url = f"https://dev.azure.com/{organization}/{project}/_apis"

    def query_backlog(self, wiql_query: str) -> List[Dict[str, Any]]:
        """Queries the ADO backlog using WIQL (Work Item Query Language)."""
        url = f"{self.base_url}/wit/wiql?api-version=7.1"
        payload = {"query": wiql_query}
        headers = {**self.auth_header, "Content-Type": "application/json"}
        
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        work_items = data.get("workItems", [])
        return work_items

    def get_work_item(self, work_item_id: int) -> Dict[str, Any]:
        """Retrieves a single work item's details."""
        url = f"{self.base_url}/wit/workitems/{work_item_id}?api-version=7.1"
        response = requests.get(url, headers=self.auth_header)
        response.raise_for_status()
        return response.json()

    def update_work_item_state(self, work_item_id: int, state: str) -> Dict[str, Any]:
        """Updates the 'System.State' field of a work item using JSON patch."""
        url = f"{self.base_url}/wit/workitems/{work_item_id}?api-version=7.1"
        payload = [
            {
                "op": "add",
                "path": "/fields/System.State",
                "value": state
            }
        ]
        headers = {**self.auth_header, "Content-Type": "application/json-patch+json"}
        
        response = requests.patch(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def create_pull_request(self, source_branch: str, target_branch: str, title: str, description: str) -> Dict[str, Any]:
        """Initiates a Pull Request in the target git repository."""
        # Ensure refs naming is correct
        source_ref = source_branch if source_branch.startswith("refs/") else f"refs/heads/{source_branch}"
        target_ref = target_branch if target_branch.startswith("refs/") else f"refs/heads/{target_branch}"
        
        url = f"https://dev.azure.com/{self.organization}/{self.project}/_apis/git/repositories/{self.repository_id}/pullrequests?api-version=7.1"
        
        payload = {
            "sourceRefName": source_ref,
            "targetRefName": target_ref,
            "title": title,
            "description": description
        }
        headers = {**self.auth_header, "Content-Type": "application/json"}
        
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
