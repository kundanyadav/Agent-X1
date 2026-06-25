import os
import sys
import subprocess
import pathlib
from typing import Dict, Any, List, Optional
from src.audit.lineage import LineageLogger
from src.memory.memory import MemoryManager

class ToolRunner:
    def __init__(
        self,
        lineage_logger: Optional[LineageLogger] = None,
        memory_manager: Optional[MemoryManager] = None
    ):
        self.lineage_logger = lineage_logger
        self.memory_manager = memory_manager

    def run_shell(
        self,
        command: str,
        correlation_id: str,
        agent_owner: str,
        justification: str,
        cwd: Optional[str] = None
    ) -> Dict[str, Any]:
        """Runs a shell command in an OS-agnostic manner and logs it to audit and memory."""
        if sys.platform.startswith("win"):
            # Wrap command in PowerShell execution bypass
            shell_cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        else:
            # Linux/macOS standard bash command
            shell_cmd = ["/bin/bash", "-c", command]

        # Execute command
        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            cwd=cwd
        )

        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

        # Log action to Memory
        if self.memory_manager:
            self.memory_manager.write_action(
                session_id=correlation_id,
                agent_owner=agent_owner,
                tool_called="run_shell",
                arguments={"command": command, "cwd": cwd},
                stdout=stdout,
                stderr=stderr,
                status="success" if exit_code == 0 else "failed"
            )

        # Log action to Lineage Audit
        if self.lineage_logger:
            self.lineage_logger.log_action(
                correlation_id=correlation_id,
                action="run_shell",
                agent_owner=agent_owner,
                justification=justification,
                details={
                    "command": command,
                    "cwd": cwd,
                    "exit_code": exit_code,
                    "stderr_preview": stderr[:500] if stderr else ""
                }
            )

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code
        }

    def read_file(self, path: str) -> str:
        """Reads and returns raw text content from a file."""
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return p.read_text(encoding="utf-8")

    def write_file(
        self,
        path: str,
        content: str,
        correlation_id: str,
        agent_owner: str,
        justification: str
    ) -> None:
        """Writes raw text content to a file, logging pre/post hashes for audit trail."""
        p = pathlib.Path(path)
        
        # Ensure parent directories exist
        p.parent.mkdir(parents=True, exist_ok=True)
        
        pre_hash = None
        if self.lineage_logger:
            pre_hash = self.lineage_logger.compute_file_hash(str(p))

        # Write file
        p.write_text(content, encoding="utf-8")

        post_hash = None
        if self.lineage_logger:
            post_hash = self.lineage_logger.compute_file_hash(str(p))
            
            self.lineage_logger.log_action(
                correlation_id=correlation_id,
                action="write_file",
                agent_owner=agent_owner,
                justification=justification,
                file_path=str(p),
                pre_hash=pre_hash,
                post_hash=post_hash
            )

        if self.memory_manager:
            self.memory_manager.write_action(
                session_id=correlation_id,
                agent_owner=agent_owner,
                tool_called="write_file",
                arguments={"file_path": str(p), "pre_hash": pre_hash, "post_hash": post_hash},
                stdout="File written successfully",
                stderr="",
                status="success"
            )

    def patch_file(
        self,
        path: str,
        search: str,
        replace: str,
        correlation_id: str,
        agent_owner: str,
        justification: str
    ) -> None:
        """Applies a search-and-replace patch to a file, logging pre/post hashes for audit trail."""
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Cannot patch non-existent file: {path}")

        file_content = p.read_text(encoding="utf-8")
        if search not in file_content:
            raise ValueError(f"Search block not found in file: {path}")

        pre_hash = None
        if self.lineage_logger:
            pre_hash = self.lineage_logger.compute_file_hash(str(p))

        # Perform replacement
        patched_content = file_content.replace(search, replace)
        p.write_text(patched_content, encoding="utf-8")

        post_hash = None
        if self.lineage_logger:
            post_hash = self.lineage_logger.compute_file_hash(str(p))
            
            self.lineage_logger.log_action(
                correlation_id=correlation_id,
                action="patch_file",
                agent_owner=agent_owner,
                justification=justification,
                file_path=str(p),
                pre_hash=pre_hash,
                post_hash=post_hash,
                details={"search_len": len(search), "replace_len": len(replace)}
            )

        if self.memory_manager:
            self.memory_manager.write_action(
                session_id=correlation_id,
                agent_owner=agent_owner,
                tool_called="patch_file",
                arguments={"file_path": str(p), "pre_hash": pre_hash, "post_hash": post_hash},
                stdout="File patched successfully",
                stderr="",
                status="success"
            )

    def delete_file(
        self,
        path: str,
        correlation_id: str,
        agent_owner: str,
        justification: str
    ) -> None:
        """Deletes a file, recording the action in the audit logs."""
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Cannot delete non-existent file: {path}")

        pre_hash = None
        if self.lineage_logger:
            pre_hash = self.lineage_logger.compute_file_hash(str(p))

        # Delete file
        p.unlink()

        if self.lineage_logger:
            self.lineage_logger.log_action(
                correlation_id=correlation_id,
                action="delete_file",
                agent_owner=agent_owner,
                justification=justification,
                file_path=str(p),
                pre_hash=pre_hash,
                post_hash=None
            )

        if self.memory_manager:
            self.memory_manager.write_action(
                session_id=correlation_id,
                agent_owner=agent_owner,
                tool_called="delete_file",
                arguments={"file_path": str(p), "pre_hash": pre_hash},
                stdout="File deleted successfully",
                stderr="",
                status="success"
            )

    def list_dir(self, path: str) -> List[str]:
        """Lists names of direct children of a directory."""
        p = pathlib.Path(path)
        if not p.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {path}")
        return [child.name for child in p.iterdir()]
