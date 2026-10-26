"""Measure the documented launch from a local clean clone and empty build cache.

Run after recording: the existing app is briefly stopped to release fixed8080.
Data volumes are retained. Test containers and clone remain for inspection.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def command(args, *, cwd=ROOT, env=None, log=None):
    return subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT if log else None, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--prune", action="store_true", help="Run the requested docker builder prune -af")
    parser.add_argument("--stop-current-app", action="store_true")
    parser.add_argument("--browser", action="store_true", help="Run the real UI preview with the installed demo tooling")
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
    command(["git", "clone", "--no-local", str(ROOT), str(args.clone)])
    if not args.without_env:
        shutil.copyfile(args.clone/".env.example", args.clone/".env")
    environment = {**os.environ, "COMPOSE_PROJECT_NAME": "okno-clean", "LLM_MODE": "offline"}
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
        command(["docker", "compose", "stop", "app"])
    report = {"revision": revision, "clone": str(args.clone), "checked_at": datetime.now(UTC).isoformat(),
              "command": "docker compose up --build", "project": "okno-clean", "pruned_build_cache": args.prune, "passed": False,
              "env_file_created": not args.without_env, "docker_only_launch_path": args.docker_only_path,
              "launch_path_has_python": bool(shutil.which("python3", path=launch_environment["PATH"])),
              "launch_path_has_node": bool(shutil.which("node", path=launch_environment["PATH"]))}
    with log_path.open("w") as log:
        prepared = time.monotonic()
        if args.prune:
            command(["docker", "builder", "prune", "-af"], log=log)
        report["prune_seconds"] = round(time.monotonic()-prepared, 3)
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
            check_environment = {**environment, "OKNO_URL": "http://localhost:8080", "OKNO_DEMO_REPORT": str(args.report.with_name(args.report.stem+"_demo.json"))}
            python = str(ROOT/".venv/bin/python")
            command([python, "scripts/smoke.py"], cwd=args.clone, env=check_environment, log=log)
            command([python, "scripts/demo_check.py"], cwd=args.clone, env=check_environment, log=log)
            if args.browser:
                # Reuse only test-runner dependencies after product readiness;
                # execute the cloned script, not the development checkout.
                (args.clone/"demo/node_modules").symlink_to(ROOT/"demo/node_modules", target_is_directory=True)
                browser_environment = {**check_environment, "OKNO_BASE_URL": "http://localhost:8080", "OKNO_DEMO_OUTPUT": str(args.report.with_suffix(".mp4"))}
                command(["npm", "run", "--prefix", str(args.clone/"demo"), "preview"], cwd=args.clone, env=browser_environment, log=log)
                report["browser_preview"] = "passed"
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
            report["elapsed_seconds"] = round(time.monotonic()-started, 3)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    if args.stop_current_app:
        command(["docker", "compose", "start", "app"])
    if cli_directory is not None:
        cli_directory.cleanup()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
