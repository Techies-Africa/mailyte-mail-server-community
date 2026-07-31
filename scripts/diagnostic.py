#!/usr/bin/env python3
"""
Mailyte Mail Server - Comprehensive Diagnostic Tool
Analyzes the system and provides a roadmap to production readiness
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


class MailServerDiagnostic:
    def __init__(self):
        self.root_dir = Path(__file__).parent
        self.issues = []
        self.warnings = []
        self.successes = []
        self.report = {}

    def print_header(self, text):
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(80)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}\n")

    def print_section(self, text):
        print(f"\n{Colors.OKBLUE}{Colors.BOLD}--- {text} ---{Colors.ENDC}")

    def success(self, msg):
        print(f"{Colors.OKGREEN}✓{Colors.ENDC} {msg}")
        self.successes.append(msg)

    def warning(self, msg):
        print(f"{Colors.WARNING}⚠{Colors.ENDC} {msg}")
        self.warnings.append(msg)

    def error(self, msg):
        print(f"{Colors.FAIL}✗{Colors.ENDC} {msg}")
        self.issues.append(msg)

    def info(self, msg):
        print(f"{Colors.OKCYAN}ℹ{Colors.ENDC} {msg}")

    def check_docker_environment(self):
        """Check if Docker and Docker Compose are available"""
        self.print_section("Docker Environment Check")

        try:
            result = subprocess.run(
                ["docker", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.success(f"Docker installed: {result.stdout.strip()}")
                self.report["docker"] = {"installed": True, "version": result.stdout.strip()}
            else:
                self.error("Docker not properly configured")
                self.report["docker"] = {"installed": False}
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.error("Docker not installed or not in PATH")
            self.report["docker"] = {"installed": False}

        try:
            result = subprocess.run(
                ["docker", "compose", "version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.success(f"Docker Compose installed: {result.stdout.strip()}")
                self.report["docker_compose"] = {
                    "installed": True,
                    "version": result.stdout.strip(),
                }
            else:
                self.error("Docker Compose not available")
                self.report["docker_compose"] = {"installed": False}
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.error("Docker Compose not installed")
            self.report["docker_compose"] = {"installed": False}

    def check_environment_config(self):
        """Check environment configuration files"""
        self.print_section("Environment Configuration Check")

        env_file = self.root_dir / ".env"
        env_example = self.root_dir / ".env.example"

        if env_example.exists():
            self.success(f".env.example found ({env_example.stat().st_size} bytes)")
        else:
            self.error(".env.example not found")

        if env_file.exists():
            self.success(f".env file configured ({env_file.stat().st_size} bytes)")
            self.report["env_file"] = {"exists": True, "path": str(env_file)}
        else:
            self.warning(".env file not found - copy from .env.example")
            self.report["env_file"] = {"exists": False}

    def check_service_structure(self):
        """Check if all service directories exist and have required files"""
        self.print_section("Service Structure Check")

        services = {
            "mailer": ["postfix", "dovecot", "rspamd", "cert_manager", "log_analyzer"],
            "worker": [
                "api",
                "tracking",
                "webhooks",
                "rate_limiter",
                "analytics",
                "monitoring",
                "rag",
                "storage_usage",
                "queue_manager",
                "archiver",
                "activesync",
                "encryption",
                "dashboard",
                "delivery_optimizer",
                "cloud_sync",
            ],
        }

        service_status = {}

        for category, service_list in services.items():
            category_path = self.root_dir / category

            if not category_path.exists():
                self.error(f"{category}/ directory not found")
                continue

            self.info(f"Checking {category} services:")

            for service in service_list:
                service_path = category_path / service
                status = self.check_service_completeness(service_path, service)
                service_status[f"{category}/{service}"] = status

        self.report["services"] = service_status

    def check_service_completeness(self, service_path: Path, service_name: str) -> dict:
        """Check if a service is complete"""
        status = {
            "exists": service_path.exists(),
            "has_dockerfile": False,
            "has_requirements": False,
            "has_app": False,
            "has_readme": False,
            "completeness": 0,
        }

        if not service_path.exists():
            self.error(f"  {service_name}: Directory missing")
            return status

        checks = []

        dockerfile = service_path / "Dockerfile"
        if dockerfile.exists():
            status["has_dockerfile"] = True
            checks.append(True)
            msg = f"  {service_name}: Dockerfile ✓"
        else:
            checks.append(False)
            msg = f"  {service_name}: Dockerfile ✗"

        requirements = service_path / "requirements.txt"
        if requirements.exists():
            status["has_requirements"] = True
            checks.append(True)
        else:
            checks.append(False)

        app_file = service_path / "app.py"
        main_file = service_path / "main.py"
        if app_file.exists() or main_file.exists():
            status["has_app"] = True
            checks.append(True)
        else:
            checks.append(False)

        readme = service_path / "README.md"
        if readme.exists():
            status["has_readme"] = True

        completeness = (sum(checks) / len(checks)) * 100
        status["completeness"] = completeness

        if completeness >= 80:
            print(f"{Colors.OKGREEN}{msg} - {completeness:.0f}% complete{Colors.ENDC}")
        elif completeness >= 50:
            print(f"{Colors.WARNING}{msg} - {completeness:.0f}% complete{Colors.ENDC}")
        else:
            print(f"{Colors.FAIL}{msg} - {completeness:.0f}% complete{Colors.ENDC}")

        return status

    def check_database_setup(self):
        """Check database configuration and migrations"""
        self.print_section("Database Setup Check")

        db_dir = self.root_dir / "database"
        if db_dir.exists():
            migrations = list(db_dir.glob("*.sql"))
            if migrations:
                self.success(f"Found {len(migrations)} database migration files")
                self.report["database"] = {
                    "migrations_exist": True,
                    "migration_count": len(migrations),
                    "files": [m.name for m in migrations],
                }
            else:
                self.warning("No SQL migration files found in database/")
                self.report["database"] = {"migrations_exist": False}
        else:
            self.error("database/ directory not found")
            self.report["database"] = {"directory_exists": False}

    def check_docker_compose(self):
        """Analyze docker-compose configuration"""
        self.print_section("Docker Compose Configuration Check")

        compose_files = [
            self.root_dir / "docker-compose.yml",
            self.root_dir / "docker-compose.dev.yml",
        ]

        compose_status = {}

        for compose_file in compose_files:
            if compose_file.exists():
                self.success(f"{compose_file.name} found")

                with open(compose_file) as f:
                    content = f.read()
                    enabled_services = content.count("build:")
                    commented_services = content.count("#   build:")

                    compose_status[compose_file.name] = {
                        "exists": True,
                        "enabled_services": enabled_services,
                        "commented_services": commented_services,
                    }

                    self.info(f"  Active services: {enabled_services}")
                    self.warning(f"  Commented services: {commented_services}")
            else:
                self.error(f"{compose_file.name} not found")
                compose_status[compose_file.name] = {"exists": False}

        self.report["docker_compose_files"] = compose_status

    def check_dependencies(self):
        """Check if Python dependencies can be installed"""
        self.print_section("Python Dependencies Check")

        try:
            result = subprocess.run([sys.executable, "--version"], capture_output=True, text=True)
            self.success(f"Python: {result.stdout.strip()}")
            self.report["python"] = {"version": result.stdout.strip()}
        except Exception as e:
            self.error(f"Python check failed: {e}")

        pyproject = self.root_dir / "pyproject.toml"
        if pyproject.exists():
            self.success("pyproject.toml found")
        else:
            self.warning("pyproject.toml not found")

        uv_lock = self.root_dir / "uv.lock"
        if uv_lock.exists():
            self.success("uv.lock found - dependencies locked")
        else:
            self.warning("uv.lock not found")

    def check_critical_paths(self):
        """Check critical directories and files"""
        self.print_section("Critical Paths Check")

        critical_dirs = [
            "storage",
            "logs",
            "config",
            "shared",
            "database",
            "storage/mail_data",
            "storage/ssl_certs",
            "storage/attachments",
        ]

        paths_status = {}

        for dir_name in critical_dirs:
            dir_path = self.root_dir / dir_name
            if dir_path.exists():
                self.success(f"{dir_name}/ exists")
                paths_status[dir_name] = True
            else:
                self.warning(f"{dir_name}/ missing - will be created on startup")
                paths_status[dir_name] = False

        self.report["critical_paths"] = paths_status

    def generate_production_roadmap(self):
        """Generate a roadmap to production readiness"""
        self.print_header("PRODUCTION READINESS ROADMAP")

        roadmap = {"immediate": [], "high_priority": [], "medium_priority": [], "optional": []}

        if not self.report.get("env_file", {}).get("exists"):
            roadmap["immediate"].append(
                {
                    "task": "Create .env file",
                    "command": "cp .env.example .env",
                    "description": "Configure environment variables for your setup",
                }
            )

        if not self.report.get("docker", {}).get("installed"):
            roadmap["immediate"].append(
                {
                    "task": "Install Docker",
                    "command": "Visit https://docs.docker.com/get-docker/",
                    "description": "Required for containerized deployment",
                }
            )

        incomplete_services = []
        for service, status in self.report.get("services", {}).items():
            if status.get("completeness", 0) < 80:
                incomplete_services.append((service, status["completeness"]))

        if incomplete_services:
            incomplete_services.sort(key=lambda x: x[1])
            roadmap["high_priority"].append(
                {
                    "task": "Complete incomplete services",
                    "services": [f"{s[0]} ({s[1]:.0f}%)" for s in incomplete_services[:5]],
                    "description": "These services need Dockerfiles, requirements.txt, and app files",
                }
            )

        compose_status = self.report.get("docker_compose_files", {}).get("docker-compose.yml", {})
        if compose_status.get("commented_services", 0) > 0:
            roadmap["medium_priority"].append(
                {
                    "task": "Enable Docker services",
                    "description": f"{compose_status['commented_services']} services are commented out",
                    "action": "Uncomment services in docker-compose.yml as they become ready",
                }
            )

        roadmap["high_priority"].extend(
            [
                {
                    "task": "Database setup",
                    "command": "python database/migrate.py",
                    "description": "Initialize database schema and tables",
                },
                {
                    "task": "SSL certificates",
                    "description": "Configure SSL certificates for secure mail delivery",
                    "action": "Use cert_manager service or provide certificates manually",
                },
                {
                    "task": "DNS configuration",
                    "description": "Configure MX, SPF, DKIM, DMARC records",
                    "critical": True,
                },
            ]
        )

        roadmap["medium_priority"].extend(
            [
                {
                    "task": "Testing",
                    "command": "python test_runner.py",
                    "description": "Run integration tests for all services",
                },
                {
                    "task": "Monitoring setup",
                    "description": "Configure monitoring and alerting",
                    "services": ["worker/monitoring", "health_monitor"],
                },
                {
                    "task": "Backup configuration",
                    "description": "Set up automated backups",
                    "services": ["worker/archiver", "storage/backup"],
                },
            ]
        )

        roadmap["optional"].extend(
            [
                {
                    "task": "Cloud storage integration",
                    "description": "Configure AWS S3 or Azure Blob storage",
                    "services": ["worker/cloud_sync"],
                },
                {
                    "task": "Email tracking",
                    "description": "Enable open and click tracking",
                    "services": ["worker/tracking"],
                },
                {
                    "task": "RAG/AI features",
                    "description": "Configure vector database for AI search",
                    "services": ["worker/rag"],
                },
            ]
        )

        self.report["roadmap"] = roadmap
        self.print_roadmap(roadmap)

    def print_roadmap(self, roadmap):
        """Print the production roadmap"""

        priorities = [
            ("IMMEDIATE ACTION REQUIRED", "immediate", Colors.FAIL),
            ("HIGH PRIORITY", "high_priority", Colors.WARNING),
            ("MEDIUM PRIORITY", "medium_priority", Colors.OKBLUE),
            ("OPTIONAL ENHANCEMENTS", "optional", Colors.OKCYAN),
        ]

        for title, key, color in priorities:
            items = roadmap.get(key, [])
            if not items:
                continue

            print(f"\n{color}{Colors.BOLD}{title}:{Colors.ENDC}")
            print(f"{color}{'─' * 80}{Colors.ENDC}")

            for i, item in enumerate(items, 1):
                print(f"\n{color}{i}. {item['task']}{Colors.ENDC}")
                if "description" in item:
                    print(f"   {item['description']}")
                if "command" in item:
                    print(f"   Command: {Colors.BOLD}{item['command']}{Colors.ENDC}")
                if "services" in item:
                    print(f"   Services: {', '.join(item['services'])}")
                if "action" in item:
                    print(f"   Action: {item['action']}")
                if item.get("critical"):
                    print(f"   {Colors.FAIL}⚠ CRITICAL FOR PRODUCTION{Colors.ENDC}")

    def generate_quick_start_commands(self):
        """Generate quick start commands"""
        self.print_header("QUICK START COMMANDS")

        print(f"{Colors.BOLD}1. Initial Setup:{Colors.ENDC}")
        print("   cp .env.example .env")
        print("   # Edit .env with your configuration")
        print()

        print(f"{Colors.BOLD}2. Start with Docker Compose:{Colors.ENDC}")
        print("   docker compose up -d mysql")
        print("   docker compose up -d rate_limiter")
        print("   # Enable more services as needed")
        print()

        print(f"{Colors.BOLD}3. Check service health:{Colors.ENDC}")
        print("   docker compose ps")
        print("   docker compose logs -f [service_name]")
        print()

        print(f"{Colors.BOLD}4. Run diagnostics again:{Colors.ENDC}")
        print("   python diagnostic.py")

    def save_report(self):
        """Save diagnostic report to JSON"""
        report_file = self.root_dir / "diagnostic_report.json"
        self.report["timestamp"] = datetime.now().isoformat()
        self.report["summary"] = {
            "total_issues": len(self.issues),
            "total_warnings": len(self.warnings),
            "total_successes": len(self.successes),
        }

        with open(report_file, "w") as f:
            json.dump(self.report, f, indent=2)

        self.info(f"\nFull report saved to: {report_file}")

    def print_summary(self):
        """Print diagnostic summary"""
        self.print_header("DIAGNOSTIC SUMMARY")

        print(f"{Colors.OKGREEN}✓ Successes: {len(self.successes)}{Colors.ENDC}")
        print(f"{Colors.WARNING}⚠ Warnings: {len(self.warnings)}{Colors.ENDC}")
        print(f"{Colors.FAIL}✗ Issues: {len(self.issues)}{Colors.ENDC}")

        if len(self.issues) == 0:
            print(f"\n{Colors.OKGREEN}{Colors.BOLD}System is ready for deployment!{Colors.ENDC}")
        elif len(self.issues) < 5:
            print(
                f"\n{Colors.WARNING}{Colors.BOLD}System needs minor fixes before production{Colors.ENDC}"
            )
        else:
            print(
                f"\n{Colors.FAIL}{Colors.BOLD}System requires significant work before production{Colors.ENDC}"
            )

    def run_all_diagnostics(self):
        """Run all diagnostic checks"""
        self.print_header("MAILYTE EMAIL SERVER DIAGNOSTIC TOOL")

        self.check_docker_environment()
        self.check_environment_config()
        self.check_dependencies()
        self.check_critical_paths()
        self.check_database_setup()
        self.check_docker_compose()
        self.check_service_structure()

        self.print_summary()
        self.generate_production_roadmap()
        self.generate_quick_start_commands()
        self.save_report()


def main():
    """Main entry point"""
    diagnostic = MailServerDiagnostic()
    diagnostic.run_all_diagnostics()


if __name__ == "__main__":
    main()
