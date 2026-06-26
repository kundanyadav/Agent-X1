import json
import re
from typing import Dict, Any, List, Optional
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.integrations.ado import AzureDevOpsClient

class BaseWorker:
    def __init__(
        self,
        agent_owner: str,
        router: InferenceRouter,
        tools: ToolRunner,
        memory: Optional[MemoryManager] = None
    ):
        self.agent_owner = agent_owner
        self.router = router
        self.tools = tools
        self.memory = memory

    def _get_llm_response(self, system_prompt: str, user_prompt: str) -> str:
        """Helper to invoke the chat completion endpoint and extract content."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        resp = self.router.chat_completions(messages=messages, temperature=0.1)
        if isinstance(resp, dict) and "choices" in resp:
            return resp["choices"][0]["message"]["content"]
        return str(resp)


class CodeWorker(BaseWorker):
    def __init__(self, router: InferenceRouter, tools: ToolRunner, memory: Optional[MemoryManager] = None):
        super().__init__("codeworker", router, tools, memory)

    def execute_task(self, task_description: str, correlation_id: str) -> Dict[str, Any]:
        """Uses LLM to decide on code modifications and executes write or patch file actions."""
        system_prompt = (
            "You are CodeWorker, a specialized subagent responsible for editing, writing, and patching code files.\n"
            "You MUST output your decision in JSON format matching one of these schemas:\n"
            "1) To write a new file:\n"
            "   {\"action\": \"write_file\", \"path\": \"<file_path>\", \"content\": \"<file_content>\", \"justification\": \"<why>\"}\n"
            "2) To patch an existing file:\n"
            "   {\"action\": \"patch_file\", \"path\": \"<file_path>\", \"search\": \"<exact_block_to_replace>\", \"replace\": \"<replacement_block>\", \"justification\": \"<why>\"}\n"
            "3) If no file edits are needed:\n"
            "   {\"action\": \"noop\", \"justification\": \"<why>\"}\n"
            "Do NOT include any extra conversational text or markdown code block formatting in your output, output raw JSON only."
        )
        
        user_prompt = f"Task Description: {task_description}"
        response_text = self._get_llm_response(system_prompt, user_prompt)
        
        # Clean up any potential markdown wrapper
        cleaned_json = response_text.strip()
        if cleaned_json.startswith("```json"):
            cleaned_json = cleaned_json[7:]
        if cleaned_json.endswith("```"):
            cleaned_json = cleaned_json[:-3]
        cleaned_json = cleaned_json.strip()

        try:
            decision = json.loads(cleaned_json)
        except Exception as e:
            return {
                "status": "failed",
                "error": f"Failed to parse CodeWorker JSON decision: {e}. Raw: {response_text}"
            }

        action = decision.get("action")
        justification = decision.get("justification", "No justification provided")
        path = decision.get("path")
        
        try:
            if action == "write_file":
                self.tools.write_file(
                    path=path,
                    content=decision.get("content", ""),
                    correlation_id=correlation_id,
                    agent_owner=self.agent_owner,
                    justification=justification
                )
                return {"status": "success", "action": "write_file", "path": path, "justification": justification}
                
            elif action == "patch_file":
                self.tools.patch_file(
                    path=path,
                    search=decision.get("search", ""),
                    replace=decision.get("replace", ""),
                    correlation_id=correlation_id,
                    agent_owner=self.agent_owner,
                    justification=justification
                )
                return {"status": "success", "action": "patch_file", "path": path, "justification": justification}
                
            elif action == "noop":
                return {"status": "success", "action": "noop", "justification": justification}
            else:
                return {"status": "failed", "error": f"Unknown action decision: '{action}'"}
        except Exception as ex:
            return {"status": "failed", "error": str(ex)}


class TestWorker(BaseWorker):
    def __init__(self, router: InferenceRouter, tools: ToolRunner, memory: Optional[MemoryManager] = None):
        super().__init__("testworker", router, tools, memory)

    def execute_task(self, test_command: str, correlation_id: str) -> Dict[str, Any]:
        """Runs the compilation or test command and diagnoses output if it fails."""
        res = self.tools.run_shell(
            command=test_command,
            correlation_id=correlation_id,
            agent_owner=self.agent_owner,
            justification=f"Running verification test: {test_command}"
        )
        
        if res["exit_code"] == 0:
            return {
                "status": "success",
                "exit_code": 0,
                "stdout": res["stdout"],
                "stderr": res["stderr"]
            }
            
        # If tests fail, run diagnosing routine via LLM
        system_prompt = (
            "You are TestWorker, a specialized subagent responsible for executing and diagnosing compiler or unit test runs.\n"
            "Analyze the command outputs and explain the root cause of the error in a concise summary. Suggestions should target code fixes."
        )
        user_prompt = (
            f"Test command failed with exit code: {res['exit_code']}\n"
            f"STDOUT:\n{res['stdout']}\n"
            f"STDERR:\n{res['stderr']}"
        )
        
        diagnosis = self._get_llm_response(system_prompt, user_prompt)
        return {
            "status": "failed",
            "exit_code": res["exit_code"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "diagnosis": diagnosis
        }


class DevOpsWorker(BaseWorker):
    def __init__(
        self,
        router: InferenceRouter,
        tools: ToolRunner,
        memory: Optional[MemoryManager] = None,
        ado_client: Optional[AzureDevOpsClient] = None
    ):
        super().__init__("devopsworker", router, tools, memory)
        self.ado_client = ado_client

    def execute_task(self, operation: str, params: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        """Runs git repository operations or Azure DevOps REST queries/mutations."""
        justification = params.get("justification", f"Executing DevOps operation: {operation}")
        
        try:
            if operation == "git_branch":
                branch_name = params["branch_name"]
                # Create branch cross-platform
                cmd = f"git checkout -b {branch_name}"
                res = self.tools.run_shell(cmd, correlation_id, self.agent_owner, justification)
                if res["exit_code"] != 0:
                    raise RuntimeError(f"Git branch checkout failed: {res['stderr']}")
                return {"status": "success", "branch": branch_name}
                
            elif operation == "git_commit":
                message = params["message"]
                files = params.get("files", ".")
                
                # Add and commit
                add_res = self.tools.run_shell(f"git add {files}", correlation_id, self.agent_owner, "Stage changes")
                if add_res["exit_code"] != 0:
                    raise RuntimeError(f"Git add failed: {add_res['stderr']}")
                    
                # Append correlation_id as Git commit metadata trailer
                commit_msg = f"{message}\n\nCorrelation-Id: {correlation_id}"
                commit_res = self.tools.run_shell(f'git commit -m "{commit_msg}"', correlation_id, self.agent_owner, justification)
                if commit_res["exit_code"] != 0:
                    raise RuntimeError(f"Git commit failed: {commit_res['stderr']}")
                    
                # Try to extract commit hash
                log_res = self.tools.run_shell("git rev-parse HEAD", correlation_id, self.agent_owner, "Extract commit hash")
                commit_hash = log_res["stdout"].strip() if log_res["exit_code"] == 0 else None
                
                return {"status": "success", "commit_hash": commit_hash}
                
            elif operation == "git_push":
                branch_name = params["branch_name"]
                cmd = f"git push origin {branch_name}"
                res = self.tools.run_shell(cmd, correlation_id, self.agent_owner, justification)
                # Note: push might fail if remote origin mock is not real, we will return exit code
                return {
                    "status": "success" if res["exit_code"] == 0 else "failed",
                    "exit_code": res["exit_code"],
                    "stdout": res["stdout"],
                    "stderr": res["stderr"]
                }
                
            elif operation == "ado_sync_backlog":
                if not self.ado_client:
                    raise ValueError("Azure DevOps client is not configured")
                wiql = params.get("wiql", "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'To Do'")
                items = self.ado_client.query_backlog(wiql)
                return {"status": "success", "work_items": items}
                
            elif operation == "ado_update_state":
                if not self.ado_client:
                    raise ValueError("Azure DevOps client is not configured")
                wi_id = params["work_item_id"]
                state = params["state"]
                res = self.ado_client.update_work_item_state(wi_id, state)
                return {"status": "success", "work_item": res}
                
            elif operation == "ado_create_pr":
                if not self.ado_client:
                    raise ValueError("Azure DevOps client is not configured")
                source = params["source_branch"]
                target = params["target_branch"]
                title = params["title"]
                desc = params.get("description", "")
                res = self.ado_client.create_pull_request(source, target, title, desc)
                return {"status": "success", "pull_request": res}
                
            else:
                return {"status": "failed", "error": f"Unknown DevOps operation: {operation}"}
                
        except Exception as ex:
            return {"status": "failed", "error": str(ex)}
