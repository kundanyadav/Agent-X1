import sys
import argparse
import pathlib
import os
from typing import Optional
from src.inference.router import InferenceRouter
from src.core.tools import ToolRunner
from src.memory.memory import MemoryManager
from src.core.orchestrator import OrchestrationEngine

try:
    import readline
    import atexit
    
    class SlashCompleter:
        def __init__(self, commands):
            self.commands = commands

        def complete(self, text, state):
            if not text:
                matches = self.commands
            else:
                matches = [x for x in self.commands if x.startswith(text)]
            results = matches + [None]
            return results[state]

    def setup_readline():
        commands = [
            "/exit", "/help", "/options", "/pin", "/resume", 
            "/delete", "/compact", "/goal", "/schedule", "/btw", 
            "/clear", "/status", "/export"
        ]
        completer = SlashCompleter(commands)
        readline.set_completer(completer.complete)
        
        # Support both GNU Readline and macOS libedit (which uses bind instead of parse_and_bind)
        if "libedit" in readline.__doc__:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
            
        os.makedirs("tmp", exist_ok=True)
        history_file = "tmp/cli_history"
        try:
            if os.path.exists(history_file):
                readline.read_history_file(history_file)
        except Exception:
            pass
        atexit.register(readline.write_history_file, history_file)
except ImportError:
    def setup_readline():
        pass

def print_startup_banner(config_path: str = "config.yaml"):
    """Prints a premium ASCII art startup banner with animated boot sequence."""
    import time

    # ── ANSI colours ──────────────────────────────────────────────
    NEON     = "\033[1;38;5;39m"   # bold electric blue  (user / brand)
    MUSTARD  = "\033[38;5;178m"    # mustard yellow      (agent / info)
    WHITE    = "\033[97m"          # bright white        (body text)
    DIM      = "\033[2;37m"        # dim grey            (decorative lines)
    GREEN    = "\033[92m"          # success green
    RED      = "\033[91m"          # error red
    CYAN     = "\033[96m"          # cyan                (labels)
    RESET    = "\033[0m"

    # ── ASCII logo ─────────────────────────────────────────────────
    logo_lines = [
        r"   ___                    __     _  __   ___  ",
        r"  / _ |___ ____ ___  __ / /_   | |/_/  <  /  ",
        r" / __ / _ `/ -_) _ \/ // __/  _>  <   / /   ",
        r"/_/ |_\_, /\__/_//_/\_, /\__/ /_/|_|  /_/    ",
        r"      /___/         /___/                      ",
    ]

    try:
        _version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(_version_file, "r", encoding="utf-8") as f:
            _version = f.read().strip()
    except Exception:
        _version = "dev"
    tagline   = f"Autonomous Developer Harness  ·  v{_version}"
    separator = "─" * 52

    os.system("cls" if os.name == "nt" else "clear")

    # Print logo in neon blue
    print()
    for line in logo_lines:
        print(f"{NEON}{line}{RESET}")
    print()
    print(f"{MUSTARD}  {tagline}{RESET}")
    print(f"{DIM}  {separator}{RESET}")
    print()

    # ── Animated boot checklist ────────────────────────────────────
    boot_steps = [
        ("Loading config",        True),
        ("Initialising readline", True),
        ("Connecting to memory",  True),
        ("Checking LLM router",   True),
        ("Ready",                 True),
    ]

    for label, ok in boot_steps:
        status_icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"  {MUSTARD}[{RESET}{status_icon}{MUSTARD}]{RESET}  {WHITE}{label}...{RESET}")
        time.sleep(0.07)   # snappy but visible

    print()

    # ── System info panel ──────────────────────────────────────────
    try:
        router = InferenceRouter(config_path=config_path)
        cfg = router.config
        active_provider = "unknown"
        active_model    = "unknown"
        for pname in ["openrouter", "openai", "gemini", "anthropic", "ollama"]:
            pcfg = cfg.get(pname, {})
            if pcfg.get("enabled"):
                active_provider = pname
                preset = pcfg.get("preset")
                model  = pcfg.get("model", "unknown")
                active_model = f"@preset/{preset}" if preset else model
                break

        db_path   = cfg.get("storage", {}).get("db_path", "tmp/memory.db")
        db_status = f"{GREEN}connected{RESET}" if os.path.exists(db_path) else f"{MUSTARD}initialising{RESET}"
    except Exception:
        active_provider = "not configured"
        active_model    = "not configured"
        db_status       = f"{MUSTARD}initialising{RESET}"

    print(f"  {DIM}{separator}{RESET}")
    print(f"  {CYAN}Provider  {RESET}│ {WHITE}{active_provider}{RESET}")
    print(f"  {CYAN}Model     {RESET}│ {WHITE}{active_model}{RESET}")
    print(f"  {CYAN}Memory DB {RESET}│ {db_status}")
    print(f"  {DIM}{separator}{RESET}")
    print()
    print(f"  {DIM}Type {RESET}{NEON}/help{RESET}{DIM} for all commands  ·  {RESET}{NEON}approved for build{RESET}{DIM} to execute  ·  {RESET}{NEON}/exit{RESET}{DIM} to quit{RESET}")
    print()


