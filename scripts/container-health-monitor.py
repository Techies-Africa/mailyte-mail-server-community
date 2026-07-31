#!/usr/bin/env python3
"""
Mailyte Container Health Monitor and Auto-Fix Tool
Monitors all containers, detects issues, and automatically fixes common problems
"""

import os
import sys
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


class ContainerHealthMonitor:
    def __init__(self):
        self.root_dir = Path(__file__).parent
        self.issues_found = []
        self.fixes_applied = []
        self.containers_status = {}

    def print_header(self, text):
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(80)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}\n")

    def print_section(self, text):
        print(f"\n{Colors.OKBLUE}{Colors.BOLD}--- {text} ---{Colors.ENDC}")

    def success(self, msg):
        print(f"{Colors.OKGREEN}✓{Colors.ENDC} {msg}")

    def warning(self, msg):
        print(f"{Colors.WARNING}⚠{Colors.ENDC} {msg}")
        self.issues_found.append(msg)

    def error(self, msg):
        print(f"{Colors.FAIL}✗{Colors.ENDC} {msg}")
        self.issues_found.append(msg)

    def info(self, msg):
        print(f"{Colors.OKCYAN}ℹ{Colors.ENDC} {msg}")

    def fix_applied(self, msg):
        print(f"{Colors.OKGREEN}🔧{Colors.ENDC} {msg}")
        self.fixes_applied.append(msg)

    def run_command(self, cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
        """Run a command and return exit code, stdout, stderr"""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=self.root_dir
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def get_container_list(self) -> List[Dict]:
        """Get list of all containers defined in docker-compose"""
        code, stdout, stderr = self.run_command(
            ["docker", "compose", "ps", "-a", "--format", "json"]
        )

        if code != 0:
            self.error(f"Failed to get container list: {stderr}")
            return []

        try:
            # Handle both single JSON object and newline-delimited JSON
            containers = []
            for line in stdout.strip().split("\n"):
                if line:
                    containers.append(json.loads(line))
            return containers
        except json.JSONDecodeError:
            self.error("Failed to parse container list JSON")
            return []

    def check_container_status(self, container: Dict) -> Dict:
        """Check detailed status of a container"""
        name = container.get("Name", "unknown")
        state = container.get("State", "unknown")
        status = container.get("Status", "unknown")

        health_info = {
            "name": name,
            "state": state,
            "status": status,
            "healthy": state == "running",
            "issues": [],
            "logs": "",
        }

        # Get container logs
        code, stdout, stderr = self.run_command(
            ["docker", "logs", "--tail", "50", name], timeout=10
        )

        if code == 0:
            health_info["logs"] = stdout

        # Check for common issues in logs
        if stdout:
            logs_lower = stdout.lower()

            # Database connection issues
            if "connection refused" in logs_lower or "can't connect" in logs_lower:
                health_info["issues"].append("database_connection")

            # Permission issues
            if "permission denied" in logs_lower:
                health_info["issues"].append("permission_denied")

            # Port binding issues
            if (
                "address already in use" in logs_lower
                or "bind: address already in use" in logs_lower
            ):
                health_info["issues"].append("port_conflict")

            # Missing dependencies
            if "modulenotfounderror" in logs_lower or "importerror" in logs_lower:
                health_info["issues"].append("missing_dependencies")

            # Configuration errors
            if "configuration error" in logs_lower or "config error" in logs_lower:
                health_info["issues"].append("config_error")

            # Out of memory
            if "out of memory" in logs_lower or "oom" in logs_lower:
                health_info["issues"].append("out_of_memory")

            # File not found
            if "no such file or directory" in logs_lower:
                health_info["issues"].append("file_not_found")

        return health_info

    def fix_database_connection(self, container_name: str) -> bool:
        """Fix database connection issues"""
        self.info(f"Attempting to fix database connection for {container_name}")

        # Check if MySQL is running
        code, stdout, stderr = self.run_command(
            ["docker", "compose", "ps", "mysql", "--format", "json"]
        )

        if code == 0 and stdout:
            try:
                mysql_info = json.loads(stdout.strip().split("\n")[0])
                if mysql_info.get("State") != "running":
                    self.info("Starting MySQL container...")
                    code, _, _ = self.run_command(["docker", "compose", "up", "-d", "mysql"])
                    if code == 0:
                        self.fix_applied("MySQL container started")
                        time.sleep(5)  # Wait for MySQL to be ready
                        return True
            except:
                pass

        # Restart the dependent container
        self.info(f"Restarting {container_name}...")
        code, _, _ = self.run_command(["docker", "compose", "restart", container_name])
        if code == 0:
            self.fix_applied(f"Restarted {container_name}")
            return True

        return False

    def fix_permission_denied(self, container_name: str) -> bool:
        """Fix permission issues"""
        self.info(f"Attempting to fix permission issues for {container_name}")

        # Common directories that might need permission fixes
        dirs_to_fix = [
            "storage",
            "logs",
            "storage/mail_data",
            "storage/ssl_certs",
            "storage/attachments",
        ]

        for dir_name in dirs_to_fix:
            dir_path = self.root_dir / dir_name
            if dir_path.exists():
                try:
                    # Make directories writable
                    os.chmod(dir_path, 0o755)
                    self.fix_applied(f"Fixed permissions for {dir_name}")
                except Exception as e:
                    self.warning(f"Could not fix permissions for {dir_name}: {e}")

        # Restart container
        code, _, _ = self.run_command(["docker", "compose", "restart", container_name])
        if code == 0:
            self.fix_applied(f"Restarted {container_name}")
            return True

        return False

    def fix_port_conflict(self, container_name: str, health_info: Dict) -> bool:
        """Fix port binding conflicts"""
        self.info(f"Attempting to fix port conflict for {container_name}")

        # Extract port from logs if possible
        logs = health_info.get("logs", "")

        self.warning(f"Port conflict detected for {container_name}")
        self.info("You may need to:")
        self.info("1. Stop the conflicting service using the port")
        self.info("2. Change the port mapping in docker-compose.yml")
        self.info("3. Use 'docker compose down' and restart")

        return False

    def fix_missing_dependencies(self, container_name: str) -> bool:
        """Fix missing dependencies"""
        self.info(f"Attempting to fix missing dependencies for {container_name}")

        # Rebuild the container
        self.info(f"Rebuilding {container_name}...")
        code, _, stderr = self.run_command(
            ["docker", "compose", "build", "--no-cache", container_name], timeout=300
        )

        if code == 0:
            self.fix_applied(f"Rebuilt {container_name}")
            # Restart the container
            code, _, _ = self.run_command(["docker", "compose", "up", "-d", container_name])
            if code == 0:
                self.fix_applied(f"Restarted {container_name}")
                return True
        else:
            self.error(f"Failed to rebuild {container_name}: {stderr}")

        return False

    def fix_file_not_found(self, container_name: str) -> bool:
        """Fix file not found issues"""
        self.info(f"Attempting to fix file not found issues for {container_name}")

        # Create missing directories
        dirs_to_create = [
            "storage",
            "logs",
            "config",
            "storage/mail_data",
            "storage/ssl_certs",
            "storage/attachments",
            "storage/qdrant_data",
        ]

        for dir_name in dirs_to_create:
            dir_path = self.root_dir / dir_name
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
                self.fix_applied(f"Created directory: {dir_name}")

        # Restart container
        code, _, _ = self.run_command(["docker", "compose", "restart", container_name])
        if code == 0:
            self.fix_applied(f"Restarted {container_name}")
            return True

        return False

    def auto_fix_issues(self, container_name: str, health_info: Dict) -> bool:
        """Automatically fix detected issues"""
        issues = health_info.get("issues", [])

        if not issues:
            return True

        self.print_section(f"Auto-fixing issues for {container_name}")

        fixed = False
        for issue in issues:
            if issue == "database_connection":
                fixed = self.fix_database_connection(container_name) or fixed
            elif issue == "permission_denied":
                fixed = self.fix_permission_denied(container_name) or fixed
            elif issue == "port_conflict":
                fixed = self.fix_port_conflict(container_name, health_info) or fixed
            elif issue == "missing_dependencies":
                fixed = self.fix_missing_dependencies(container_name) or fixed
            elif issue == "file_not_found":
                fixed = self.fix_file_not_found(container_name) or fixed

        return fixed

    def check_all_containers(self):
        """Check health of all containers"""
        self.print_section("Checking Container Health")

        containers = self.get_container_list()

        if not containers:
            self.warning("No containers found. Run 'docker compose up -d' to start services")
            return

        for container in containers:
            name = container.get("Name", "unknown")
            health_info = self.check_container_status(container)
            self.containers_status[name] = health_info

            if health_info["healthy"]:
                self.success(f"{name}: Running")
            else:
                self.error(f"{name}: {health_info['state']} - {health_info['status']}")

                if health_info["issues"]:
                    self.warning(f"  Issues detected: {', '.join(health_info['issues'])}")

    def fix_all_issues(self):
        """Attempt to fix all detected issues"""
        self.print_section("Auto-fixing Detected Issues")

        for name, health_info in self.containers_status.items():
            if not health_info["healthy"] or health_info["issues"]:
                self.auto_fix_issues(name, health_info)

    def verify_fixes(self):
        """Verify that fixes were successful"""
        self.print_section("Verifying Fixes")

        time.sleep(5)  # Wait for containers to stabilize

        containers = self.get_container_list()
        fixed_count = 0
        still_broken = 0

        for container in containers:
            name = container.get("Name", "unknown")
            state = container.get("State", "unknown")

            if state == "running":
                self.success(f"{name}: Now running")
                fixed_count += 1
            else:
                self.error(f"{name}: Still {state}")
                still_broken += 1

        return fixed_count, still_broken

    def generate_health_report(self):
        """Generate a detailed health report"""
        self.print_header("CONTAINER HEALTH REPORT")

        total_containers = len(self.containers_status)
        healthy_containers = sum(1 for h in self.containers_status.values() if h["healthy"])
        unhealthy_containers = total_containers - healthy_containers

        print(f"Total Containers: {total_containers}")
        print(f"{Colors.OKGREEN}Healthy: {healthy_containers}{Colors.ENDC}")
        print(f"{Colors.FAIL}Unhealthy: {unhealthy_containers}{Colors.ENDC}")
        print(f"\n{Colors.WARNING}Issues Found: {len(self.issues_found)}{Colors.ENDC}")
        print(f"{Colors.OKGREEN}Fixes Applied: {len(self.fixes_applied)}{Colors.ENDC}")

        if self.fixes_applied:
            print(f"\n{Colors.BOLD}Fixes Applied:{Colors.ENDC}")
            for fix in self.fixes_applied:
                print(f"  • {fix}")

        if unhealthy_containers > 0:
            print(f"\n{Colors.BOLD}Unhealthy Containers:{Colors.ENDC}")
            for name, health in self.containers_status.items():
                if not health["healthy"]:
                    print(f"\n  {Colors.FAIL}{name}{Colors.ENDC}")
                    print(f"    State: {health['state']}")
                    print(f"    Status: {health['status']}")
                    if health["issues"]:
                        print(f"    Issues: {', '.join(health['issues'])}")

                    # Show last few log lines
                    logs = health.get("logs", "").strip().split("\n")
                    if logs:
                        print(f"    Recent logs:")
                        for log_line in logs[-5:]:
                            if log_line.strip():
                                print(f"      {log_line[:100]}")

    def run_monitoring(self, auto_fix: bool = True):
        """Run the complete monitoring and fixing process"""
        self.print_header("MAILYTE CONTAINER HEALTH MONITOR")

        self.check_all_containers()

        if auto_fix:
            self.fix_all_issues()
            fixed, broken = self.verify_fixes()

        self.generate_health_report()

        # Save report
        report = {
            "timestamp": datetime.now().isoformat(),
            "containers": self.containers_status,
            "issues_found": self.issues_found,
            "fixes_applied": self.fixes_applied,
        }

        report_file = self.root_dir / "container_health_report.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)

        self.info(f"\nReport saved to: {report_file}")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Monitor and fix container health issues")
    parser.add_argument("--no-fix", action="store_true", help="Only monitor, do not auto-fix")
    parser.add_argument("--continuous", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval", type=int, default=60, help="Interval in seconds for continuous mode"
    )

    args = parser.parse_args()

    monitor = ContainerHealthMonitor()

    if args.continuous:
        print(f"Running in continuous mode (interval: {args.interval}s). Press Ctrl+C to stop.")
        try:
            while True:
                monitor.run_monitoring(auto_fix=not args.no_fix)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
    else:
        monitor.run_monitoring(auto_fix=not args.no_fix)


if __name__ == "__main__":
    main()
