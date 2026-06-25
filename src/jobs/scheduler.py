import os
import time
import datetime
import threading
from typing import Dict, Any, List
import yaml

class JobScheduler:
    def __init__(self, config_path: str = "config.yaml", jobs_path: str = "jobs.yaml"):
        self.config_path = config_path
        self.jobs_path = jobs_path
        self.last_run: Dict[str, int] = {}  # Job name -> last run minute timestamp (in epoch minutes)
        self.running = False

    def load_jobs(self) -> List[Dict[str, Any]]:
        """Loads jobs list from jobs.yaml."""
        if not os.path.exists(self.jobs_path):
            return []
        with open(self.jobs_path, "r") as f:
            data = yaml.safe_load(f)
        return data.get("jobs", [])

    def match_cron_field(self, field: str, current_val: int) -> bool:
        """Parses a single cron field and returns True if current_val matches."""
        field = field.strip()
        if field == "*":
            return True
            
        # Handle step values like */15
        if field.startswith("*/"):
            try:
                step = int(field[2:])
                return current_val % step == 0
            except ValueError:
                return False
                
        # Handle lists like 1,2,5
        if "," in field:
            try:
                parts = [int(p) for p in field.split(",") if p.strip()]
                return current_val in parts
            except ValueError:
                return False
                
        # Handle ranges like 1-5
        if "-" in field:
            try:
                start, end = map(int, field.split("-"))
                return start <= current_val <= end
            except ValueError:
                return False
                
        # Handle direct numeric value
        try:
            return int(field) == current_val
        except ValueError:
            return False

    def should_run(self, cron_expr: str, dt: datetime.datetime) -> bool:
        """Checks if datetime matches cron expression (minute, hour, day, month, weekday)."""
        fields = cron_expr.split()
        if len(fields) < 5:
            return False
            
        minute_match = self.match_cron_field(fields[0], dt.minute)
        hour_match = self.match_cron_field(fields[1], dt.hour)
        day_match = self.match_cron_field(fields[2], dt.day)
        month_match = self.match_cron_field(fields[3], dt.month)
        
        # weekday mapping: cron is typically 0-6 (Sunday-Saturday) or 0-7, datetime weekday() is 0-6 (Monday-Sunday)
        # Convert datetime weekday (0=Mon, 6=Sun) to cron style (0=Sun, 1=Mon, 6=Sat)
        cron_weekday = (dt.weekday() + 1) % 7
        weekday_match = self.match_cron_field(fields[4], cron_weekday)
        
        return all([minute_match, hour_match, day_match, month_match, weekday_match])

    def trigger_job(self, job: Dict[str, Any]):
        """Executes the specified task in a background thread."""
        name = job["name"]
        task = job["task"]
        print(f"[{datetime.datetime.now().isoformat()}] Starting scheduled job: {name} ({task})")
        
        # In a real environment, this imports task and runs it.
        # For mock verification, we print and write a trace action if memory manager is loaded.
        pass

    def run_tick(self, dt: datetime.datetime):
        """Runs a single scheduler tick checking all jobs against the current minute."""
        current_minute_epoch = int(time.time() / 60)
        jobs = self.load_jobs()
        
        for job in jobs:
            name = job["name"]
            cron = job["cron"]
            
            # Avoid running the same job multiple times in the same minute
            if self.last_run.get(name) == current_minute_epoch:
                continue
                
            if self.should_run(cron, dt):
                self.last_run[name] = current_minute_epoch
                # Run job asynchronously
                t = threading.Thread(target=self.trigger_job, args=(job,), daemon=True)
                t.start()

    def start(self):
        """Starts the scheduler main loop."""
        self.running = True
        print("Agent-X1 scheduler started.")
        while self.running:
            now = datetime.datetime.now()
            self.run_tick(now)
            # Sleep until next check (e.g. 5 seconds)
            time.sleep(5)

    def stop(self):
        self.running = False
        print("Agent-X1 scheduler stopped.")

if __name__ == "__main__":
    scheduler = JobScheduler()
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.stop()