class CliGateway:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path


    def save_scheduled_job(self, goal: str, cron: str, correlation_id: str):
        """Appends the scheduled job to jobs.yaml and writes the goal config to tmp/scheduled_goal_<id>.json."""
        import yaml
        # 1. Save config to tmp/scheduled_goal_<id>.json
        job_id = correlation_id[:8]
        pathlib.Path("tmp").mkdir(parents=True, exist_ok=True)
        job_file = pathlib.Path("tmp") / f"scheduled_goal_{job_id}.json"
        
        try:
            router = InferenceRouter(config_path=self.config_path)
            memory = MemoryManager(db_path=router.config.get("storage", {}).get("db_path", "tmp/memory.db"), router=router)
            engine = OrchestrationEngine(router=router, tools=None, memory=memory)
            tasks = engine.decompose_plan_into_tasks(f"Goal: {goal}")
        except Exception as e:
            print(f"[!] Warning: Failed to pre-decompose goal during scheduling: {e}")
            tasks = []
            
        job_data = {
            "correlation_id": correlation_id,
            "goal": goal,
            "cron": cron,
            "tasks": tasks
        }
        with open(job_file, "w") as f:
            import json
            json.dump(job_data, f, indent=2)
            
        # 2. Append new job to jobs.yaml
        jobs_path = pathlib.Path("jobs.yaml")
        jobs_list = []
        if jobs_path.exists():
            try:
                with open(jobs_path, "r") as f:
                    yaml_data = yaml.safe_load(f) or {}
                    jobs_list = yaml_data.get("jobs", [])
            except Exception as e:
                print(f"[!] Warning: Failed to read jobs.yaml: {e}")
                
        job_name = f"scheduled_goal_{job_id}"
        new_job = {
            "name": job_name,
            "cron": cron,
            "task": "tasks.run_scheduled_goal",
            "description": f"Scheduled execution of goal: {goal}"
        }
        jobs_list.append(new_job)
        
        try:
            with open(jobs_path, "w") as f:
                yaml.safe_dump({"jobs": jobs_list}, f, default_flow_style=False)
            print(f"[+] Successfully scheduled! Job '{job_name}' written to jobs.yaml.")
        except Exception as e:
            print(f"[!] Error: Failed to write to jobs.yaml: {e}")

    def interactive_planning_loop(self, goal: str, engine: OrchestrationEngine, resume_session: Optional[str] = None) -> str:
        """Runs a back-and-forth interactive CLI chat loop to refine the implementation plan."""
        # ANSI Escape Sequences for styling
        NEON_BLUE = "\033[1;38;5;39m"  # Bold neon/electric blue
        MUSTARD = "\033[38;5;178m"    # Mustard yellow
        WHITE = "\033[97m"            # Bright white
        RESET = "\033[0m"             # Reset colors
        GREEN = "\033[92m"            # Success Green
        RED = "\033[91m"              # Warning/Alert Red
        CYAN = "\033[96m"             # Options/Prompt Cyan

        print(f"\n{MUSTARD}=== Entering Interactive Planning Phase ==={RESET}")
        
        history = []
        scheduled_cron = None
        
        if resume_session:
            try:
                full_context = engine.memory.get_pinned_session(resume_session)
                if full_context:
                    goal = full_context.get("goal")
                    history = full_context.get("history", [])
                    scheduled_cron = full_context.get("scheduled_cron")
                    
                    proposal = "No plan proposal found. Please type '/compact' or write feedback to generate one."
                    for msg in reversed(history):
                        if msg.get("role") == "assistant":
                            proposal = msg.get("content")
                            break
                    print(f"{GREEN}[+] Resumed session '{resume_session}' successfully.{RESET}")
                    print(f"{MUSTARD}[*] Restored Goal: '{goal}'{RESET}")
                    if scheduled_cron:
                        print(f"{MUSTARD}[*] Restored Schedule: '{scheduled_cron}'{RESET}")
                else:
                    print(f"{RED}[!] Error: Failed to load pinned session '{resume_session}'. Generating new proposal...{RESET}")
                    print(f"\n{MUSTARD}[*] Generating initial proposal...{RESET}")
                    proposal = engine.generate_planning_proposal(goal, history)
                    history.append({"role": "assistant", "content": proposal})
            except Exception as e:
                print(f"{RED}[!] Error loading pinned session '{resume_session}': {e}. Generating new proposal...{RESET}")
                print(f"\n{MUSTARD}[*] Generating initial proposal...{RESET}")
                proposal = engine.generate_planning_proposal(goal, history)
                history.append({"role": "assistant", "content": proposal})
        else:
            print(f"{WHITE}Goal: {goal}{RESET}")
            # Get first proposal
            print(f"\n{MUSTARD}[*] Generating initial proposal...{RESET}")
            proposal = engine.generate_planning_proposal(goal, history)
            history.append({"role": "assistant", "content": proposal})
        
        reprint_proposal = True
        first_turn = True
        
        while True:
            if reprint_proposal:
                print(f"\n{MUSTARD}" + "=" * 60)
                print(f"{WHITE}{proposal}")
                print(f"{MUSTARD}" + "=" * 60 + f"{RESET}\n")
                if scheduled_cron:
                    print(f"{GREEN}[Scheduled]: Job will be scheduled to run on: '{scheduled_cron}'{RESET}\n")
            
            if first_turn:
                print(f"{CYAN}Options:{RESET}")
                print("  - Type your feedback to refine the plan.")
                print("  - Type '/btw <question>' to ask a question without changing the plan.")
                print("  - Type '/compact' to distill conversation history and save tokens.")
                print("  - Type '/goal <new_goal>' to pivot and reset the session.")
                print("  - Type '/schedule \"<cron>\"' to schedule execution.")
                print("  - Type '/pin [name]' to save current session context.")
                print("  - Type '/resume' to choose and restore a pinned session.")
                print("  - Type '/delete [name]' to delete a pinned session.")
                print("  - Type '/clear' to clear the screen and reprint the plan.")
                print("  - Type '/status' to show active provider, model, and session info.")
                print("  - Type '/export [filename]' to save the plan to a markdown file.")
                print("  - Type 'approved for build' to approve the plan and start execution.")
                print("  - Type '/exit' to cancel.")
                print(f"  (Type {CYAN}/help{RESET} at any time to see these options again)\n")
                first_turn = False
            
            # Default behavior: reprint proposal on next turn unless suppressed
            reprint_proposal = True
            
            try:
                # User typing in neon blue
                user_input = input(f"{NEON_BLUE}Your feedback/decision (type /help for options): ")
            except KeyboardInterrupt:
                print(f"\n{RED}[!] Execution aborted by keyboard interrupt.{RESET}")
                return ""
            finally:
                print(RESET, end="", flush=True)
                
            user_input = user_input.strip()
            if not user_input:
                continue
                
            # Slash Command Parsing
            if user_input.startswith("/"):
                parts = user_input.split(" ", 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                
                if cmd == "/exit":
                    print(f"\n{RED}[!] Execution aborted.{RESET}")
                    return ""
                elif cmd in ["/help", "/options"]:
                    print(f"\n{CYAN}Options:{RESET}")
                    print("  - Type your feedback to refine the plan.")
                    print("  - Type '/btw <question>' to ask a question without changing the plan.")
                    print("  - Type '/compact' to distill conversation history and save tokens.")
                    print("  - Type '/goal <new_goal>' to pivot and reset the session.")
                    print("  - Type '/schedule \"<cron>\"' to schedule execution.")
                    print("  - Type '/pin [name]' to save current session context.")
                    print("  - Type '/resume' to choose and restore a pinned session.")
                    print("  - Type '/delete [name]' to delete a pinned session.")
                    print("  - Type '/clear' to clear the screen and reprint the plan.")
                    print("  - Type '/status' to show active provider, model, and session info.")
                    print("  - Type '/export [filename]' to save the plan to a markdown file.")
                    print("  - Type 'approved for build' to approve the plan and start execution.")
                    print("  - Type '/exit' to cancel.")
                    print()
                    reprint_proposal = False
                    continue
                elif cmd == "/pin":
                    pin_name = arg
                    if not pin_name:
                        import time
                        pin_name = f"pin_{int(time.time())}"
                    try:
                        engine.memory.pin_session(pin_name, goal, history, scheduled_cron)
                        print(f"{GREEN}[+] Pinned session saved as '{pin_name}'.{RESET}\n")
                    except Exception as e:
                        print(f"{RED}[!] Error pinning session: {e}{RESET}\n")
                    reprint_proposal = False
                    continue
                elif cmd == "/resume":
                    try:
                        pinned = engine.memory.get_pinned_sessions()
                        if not pinned:
                            print(f"{RED}[!] No pinned sessions found.{RESET}\n")
                            reprint_proposal = False
                            continue
                        
                        print(f"\n{MUSTARD}Available pinned sessions:{RESET}")
                        for idx, session in enumerate(pinned, 1):
                            name = session.get("name")
                            session_goal = session.get("goal")
                            ts = session.get("timestamp")
                            import datetime
                            dt_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                            print(f"  {idx}. {name} - Goal: {session_goal} (Pinned: {dt_str})")
                        
                        choice_input = input(f"\nChoose a session number or name to resume (or type '/exit' to cancel): ").strip()
                        if not choice_input or choice_input.lower() == "/exit":
                            print(f"{MUSTARD}[*] Resume cancelled.{RESET}\n")
                            reprint_proposal = False
                            continue
                            
                        target_session = None
                        try:
                            choice_idx = int(choice_input) - 1
                            if 0 <= choice_idx < len(pinned):
                                target_session = pinned[choice_idx]
                        except ValueError:
                            pass
                            
                        if not target_session:
                            for session in pinned:
                                if session.get("name") == choice_input:
                                    target_session = session
                                    break
                                    
                        if not target_session:
                            print(f"{RED}[!] Error: Invalid selection or name.{RESET}\n")
                            reprint_proposal = False
                            continue
                            
                        name = target_session.get("name")
                        full_context = engine.memory.get_pinned_session(name)
                        if not full_context:
                            print(f"{RED}[!] Error: Failed to load pinned session '{name}'.{RESET}\n")
                            reprint_proposal = False
                            continue
                            
                        goal = full_context.get("goal")
                        history = full_context.get("history", [])
                        scheduled_cron = full_context.get("scheduled_cron")
                        
                        proposal = "No plan proposal found. Please type '/compact' or write feedback to generate one."
                        for msg in reversed(history):
                            if msg.get("role") == "assistant":
                                proposal = msg.get("content")
                                break
                                
                        print(f"\n{GREEN}[+] Resumed session '{name}' successfully.{RESET}")
                        print(f"{MUSTARD}[*] Restored Goal: '{goal}'{RESET}")
                        if scheduled_cron:
                            print(f"{MUSTARD}[*] Restored Schedule: '{scheduled_cron}'{RESET}")
                            
                    except Exception as e:
                        print(f"{RED}[!] Error resuming session: {e}{RESET}\n")
                    continue
                elif cmd == "/delete":
                    try:
                        if arg:
                            deleted = engine.memory.delete_pinned_session(arg)
                            if deleted:
                                print(f"{GREEN}[+] Deleted pinned session '{arg}' successfully.{RESET}\n")
                            else:
                                print(f"{RED}[!] Error: Pinned session '{arg}' not found.{RESET}\n")
                        else:
                            pinned = engine.memory.get_pinned_sessions()
                            if not pinned:
                                print(f"{RED}[!] No pinned sessions found.{RESET}\n")
                                reprint_proposal = False
                                continue
                            
                            print(f"\n{MUSTARD}Available pinned sessions:{RESET}")
                            for idx, session in enumerate(pinned, 1):
                                name = session.get("name")
                                session_goal = session.get("goal")
                                ts = session.get("timestamp")
                                import datetime
                                dt_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                                print(f"  {idx}. {name} - Goal: {session_goal} (Pinned: {dt_str})")
                            
                            choice_input = input(f"\nChoose a session number or name to delete (or type '/exit' to cancel): ").strip()
                            if not choice_input or choice_input.lower() == "/exit":
                                print(f"{MUSTARD}[*] Deletion cancelled.{RESET}\n")
                                reprint_proposal = False
                                continue
                                
                            target_name = None
                            try:
                                choice_idx = int(choice_input) - 1
                                if 0 <= choice_idx < len(pinned):
                                    target_name = pinned[choice_idx]["name"]
                            except ValueError:
                                pass
                                
                            if not target_name:
                                for session in pinned:
                                    if session.get("name") == choice_input:
                                        target_name = session.get("name")
                                        break
                                        
                            if not target_name:
                                print(f"{RED}[!] Error: Invalid selection or name.{RESET}\n")
                                reprint_proposal = False
                                continue
                                
                            deleted = engine.memory.delete_pinned_session(target_name)
                            if deleted:
                                print(f"{GREEN}[+] Deleted pinned session '{target_name}' successfully.{RESET}\n")
                            else:
                                print(f"{RED}[!] Error deleting session.{RESET}\n")
                    except Exception as e:
                        print(f"{RED}[!] Error deleting session: {e}{RESET}\n")
                    reprint_proposal = False
                    continue
                elif cmd == "/compact":
                    print(f"\n{MUSTARD}[*] Distilling and compacting chat history to save tokens...{RESET}")
                    history = engine.compact_planning_history(goal, history)
                    print(f"{MUSTARD}[*] Regenerating proposal based on compacted history...{RESET}")
                    proposal = engine.generate_planning_proposal(goal, history)
                    history.append({"role": "assistant", "content": proposal})
                    print(f"{GREEN}[+] Chat history successfully compacted! New history contains {len(history)} items.{RESET}")
                    continue
                elif cmd == "/goal":
                    if not arg:
                        print(f"{RED}[!] Error: Please specify a new goal after /goal.{RESET}\n")
                        reprint_proposal = False
                        continue
                    print(f"\n{MUSTARD}[*] Pivoting goal to: {arg}{RESET}")
                    goal = arg
                    history = []
                    print(f"{MUSTARD}[*] Generating new proposal...{RESET}")
                    proposal = engine.generate_planning_proposal(goal, history)
                    history.append({"role": "assistant", "content": proposal})
                    continue
                elif cmd == "/schedule":
                    if not arg:
                        print(f"{RED}[!] Error: Please specify a cron expression after /schedule.{RESET}\n")
                        reprint_proposal = False
                        continue
                    cron_expr = arg.strip('"').strip("'")
                    scheduled_cron = cron_expr
                    print(f"{GREEN}[+] Goal set to run on schedule: '{cron_expr}' (will be written to jobs.yaml on approval).{RESET}\n")
                    reprint_proposal = False
                    continue
                elif cmd == "/btw":
                    if not arg:
                        print(f"{RED}[!] Error: Please specify a question after /btw.{RESET}\n")
                        reprint_proposal = False
                        continue
                    print(f"\n{MUSTARD}[*] Running secondary Q/A thread for: '{arg}'...{RESET}")
                    answer = engine.answer_planning_qa(arg)
                    print("\n" + "-" * 60)
                    print(f"{WHITE}[BTW Q&A Answer]\n{answer}{RESET}")
                    print("-" * 60 + "\n")
                    reprint_proposal = False
                    continue
                elif cmd in ["/clear", "/cls"]:
                    import os
                    os.system("cls" if os.name == "nt" else "clear")
                    print(f"\n{MUSTARD}=== Agent-X1 — Active Plan ==={RESET}")
                    print(f"\n{MUSTARD}{'=' * 60}{RESET}")
                    print(f"{WHITE}{proposal}{RESET}")
                    print(f"{MUSTARD}{'=' * 60}{RESET}\n")
                    reprint_proposal = False
                    continue
                elif cmd in ["/status", "/info"]:
                    import datetime
                    cfg = engine.router.config if hasattr(engine, "router") else {}
                    active_provider = "unknown"
                    active_model = "unknown"
                    for provider_name in ["openrouter", "openai", "gemini", "anthropic", "ollama"]:
                        provider_cfg = cfg.get(provider_name, {})
                        if provider_cfg.get("enabled"):
                            active_provider = provider_name
                            preset = provider_cfg.get("preset")
                            model = provider_cfg.get("model", "unknown")
                            active_model = f"@preset/{preset}" if preset else model
                            break
                    turn = len([m for m in history if m.get("role") == "user"])
                    sched_display = scheduled_cron if scheduled_cron else "none"
                    pin_count = len(engine.memory.get_pinned_sessions()) if hasattr(engine, "memory") else "?"
                    ts_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"\n{MUSTARD}{'─' * 46}{RESET}")
                    print(f"{MUSTARD}  Agent-X1 Session Status  [{ts_now}]{RESET}")
                    print(f"{MUSTARD}{'─' * 46}{RESET}")
                    print(f"  {CYAN}Goal          :{RESET} {goal}")
                    print(f"  {CYAN}Provider      :{RESET} {active_provider}")
                    print(f"  {CYAN}Model         :{RESET} {active_model}")
                    print(f"  {CYAN}Turn          :{RESET} {turn}")
                    print(f"  {CYAN}Schedule      :{RESET} {sched_display}")
                    print(f"  {CYAN}Pinned saved  :{RESET} {pin_count}")
                    print(f"{MUSTARD}{'─' * 46}{RESET}\n")
                    reprint_proposal = False
                    continue
                elif cmd == "/export":
                    import os, datetime
                    filename = arg.strip() if arg else "proposal.md"
                    if not filename.endswith(".md"):
                        filename += ".md"
                    try:
                        os.makedirs("tmp", exist_ok=True)
                        export_path = os.path.join("tmp", filename)
                        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with open(export_path, "w", encoding="utf-8") as f:
                            f.write(f"# Agent-X1 Plan Export\n\n")
                            f.write(f"**Goal:** {goal}  \n")
                            f.write(f"**Exported:** {ts}  \n\n")
                            f.write("---\n\n")
                            f.write(proposal)
                            f.write("\n")
                        print(f"{GREEN}[+] Plan exported to '{export_path}' successfully.{RESET}\n")
                    except Exception as e:
                        print(f"{RED}[!] Error exporting plan: {e}{RESET}\n")
                    reprint_proposal = False
                    continue
                else:
                    print(f"{RED}[!] Unknown slash command: {cmd}  (type /help to see all commands){RESET}\n")
                    reprint_proposal = False
                    continue
                    
            if user_input.lower() == "approved for build":
                print(f"\n{GREEN}[+] Plan approved!{RESET}")
                if scheduled_cron:
                    correlation_id = engine.generate_correlation_id()
                    self.save_scheduled_job(goal, scheduled_cron, correlation_id)
                    return "scheduled"
                return proposal
            elif user_input.lower() in ["abort", "no", "n"]:
                print(f"\n{RED}[!] Execution aborted.{RESET}")
                return ""
            
            # Check if user input contains important preferences to save to memory
            engine.learn_user_fact_if_needed(user_input)
            
            # User provided feedback
            print(f"\n{MUSTARD}[*] Refining plan based on feedback...{RESET}")
            history.append({"role": "user", "content": user_input})
            proposal = engine.generate_planning_proposal(goal, history)
            history.append({"role": "assistant", "content": proposal})

    def run_goal(self, goal: str, auto_approve: bool = True, dry_run: bool = False, interactive: bool = False, resume_session: Optional[str] = None) -> str:
        """Initializes dependencies and runs the orchestrator execution loop for a goal."""
        print(f"[*] Initializing Agent-X1 Harness with config: {self.config_path}")
        
        try:
            router = InferenceRouter(config_path=self.config_path)
        except Exception as e:
            print(f"[!] Error loading config: {e}")
            return "failed"
            
        storage_cfg = router.config.get("storage", {})
        db_path = storage_cfg.get("db_path", "tmp/memory.db")
        audit_path = storage_cfg.get("audit_log_path", "logs/audit_lineage.jsonl")
        encrypt_logs = router.config.get("security", {}).get("encrypt_logs", False)
        
        # Load Audit and Memory managers
        from src.audit.lineage import LineageLogger
        lineage = LineageLogger(log_path=audit_path, encrypt=encrypt_logs)
        memory = MemoryManager(db_path=db_path, router=router)
        tools = ToolRunner(lineage_logger=lineage, memory_manager=memory)
        
        engine = OrchestrationEngine(router=router, tools=tools, memory=memory)
        
        correlation_id = engine.generate_correlation_id()
        print(f"[*] Generated correlation ID: {correlation_id}")
        
        if interactive:
            finalized_plan = self.interactive_planning_loop(goal, engine, resume_session=resume_session)
            if not finalized_plan:
                return "aborted"
            if finalized_plan == "scheduled":
                return "completed"
            print("[*] Decomposing finalized plan into task DAG...")
            tasks = engine.decompose_plan_into_tasks(finalized_plan)
        else:
            if dry_run:
                print("[*] Running in DRY-RUN simulation mode.")
                tasks = [
                    {
                        "id": "t-dry-1",
                        "name": "Dry run analysis",
                        "depends_on": [],
                        "worker": "codeworker",
                        "args": {"task_description": f"Simulating goal: {goal}"}
                    }
                ]
            else:
                print("[*] Decomposing goal into tasks...")
                tasks = engine.decompose_goal(goal)
            
        print("\n=== GENERATED TASK DAG ===")
        for t in tasks:
            deps = f" (depends on: {', '.join(t['depends_on'])})" if t.get("depends_on") else ""
            print(f"- [{t['id']}] {t['name']} - Worker: {t['worker']}{deps}")
        print("==========================\n")
        
        if dry_run:
            print("[*] Dry run completed successfully.")
            return "completed"

        # Goal Planning Gate
        if auto_approve:
            engine.write_tasks_plan(goal, tasks, correlation_id, status="Approved")
        else:
            engine.write_tasks_plan(goal, tasks, correlation_id, status="Awaiting Approval")
            print(f"[*] Goal Planning Gate: Task execution plan written to tasks_plan.md.")
            print(f"[*] You can approve by checking the box in the file and saving it, or via console.")
            
            user_input = input("Do you approve the execution plan? (y/n): ").strip().lower()
            if user_input == "y" or engine.check_plan_file_approved():
                print("[*] Plan approved. Resuming execution...")
                engine.update_tasks_plan_status(status="Approved")
            else:
                print("[!] Plan rejected. Aborting execution.")
                engine.update_tasks_plan_status(status="Aborted")
                return "aborted"
            
        print("[*] Starting execution loop...")
        status = engine.execute_loop(correlation_id, tasks, auto_approve=auto_approve)
        
        print(f"\n[*] Execution loop completed with status: {status}")
        print(f"[*] Audit logs saved at: {audit_path}")
        return status

