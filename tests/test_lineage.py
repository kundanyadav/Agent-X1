import unittest
import pathlib
import sys
import os
import json
import time

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.audit.lineage import LineageLogger

class TestLineageLogger(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = pathlib.Path(__file__).parent.parent / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.log_path = self.tmp_dir / "test_lineage.jsonl"
        self.test_file_path = self.tmp_dir / "test_mutation.txt"
        
        # Cleanup before tests
        if self.log_path.exists():
            self.log_path.unlink()
        if self.test_file_path.exists():
            self.test_file_path.unlink()

    def tearDown(self):
        # Cleanup files
        if self.log_path.exists():
            self.log_path.unlink()
        if self.test_file_path.exists():
            self.test_file_path.unlink()

    def test_compute_file_hash(self):
        """Verifies calculation of SHA-256 hash for files."""
        logger = LineageLogger(log_path=str(self.log_path))
        
        # Non-existent file
        self.assertIsNone(logger.compute_file_hash(str(self.test_file_path)))
        
        # Create file
        content = b"hello agent-x1 audit log content"
        with open(self.test_file_path, "wb") as f:
            f.write(content)
            
        import hashlib
        expected_hash = hashlib.sha256(content).hexdigest()
        self.assertEqual(logger.compute_file_hash(str(self.test_file_path)), expected_hash)

    def test_log_action_unencrypted(self):
        """Verifies unencrypted lineage logging and agent owner validation."""
        logger = LineageLogger(log_path=str(self.log_path), encrypt=False)
        
        # Validate owner gating
        with self.assertRaises(ValueError):
            logger.log_action(
                correlation_id="uuid-123",
                action="file_write",
                agent_owner="rogue_agent",
                justification="Testing"
            )
            
        # Log valid action
        record = logger.log_action(
            correlation_id="uuid-456",
            action="file_write",
            agent_owner="codeworker",
            justification="Adding helper function to module",
            file_path=str(self.test_file_path),
            pre_hash="pre123",
            post_hash="post123",
            commit_hash="gitabc"
        )
        
        self.assertEqual(record["correlation_id"], "uuid-456")
        self.assertEqual(record["agent_owner"], "codeworker")
        self.assertEqual(record["commit_hash"], "gitabc")
        
        # Check that file exists and matches record
        logs = logger.read_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["correlation_id"], "uuid-456")
        self.assertEqual(logs[0]["pre_hash"], "pre123")
        self.assertEqual(logs[0]["post_hash"], "post123")
        
        # Log another action and verify append behavior
        logger.log_action(
            correlation_id="uuid-456",
            action="git_commit",
            agent_owner="devopsworker",
            justification="Commit changes to remote repository"
        )
        
        logs_after = logger.read_logs()
        self.assertEqual(len(logs_after), 2)
        self.assertEqual(logs_after[1]["agent_owner"], "devopsworker")
        self.assertEqual(logs_after[1]["action"], "git_commit")

    def test_encryption_lifecycle(self):
        """Verifies encryption and decryption of audit logs if cryptography is installed, otherwise verify error."""
        try:
            from cryptography.fernet import Fernet
            has_crypto = True
        except ImportError:
            has_crypto = False
            
        if not has_crypto:
            # Verify import error is raised when encrypt=True
            with self.assertRaises(ImportError):
                LineageLogger(log_path=str(self.log_path), encrypt=True)
        else:
            # Generate static key for verification
            key = Fernet.generate_key()
            logger = LineageLogger(log_path=str(self.log_path), encrypt=True, key=key)
            
            # Log action
            logger.log_action(
                correlation_id="secure-uuid",
                action="db_write",
                agent_owner="orchestrator",
                justification="Writing system configuration database records"
            )
            
            # Verify file content is encrypted (not readable JSON)
            with open(self.log_path, "r", encoding="utf-8") as f:
                raw_line = f.readline().strip()
            
            # Should not load as JSON
            with self.assertRaises(json.JSONDecodeError):
                json.loads(raw_line)
                
            # Read through reader (should decrypt successfully)
            logs = logger.read_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["correlation_id"], "secure-uuid")
            self.assertEqual(logs[0]["action"], "db_write")

if __name__ == "__main__":
    unittest.main()
