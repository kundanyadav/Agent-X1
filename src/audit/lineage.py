import os
import json
import time
import hashlib
import pathlib
from typing import Dict, Any, Optional, List

# Optional encryption support
try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

class LineageLogger:
    def __init__(self, log_path: str = "logs/audit_lineage.jsonl", encrypt: bool = False, key: Optional[bytes] = None):
        self.log_path = pathlib.Path(log_path)
        self.encrypt = encrypt
        
        # Ensure log directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.fernet = None
        if self.encrypt:
            if Fernet is None:
                raise ImportError(
                    "The 'cryptography' library is required for audit log encryption. "
                    "Please install it using 'pip install cryptography'."
                )
            if key is None:
                # Look for encryption key in environment, or generate a transient one for testing
                env_key = os.environ.get("AUDIT_ENCRYPTION_KEY")
                if env_key:
                    key = env_key.encode()
                else:
                    # Generate a transient key if none provided
                    key = Fernet.generate_key()
            self.fernet = Fernet(key)

    def compute_file_hash(self, file_path: str) -> Optional[str]:
        """Computes the SHA-256 hash of a file. Returns None if file does not exist."""
        path = pathlib.Path(file_path)
        if not path.is_file():
            return None
        
        sha256 = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception:
            return None

    def log_action(
        self,
        correlation_id: str,
        action: str,
        agent_owner: str,
        justification: str,
        details: Optional[Dict[str, Any]] = None,
        file_path: Optional[str] = None,
        pre_hash: Optional[str] = None,
        post_hash: Optional[str] = None,
        commit_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        """Constructs an audit event, logs it to JSONL, and returns the event dictionary."""
        valid_owners = ["orchestrator", "codeworker", "testworker", "devopsworker"]
        if agent_owner not in valid_owners:
            raise ValueError(f"Invalid agent owner: '{agent_owner}'. Must be one of {valid_owners}")
            
        record = {
            "timestamp": time.time(),
            "correlation_id": correlation_id,
            "action": action,
            "agent_owner": agent_owner,
            "justification": justification,
            "file_path": str(file_path) if file_path else None,
            "pre_hash": pre_hash,
            "post_hash": post_hash,
            "commit_hash": commit_hash,
            "details": details or {}
        }
        
        line_to_write = json.dumps(record)
        
        if self.encrypt and self.fernet:
            encrypted_bytes = self.fernet.encrypt(line_to_write.encode())
            line_to_write = encrypted_bytes.decode()
            
        # Append-only write to file
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line_to_write + "\n")
            
        return record

    def read_logs(self) -> List[Dict[str, Any]]:
        """Reads all audit log entries, decrypting if necessary."""
        if not self.log_path.exists():
            return []
            
        records = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                    
                if self.encrypt and self.fernet:
                    try:
                        decrypted_bytes = self.fernet.decrypt(line.encode())
                        line = decrypted_bytes.decode()
                    except Exception as e:
                        # If decryption fails, skip or raise error depending on strictness
                        raise ValueError(f"Decryption failed for log line: {e}")
                        
                records.append(json.loads(line))
        return records
