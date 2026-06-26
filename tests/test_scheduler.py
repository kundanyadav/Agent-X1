import unittest
import sys
import pathlib
import datetime
from unittest.mock import patch, MagicMock

# Adjust path to import from src
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.jobs.scheduler import JobScheduler

class TestJobScheduler(unittest.TestCase):
    def setUp(self):
        self.scheduler = JobScheduler()

    def test_match_cron_field(self):
        """Verifies cron field matching for wildcards, lists, ranges, steps, and values."""
        # Wildcard
        self.assertTrue(self.scheduler.match_cron_field("*", 45))
        
        # Exact values
        self.assertTrue(self.scheduler.match_cron_field("15", 15))
        self.assertFalse(self.scheduler.match_cron_field("15", 30))
        
        # Step intervals
        self.assertTrue(self.scheduler.match_cron_field("*/15", 0))
        self.assertTrue(self.scheduler.match_cron_field("*/15", 30))
        self.assertFalse(self.scheduler.match_cron_field("*/15", 7))
        
        # Lists
        self.assertTrue(self.scheduler.match_cron_field("1,2,5", 2))
        self.assertFalse(self.scheduler.match_cron_field("1,2,5", 3))
        
        # Ranges
        self.assertTrue(self.scheduler.match_cron_field("10-20", 15))
        self.assertTrue(self.scheduler.match_cron_field("10-20", 10))
        self.assertTrue(self.scheduler.match_cron_field("10-20", 20))
        self.assertFalse(self.scheduler.match_cron_field("10-20", 9))
        self.assertFalse(self.scheduler.match_cron_field("10-20", 21))

    def test_should_run(self):
        """Verifies full cron pattern matching with specific datetimes."""
        # Every 15 minutes: "*/15 * * * *"
        cron = "*/15 * * * *"
        dt_match = datetime.datetime(2026, 6, 25, 12, 30) # 12:30 is divisible by 15
        dt_fail = datetime.datetime(2026, 6, 25, 12, 35) # 12:35 is not
        
        self.assertTrue(self.scheduler.should_run(cron, dt_match))
        self.assertFalse(self.scheduler.should_run(cron, dt_fail))
        
        # Nightly job: "0 2 * * *"
        cron_night = "0 2 * * *"
        dt_match_night = datetime.datetime(2026, 6, 25, 2, 0)
        dt_fail_night = datetime.datetime(2026, 6, 25, 2, 5)
        
        self.assertTrue(self.scheduler.should_run(cron_night, dt_match_night))
        self.assertFalse(self.scheduler.should_run(cron_night, dt_fail_night))

    @patch("src.jobs.scheduler.JobScheduler.load_jobs")
    def test_run_tick_trigger_matching(self, mock_load):
        """Verifies run_tick schedules job on matching datetime ticks."""
        mock_load.return_value = [
            {
                "name": "sync_test",
                "cron": "*/10 * * * *",
                "task": "tasks.sync"
            }
        ]
        
        self.scheduler.trigger_job = MagicMock()
        
        # 1. Match tick (12:20)
        dt_run = datetime.datetime(2026, 6, 25, 12, 20)
        self.scheduler.run_tick(dt_run)
        
        # Wait a small fraction for the background thread to boot and trigger
        import time
        time.sleep(0.05)
        
        self.scheduler.trigger_job.assert_called_once()
        
        # 2. Re-tick in same minute should not trigger duplicate runs
        self.scheduler.trigger_job.reset_mock()
        self.scheduler.run_tick(dt_run)
        time.sleep(0.05)
        self.scheduler.trigger_job.assert_not_called()

    @patch("src.jobs.tasks.consolidate_memories")
    def test_trigger_job_dynamic_dispatch(self, mock_consolidate):
        """Verifies that trigger_job dynamically imports and invokes the task function."""
        job = {
            "name": "memory_consolidation",
            "cron": "0 4 * * *",
            "task": "tasks.consolidate_memories"
        }
        self.scheduler.trigger_job(job)
        mock_consolidate.assert_called_once_with(config_path="config.yaml")

    @patch("src.jobs.scheduler.JobScheduler.load_jobs")
    @patch("src.jobs.tasks.consolidate_memories")
    def test_cron_tick_triggers_actual_callable_and_logs(self, mock_consolidate, mock_load):
        """Verifies that a matching cron tick triggers the actual task function and logs the start."""
        mock_load.return_value = [
            {
                "name": "memory_consolidation",
                "cron": "*/10 * * * *",
                "task": "tasks.consolidate_memories"
            }
        ]
        
        # Match tick (12:20 is divisible by 10)
        dt_run = datetime.datetime(2026, 6, 25, 12, 20)
        
        # Capture stdout to verify logging
        import io
        import sys
        captured_output = io.StringIO()
        sys.stdout = captured_output
        try:
            self.scheduler.run_tick(dt_run)
            
            # Wait a small fraction for the background thread to run
            import time
            time.sleep(0.05)
        finally:
            sys.stdout = sys.__stdout__
            
        # Verify the function was called
        mock_consolidate.assert_called_once_with(config_path="config.yaml")
        
        # Verify the console output contains the log message
        log_content = captured_output.getvalue()
        self.assertIn("Starting scheduled job: memory_consolidation (tasks.consolidate_memories)", log_content)

if __name__ == "__main__":
    unittest.main()
