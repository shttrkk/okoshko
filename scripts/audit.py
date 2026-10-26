"""Local dependency audit. Reports are reviewable; scanner failure never passes."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shlex import join

ROOT = Path(__file__).resolve().parents[1]


def run_json(command, cwd):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=180)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Scanner failed: {join(command)} (exit {result.returncode})\n{result.stderr[-1000:]}") from error
    if not isinstance(report, dict):
        raise RuntimeError(f"Scanner failed: {command[0]} returned a non-object JSON report")
    return result.returncode, report


def python_findings(code, report):
    dependencies = report.get("dependencies")
    if report.get("error") or code not in {0, 1} or not isinstance(dependencies, list) or not dependencies:
        raise RuntimeError(f"Incomplete Python scanner report (exit {code})")
    findings = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("vulns"), list) or dependency.get("skip_reason"):
            raise RuntimeError("Python scanner skipped a dependency or returned an invalid dependency report")
        if dependency["vulns"]:
            findings.append({key: dependency[key] for key in ("name", "version", "vulns")})
    if (code == 1) != bool(findings):
        raise RuntimeError(f"Python scanner exit {code} disagrees with its findings")
    return findings


def npm_findings(code, report):
    metadata = report.get("metadata")
    counts = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    severities = ("info", "low", "moderate", "high", "critical", "total")
    if report.get("error") or code not in {0, 1} or not isinstance(counts, dict):
        raise RuntimeError(f"Incomplete npm scanner report (exit {code})")
    if any(type(counts.get(key)) is not int or counts[key] < 0 for key in severities):
        raise RuntimeError("npm scanner report is missing severity counts")
    if (code == 1) != bool(counts["total"]):
        raise RuntimeError(f"npm scanner exit {code} disagrees with its findings")
    return counts


def scan(name, command, cwd, kind):
    try:
        code, report = run_json(command, cwd)
        findings = python_findings(code, report) if kind == "python" else npm_findings(code, report)
        return {"name": name, "exit_code": code, "report": report, "findings": findings, "error": None}
    except (OSError, subprocess.TimeoutExpired, RuntimeError, KeyError, TypeError) as error:
        message = str(error)[:1500]
        return {"name": name, "exit_code": None, "report": {"error": message}, "findings": None, "error": message}


def main():
    destination = ROOT / "docs" / "audits"
    destination.mkdir(parents=True, exist_ok=True)
    locks = {"python": "backend/requirements.lock", "python-dev": "requirements-dev.lock",
             "python-pipeline": "pipeline/requirements.lock"}
    inputs = {name: ROOT / path for name, path in locks.items()}
    inputs["npm"] = ROOT / "frontend" / "package-lock.json"
    input_hashes = {name: sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()}
    scans = [(name, [sys.executable, "-m", "pip_audit", "--require-hashes", "--disable-pip", "--strict",
                     "-r", path, "--format", "json", "--progress-spinner", "off"], ROOT, "python")
             for name, path in locks.items()]
    scans.extend([("npm", ["npm", "audit", "--omit=dev", "--json"], ROOT / "frontend", "npm"),
                  ("npm-full", ["npm", "audit", "--json"], ROOT / "frontend", "npm")])
    # All scanners are read-only and independent. Run every scan even if another fails.
    with ThreadPoolExecutor(max_workers=len(scans)) as executor:
        results = list(executor.map(lambda args: scan(*args), scans))
    by_name = {result["name"]: result for result in results}
    for result in results:
        (destination / f"{result['name']}.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=2)+"\n")
    errors = {result["name"]: result["error"] for result in results if result["error"]}
    for name, path in inputs.items():
        if sha256(path.read_bytes()).hexdigest() != input_hashes[name]:
            errors[name] = "Lock file changed while its audit was running; rerun the audit"
    python_counts = {name: len(by_name[name]["findings"]) if not by_name[name]["error"] else None for name in locks}
    python_vulns = [{"lock": locks[name], **finding} for name in locks for finding in by_name[name]["findings"] or []]
    npm_counts = {name: by_name[name]["findings"] for name in ("npm", "npm-full")}
    npm_blocking = sum((counts or {}).get("high", 0)+(counts or {}).get("critical", 0) for counts in npm_counts.values())
    summary = {"generated_at": datetime.now(UTC).isoformat(), "python_vulnerable_packages": len(python_vulns),
               "python_locks": {name: {"path": path, "sha256": input_hashes[name],
                                        "vulnerable_packages": python_counts[name]} for name, path in locks.items()},
               "npm_production": npm_counts["npm"], "npm_full": npm_counts["npm-full"],
               "npm_lock_sha256": input_hashes["npm"],
               "scanner_errors": bool(errors), "errors": errors,
               "exit_codes": {result["name"]: result["exit_code"] for result in results},
               "passed": not (python_vulns or npm_blocking or errors),
               "policy": "Python runtime/dev/pipeline: resolve every reported vulnerability; npm full/production: no High/Critical. Scanner failure or skipped dependency blocks.",
               "commands": [join(command) for _, command, _, _ in scans]}
    (destination/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for item in python_vulns:
        print(item["lock"], item["name"], item["version"], [(v["id"], v.get("fix_versions", [])) for v in item["vulns"]])
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

