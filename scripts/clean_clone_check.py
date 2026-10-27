"""Measure the documented launch from a local clean clone and empty build cache.

Run after recording: the existing app is briefly stopped to release fixed8080.
Data volumes are retained. Test containers and clone remain for inspection.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def command(args, *, cwd=ROOT, env=None, log=None):
    return subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT if log else None, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--prune", action="store_true", help="Run the requested docker builder prune -af")
    parser.add_argument("--stop-current-app", action="store_true")
    parser.add_argument("--current-project", default="okno", help="Existing Compose project to pause without deleting volumes")
    parser.add_argument("--browser", action="store_true", help="Run the real UI preview with the installed demo tooling")
    parser.add_argument("--check", action="store_true", help="Run make check in the clone after product readiness, reusing QA dependencies only")
    parser.add_argument("--all-smokes", action="store_true", help="Run every frontend/scripts/*-smoke.mjs without recording video")
    parser.add_argument("--cleanup", action="store_true", help="Remove this temporary project's containers, volumes and clone after the check")
    parser.add_argument("--without-env", action="store_true", help="Prove default startup with no .env file")
    parser.add_argument("--docker-only-path", action="store_true", help="Launch Compose with no host Python/Node in PATH")
    args = parser.parse_args()
    args.clone = args.clone.resolve()
    args.report = args.report.resolve()
    if args.clone.exists():
        parser.error("Clone destination must not exist")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.report.with_suffix(".log")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    free_before = shutil.disk_usage(ROOT).free
    if free_before < 5 * 1024**3:
        raise RuntimeError("Acceptance build needs at least 5 GiB of free host disk space")
    command(["git", "clone", "--no-local", str(ROOT), str(args.clone)])
    if not args.without_env:
        shutil.copyfile(args.clone/".env.example", args.clone/".env")
    # A new folder alone is insufficient: the Compose file has a fixed name.
    # Never reuse the previous acceptance run's database or runtime volume.
    project = "okno-clean-" + uuid4().hex[:12]
    environment = {**os.environ, "COMPOSE_PROJECT_NAME": project, "LLM_MODE": "offline", "LLM_API_KEY": ""}
    for name in ("DB_URL", "APP_PORT", "COMPOSE_FILE", "COMPOSE_PROFILES"):
        environment.pop(name, None)  # Verify product defaults, not ambient overrides.
    launch_environment = environment.copy()
    cli_directory = None
    if args.docker_only_path:
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError("Docker CLI is required")
        cli_directory = tempfile.TemporaryDirectory(prefix="okno-docker-cli-")
        binaries = {"docker": Path(docker).resolve()}
        docker_folder = binaries["docker"].parent
        for name in ("docker-compose", "docker-buildx"):
            candidate = shutil.which(name) or str(docker_folder/name)
            if Path(candidate).is_file():
                binaries[name] = Path(candidate).resolve()
        for candidate in docker_folder.glob("docker-credential-*"):
            if candidate.is_file():
                binaries[candidate.name] = candidate.resolve()
        for name, executable in binaries.items():
            (Path(cli_directory.name)/name).symlink_to(executable)
        launch_environment["PATH"] = cli_directory.name
    if args.stop_current_app:
        command(["docker", "compose", "-p", args.current_project, "stop", "app"])
    with socket.socket() as port_probe:
        if port_probe.connect_ex(("127.0.0.1", 8080)) == 0:
            if args.stop_current_app:
                command(["docker", "compose", "-p", args.current_project, "start", "app"])
            raise RuntimeError("Port8080 is still occupied; cannot verify a fresh application's health")
    report = {"revision": revision, "clone": str(args.clone), "checked_at": datetime.now(UTC).isoformat(),
              "command": "docker compose up --build", "project": project, "fresh_project_volumes": True,
              "pruned_build_cache": args.prune, "passed": False,
              "free_disk_bytes_before_clone": free_before,
              "env_file_created": not args.without_env, "docker_only_launch_path": args.docker_only_path,
              "llm_api_key_empty": True,
              "launch_path_has_python": bool(shutil.which("python3", path=launch_environment["PATH"])),
              "launch_path_has_node": bool(shutil.which("node", path=launch_environment["PATH"]))}
    report["preexisting_project_volumes"] = subprocess.check_output(
        ["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"],
        text=True,
    ).splitlines()
    if report["preexisting_project_volumes"]:
        raise RuntimeError("Acceptance project already has volumes")
    with log_path.open("w") as log:
        prepared = time.monotonic()
        if args.prune:
            command(["docker", "builder", "prune", "-af"], log=log)
        report["prune_seconds"] = round(time.monotonic()-prepared, 3)
        report["free_disk_bytes_before_build"] = shutil.disk_usage(ROOT).free
        started = time.monotonic()
        compose = subprocess.Popen(["docker", "compose", "up", "--build"], cwd=args.clone, env=launch_environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = started + 600
            while time.monotonic() < deadline:
                if compose.poll() is not None:
                    raise RuntimeError(f"Compose exited before readiness: {compose.returncode}")
                try:
                    with urlopen("http://localhost:8080/health", timeout=2) as response:
                        ready = json.load(response) == {"status": "ok"}
                    if ready:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(2)
            else:
                raise TimeoutError("Launch exceeded10minutes")
            report["launch_seconds"] = round(time.monotonic()-started, 3)
            ready_at = datetime.now(UTC)
            report["health_observed_at"] = ready_at.isoformat()
            container_id = subprocess.check_output(
                ["docker", "compose", "ps", "-q", "app"], cwd=args.clone, env=environment, text=True,
            ).strip()
            container = json.loads(subprocess.check_output(["docker", "inspect", container_id], text=True))[0]
            container_started = datetime.fromisoformat(container["State"]["StartedAt"].replace("Z", "+00:00"))
            report["restart_count"] = container["RestartCount"]
            if report["restart_count"]:
                raise RuntimeError("Fresh startup required a container restart")
            report["container_started_at"] = container["State"]["StartedAt"]
            report["container_start_to_health_seconds"] = round((ready_at-container_started).total_seconds(), 3)
            report["container_start_to_health_under_2min"] = report["container_start_to_health_seconds"] < 120
            runtime_image = json.loads(subprocess.check_output(["docker", "image", "inspect", container["Image"]], text=True))[0]
            report["image"] = {key: runtime_image[key] for key in ("Id", "Architecture", "Os", "Size")}
            report["image"]["user"] = runtime_image["Config"]["User"]
            check_environment = {**environment, "OKNO_URL": "http://localhost:8080", "OKNO_DEMO_REPORT": str(args.report.with_name(args.report.stem+"_demo.json"))}
            python = str(ROOT/".venv/bin/python")
            if args.check or args.browser or args.all_smokes:
                # Host tooling is attached only after Docker-only readiness.
                # No dependency from these links participates in the image.
                (args.clone / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
                (args.clone / 'frontend/node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
                report['qa_dependencies_reused_after_readiness'] = True
            if args.check:
                command(['make', 'check'], cwd=args.clone, env=check_environment, log=log)
                report['make_check'] = 'passed'
            else:
                command([python, "scripts/smoke.py"], cwd=args.clone, env=check_environment, log=log)
                command([python, "scripts/demo_check.py"], cwd=args.clone, env=check_environment, log=log)
            report['api_smoke'] = report['demo_check'] = 'passed'
            if args.browser:
                # Reuse only test-runner dependencies after product readiness;
                # execute the cloned script, not the development checkout.
                (args.clone/"demo/node_modules").symlink_to(ROOT/"demo/node_modules", target_is_directory=True)
                browser_environment = {**check_environment, "OKNO_BASE_URL": "http://localhost:8080", "OKNO_DEMO_OUTPUT": str(args.report.with_suffix(".mp4"))}
                command(["npm", "run", "--prefix", str(args.clone/"demo"), "preview"], cwd=args.clone, env=browser_environment, log=log)
                report["browser_preview"] = "passed"
                command(['npm', '--prefix', 'frontend', 'run', 'smoke'], cwd=args.clone, env=browser_environment, log=log)
                report['browser_smoke'] = 'passed'
            if args.all_smokes:
                browser_environment = {**check_environment, "OKNO_BASE_URL": "http://localhost:8080"}
                smokes = sorted((args.clone / "frontend/scripts").glob("*-smoke.mjs"))
                if not smokes:
                    raise RuntimeError("No browser smoke scripts found")
                report["browser_smokes"] = {}
                for smoke in smokes:
                    command(["node", str(smoke)], cwd=args.clone / "frontend", env=browser_environment, log=log)
                    report["browser_smokes"][smoke.name] = "passed"
            report["passed"] = True
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            compose.terminate()
            try:
                compose.wait(timeout=20)
            except subprocess.TimeoutExpired:
                compose.kill()
                compose.wait(timeout=5)
            report["free_disk_bytes_after_checks"] = shutil.disk_usage(ROOT).free
            report["elapsed_seconds"] = round(time.monotonic()-started, 3)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    if args.stop_current_app:
        command(["docker", "compose", "-p", args.current_project, "start", "app"])
    if args.cleanup:
        # Only the unique project created above is removed. Existing application
        # projects and their data volumes are never candidates for cleanup.
        command(["docker", "compose", "down", "-v", "--remove-orphans"], cwd=args.clone, env=environment)
        shutil.rmtree(args.clone)
        report["temporary_project_and_clone_removed"] = True
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    if cli_directory is not None:
        cli_directory.cleanup()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