def main():
    setup_readline()
    parser = argparse.ArgumentParser(description="Agent-X1 CLI Gateway")
    parser.add_argument("--goal", type=str, help="The goal for Agent-X1 to achieve")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config.yaml file")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Auto approve Major task shifts")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying files")
    parser.add_argument("--interactive", "-i", action="store_true", help="Enable back-and-forth interactive planning chat")
    parser.add_argument("--chat", "-c", action="store_true", help="Start conversational chat mode with the agent")
    
    args = parser.parse_args()
    
    interactive = args.interactive or args.chat
    goal = args.goal
    resume_session = None
    
    if interactive:
        print_startup_banner(config_path=args.config)
    
    gateway = CliGateway(config_path=args.config)

    
    if not goal:
        if interactive:
            try:
                NEON_BLUE = "\033[1;38;5;39m"
                RESET = "\033[0m"
                try:
                    goal = input(f"{NEON_BLUE}Please enter your goal (type /resume to restore a pinned session): ").strip()
                finally:
                    print(RESET, end="", flush=True)
            except KeyboardInterrupt:
                print("\n[!] Exiting.")
                sys.exit(0)
            if not goal:
                print("[!] Error: A goal is required to run the agent.")
                sys.exit(1)
        else:
            parser.error("the following arguments are required: --goal (or run interactively with --interactive or --chat)")
            
    # Handle direct /exit or /resume from startup prompt
    if goal.lower() == "/exit":
        print("\n[!] Exiting.")
        sys.exit(0)
    elif goal.lower() == "/resume":
        try:
            router = InferenceRouter(config_path=gateway.config_path)
            db_path = router.config.get("storage", {}).get("db_path", "tmp/memory.db")
            memory = MemoryManager(db_path=db_path, router=router)
            pinned = memory.get_pinned_sessions()
            if not pinned:
                print("\033[91m[!] No pinned sessions found.\033[0m")
                sys.exit(1)
            
            print(f"\n\033[38;5;178mAvailable pinned sessions:\033[0m")
            for idx, session in enumerate(pinned, 1):
                name = session.get("name")
                session_goal = session.get("goal")
                ts = session.get("timestamp")
                import datetime
                dt_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {idx}. {name} - Goal: {session_goal} (Pinned: {dt_str})")
            
            choice_input = input("\nChoose a session number or name to resume (or type '/exit' to cancel): ").strip()
            if not choice_input or choice_input.lower() == "/exit":
                print("[*] Exiting.")
                sys.exit(0)
                
            target_session = None
            try:
                choice_idx = int(choice_input) - 1
                if 0 <= choice_idx < len(pinned):
                    target_session = pinned[choice_idx]
            except ValueError:
                pass
                
            if not target_session:
                for session in pinned:
                    if session.get("name") == choice_input:
                        target_session = session
                        break
                        
            if not target_session:
                print("\033[91m[!] Error: Invalid selection or name.\033[0m")
                sys.exit(1)
                
            resume_session = target_session.get("name")
            goal = target_session.get("goal")
            
        except Exception as e:
            print(f"\033[91m[!] Error listing pinned sessions: {e}\033[0m")
            sys.exit(1)
            
    status = gateway.run_goal(goal, auto_approve=args.auto_approve, dry_run=args.dry_run, interactive=interactive, resume_session=resume_session)
    
    sys.exit(0 if status == "completed" else 1)

if __name__ == "__main__":
    main()
