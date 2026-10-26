"""Local dependency audit. Reports are reviewable; scanner failure never passes."""
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_json(command, cwd):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=180)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Scanner failed: {command[0]} {command[1]} (exit {result.returncode})\n{result.stderr[-1000:]}") from error
    return result.returncode, report


def main():
    destination = ROOT / "docs" / "audits"
    destination.mkdir(parents=True, exist_ok=True)
    python_code, python_report = run_json([sys.executable, "-m", "pip_audit", "--require-hashes", "-r", "backend/requirements.lock", "--format", "json", "--progress-spinner", "off"], ROOT)
    npm_code, npm_report = run_json(["npm", "audit", "--omit=dev", "--json"], ROOT / "frontend")
    for name, report in (("python", python_report), ("npm", npm_report)):
        (destination / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    python_vulns = [{"name": dep["name"], "version": dep["version"], "vulns": dep["vulns"]}
                    for dep in python_report.get("dependencies", []) if dep.get("vulns")]
    npm_counts = npm_report.get("metadata", {}).get("vulnerabilities", {})
    npm_blocking = npm_counts.get("high", 0)+npm_counts.get("critical", 0)
    scanner_errors = bool(python_report.get("error") or npm_report.get("error") or python_code not in {0,1} or npm_code not in {0,1})
    summary = {"generated_at":datetime.now(UTC).isoformat(),"python_vulnerable_packages":len(python_vulns),
               "npm_production":npm_counts,"scanner_errors":scanner_errors,
               "passed":not (python_vulns or npm_blocking or scanner_errors),
               "policy":"Python: resolve every reported vulnerability; npm production: no High/Critical. Scanner failure blocks.",
               "commands":["pip-audit --require-hashes -r backend/requirements.lock -f json", "npm audit --omit=dev --json"]}
    (destination/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for item in python_vulns:
        print(item["name"], item["version"], [(v["id"], v.get("fix_versions", [])) for v in item["vulns"]])
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

