"""Standard-library release guards; read-only Git/filesystem, redacted findings.

Only the requested JSON report is written. No network, DB, gc, ref changes or
history rewriting. --history also scans reachable Git blobs and commit/tag text.
Pattern matches are candidates for review, not proof that a credential is real.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import warnings
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "checks" / "repository_audit.json"
LIMIT = 50_000_000
MANDATORY = ("README.md", "Dockerfile", "docker-compose.yml", ".env.example", "Makefile", ".gitignore", ".dockerignore")
BINARY = {".npz", ".gz", ".png", ".jpg", ".jpeg", ".ico", ".pbf", ".pmtiles", ".pdf", ".woff", ".woff2"}
PATTERNS = {
    "private_key_material": re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{70,255})\b"),
    "aws_access_key_id": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "openai_style_token": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,255}\b"),
    "slack_token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{24,255}\b"),
}
PREFIXES = {
    "private_key_material": (b"-----BEGIN ",),
    "github_token": (b"ghp_", b"gho_", b"ghu_", b"ghs_", b"ghr_", b"github_pat_"),
    "aws_access_key_id": (b"AKIA", b"ASIA"),
    "openai_style_token": (b"sk-",),
    "slack_token": (b"xoxb-", b"xoxa-", b"xoxp-", b"xoxr-", b"xoxs-"),
}
NAMES = rb"(?:[A-Za-z0-9]+_)*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET_KEY|PRIVATE_KEY|PASSWORD)"
QUOTED = re.compile(rb"(?i)(?<![\w])['\"]?("+NAMES+rb")[ '\"]*[:=][ \t]*(['\"])([^'\"\r\n]+)\2")
BARE = re.compile(rb"(?im)^[ \t]*(?:export[ \t]+)?("+NAMES+rb")[ \t]*[:=][ \t]*([^\s'\"#{}]+)[ \t]*(?:#.*)?$")
CONNECTION = re.compile(rb"\b(?:postgresql|postgres|mysql|mongodb)://([^:/\s]+):([^@\s]+)@([^/\s]+)")


def git(*args, input=None):
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(["git", *args], cwd=ROOT, env=env, input=input, capture_output=True, check=True).stdout


def placeholder(value):
    low = value.strip().lower()
    return (low in {b"test", b"testing", b"dummy", b"example", b"placeholder", b"changeme", b"local-test-only", b"unused"}
            or low.startswith((b"your_", b"your-", b"<", b"${", b"example-", b"test-", b"dummy-", b"synthetic-"))
            or b"EXAMPLE" in value)


def scan_secrets(data, path=""):
    """Return only category/count. Never return or log matched bytes."""
    potential, examples = Counter(), Counter()
    annotations = set()
    if path.endswith(".py"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                annotations = {node.lineno for node in ast.walk(ast.parse(data)) if isinstance(node, ast.AnnAssign) and node.value is None}
        except (SyntaxError, UnicodeError):
            pass
    for name, pattern in PATTERNS.items():
        if not any(prefix in data for prefix in PREFIXES[name]):
            continue
        for match in pattern.finditer(data):
            target = examples if placeholder(match.group()) else potential
            target[name] += 1
    upper = data.upper()
    assignment_patterns = (QUOTED, BARE) if any(name in upper for name in (b"API_KEY", b"ACCESS_TOKEN", b"AUTH_TOKEN", b"SECRET_KEY", b"PRIVATE_KEY", b"PASSWORD")) else ()
    for pattern in assignment_patterns:
        for match in pattern.finditer(data):
            if pattern is BARE and data.count(b"\n", 0, match.start())+1 in annotations:
                examples["type_annotation_not_credential"] += 1
                continue
            name, value = match.group(1).lower(), match.group(3 if pattern is QUOTED else 2)
            if not value or value.startswith((b"os.", b"self.", b"settings.", b"secrets.")):
                continue
            known_demo = name.endswith(b"password") and value.lower() in {b"okno", b"app", b"postgres", b"hotelier123", b"cascade-demo-2026", b"brevis-demo-2026"}
            target = examples if placeholder(value) or known_demo else potential
            target["literal_credential_assignment"] += 1
    for match in CONNECTION.finditer(data):
        user, password, host = match.groups()
        demo = user == password and user in {b"okno", b"app", b"postgres"} and host.split(b":")[0] in {b"db", b"localhost", b"127.0.0.1"}
        target = examples if demo or placeholder(password) else potential
        target["connection_uri_credentials"] += 1
    return dict(potential), dict(examples)


def forbidden_path(path):
    p = Path(path)
    name, parts = p.name.lower(), tuple(part.lower() for part in p.parts)
    reasons = []
    if name == ".env" or name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}:
        reasons.append("environment_file")
    if name in {"id_rsa", "id_ed25519", "id_dsa"} or p.suffix.lower() in {".key", ".p12", ".pfx", ".jks", ".keystore"}:
        reasons.append("private_key_or_credential_container_candidate")
    if name.endswith((".osm", ".osm.pbf", ".osm.pbf.gz", ".tif", ".tiff", ".parquet", ".parquet.tmp")):
        reasons.append("raw_geodata_download")
    if p.suffix.lower() == ".pbf" and not path.startswith("data/tiles/fonts/"):
        reasons.append("raw_pbf_outside_bundled_fonts")
    if path.startswith("data/raw/") or any(part in {"website_snapshots", "node_modules", ".cache", "__pycache__", ".venv", ".venv-pipeline", "downloads", "download_cache"} for part in parts):
        reasons.append("raw_or_download_cache")
    if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi"}:
        reasons.append("video_binary")
    if path.startswith("data/") and p.suffix.lower() in {".html", ".htm"}:
        reasons.append("downloaded_html")
    return reasons


def directory_bytes(path):
    total = 0
    for current, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(current) / name).lstat().st_size
            except FileNotFoundError:
                pass  # Another read-only Git process may finish its temporary file.
    return total


def text_blob(path, data):
    return Path(path).suffix.lower() not in BINARY and b"\0" not in data[:8192]


def history_audit():
    objects = {}
    for line in git("-c", "core.quotePath=false", "rev-list", "--objects", "--all").decode(errors="replace").splitlines():
        oid, _, path = line.partition(" ")
        objects.setdefault(oid, path)
    metadata = git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", input="".join(oid+"\n" for oid in objects).encode())
    selected, large, kinds, binary_blobs = [], [], Counter(), 0
    for line in metadata.decode().splitlines():
        oid, kind, size = line.split()
        kinds[kind] += 1
        size = int(size)
        path = objects[oid] or "git:"+kind
        if kind == "blob":
            if size > LIMIT:
                large.append({"path": path, "bytes": size})
            if Path(path).suffix.lower() in BINARY:
                binary_blobs += 1
        if kind in {"commit", "tag"} or kind == "blob" and Path(path).suffix.lower() not in BINARY:
            selected.append((oid, kind, size, path))
    findings, examples, scanned, binary_skipped = [], [], 0, 0
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for oid, kind, size, path in selected:
            process.stdin.write((oid+"\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            if len(header) != 3 or header[:2] != [oid, kind] or int(header[2]) != size:
                raise RuntimeError("Git history object could not be read")
            data = process.stdout.read(size)
            if process.stdout.read(1) != b"\n":
                raise RuntimeError("Incomplete Git history object")
            if not text_blob(path, data):
                binary_skipped += 1
                continue
            potential, known = scan_secrets(data, path)
            scanned += 1
            if potential:
                findings.append({"path": path, "counts": potential})
            if known:
                examples.append({"path": path, "counts": known})
        process.stdin.close()
        if process.wait(timeout=30) != 0:
            raise RuntimeError("Git history scanner failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    historical_paths = sorted(set(filter(None, git("log", "--all", "--format=", "--name-only", "-z").decode(errors="replace").split("\0"))))
    forbidden = [{"path": path, "reasons": forbidden_path(path)} for path in historical_paths if forbidden_path(path)]
    return {"enabled": True, "reachable_objects": len(objects), "object_types": dict(kinds), "text_objects_scanned": scanned,
            "binary_blobs_not_secret_scanned": binary_blobs+binary_skipped, "historical_path_count": len(historical_paths),
            "potential_secret_matches": findings, "recognized_examples": examples,
            "historical_files_over_50mb": large, "historical_sensitive_or_raw_paths": forbidden}


def workflow_events(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^[\"']?on[\"']?:\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match[1].split("#", 1)[0].strip()
        if value:
            return re.findall(r"[a-z_]+", value)
        block = []
        for following in lines[index+1:]:
            if following and not following[0].isspace():
                break
            if following.strip() and not following.lstrip().startswith("#"):
                block.append(following)
        indent = min((len(row)-len(row.lstrip()) for row in block), default=0)
        return [m[1] for row in block if (m := re.match(r"^ {"+str(indent)+r"}([a-z_]+):", row))]
    return []


def service_block(text, service):
    match = re.search(r"^  "+re.escape(service)+r":\s*\n(.*?)(?=^  [A-Za-z_]|^\S|\Z)", text, re.M | re.S)
    return match[1] if match else ""


def pinned_lock(path):
    text = path.read_text()
    blocks = re.split(r"(?m)(?=^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^]]+\])?==)", text)
    packages = [block for block in blocks if re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^]]+\])?==", block)]
    bad_lines = [index for index, line in enumerate(text.splitlines(), 1) if line and not line[0].isspace() and not line.startswith(("#", "--"))
                 and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^]]+\])?==[^\s;]+", line)]
    return {"path": str(path.relative_to(ROOT)), "packages": len(packages), "unpinned_requirement_line_numbers": bad_lines,
            "packages_without_sha256": sum(not re.search(r"--hash=sha256:[a-f0-9]{64}", block) for block in packages)}


def npm_integrity_covered(name, package, packages):
    if package.get("integrity"):
        return True
    if not package.get("inBundle"):
        return False
    # Bundled files are covered by the enclosing package's locked tarball hash.
    while "/node_modules/" in name:
        name = name.rsplit("/node_modules/", 1)[0]
        parent = packages.get(name, {})
        if parent.get("version") and parent.get("integrity"):
            return True
        if not parent.get("inBundle"):
            return False
    return False


def audit(include_history=False):
    head = git("rev-parse", "HEAD").decode().strip()
    entries = git("ls-files", "--stage", "-z").decode().split("\0")
    files = [entry.split("\t", 1)[1] for entry in entries if entry]
    checks, findings, examples, sizes, forbidden, missing = {}, [], [], [], [], []
    for path in files:
        item = ROOT / path
        if not item.exists() and not item.is_symlink():
            missing.append(path)
            continue
        sizes.append({"path": path, "bytes": item.lstat().st_size})
        reasons = forbidden_path(path)
        if reasons:
            forbidden.append({"path": path, "reasons": reasons})
        data = os.readlink(item).encode() if item.is_symlink() else item.read_bytes()
        if text_blob(path, data):
            potential, known = scan_secrets(data, path)
            if potential:
                findings.append({"path": path, "counts": potential})
            if known:
                examples.append({"path": path, "counts": known})
    checks["mandatory_root_files"] = {"passed": all(path in files for path in MANDATORY), "missing": sorted(set(MANDATORY)-set(files))}
    checks["tracked_files_readable"] = {"passed": not missing, "missing_paths": missing}
    checks["tracked_sensitive_or_raw_files"] = {"passed": not forbidden, "findings": forbidden}
    oversized = [item for item in sizes if item["bytes"] > LIMIT]
    checks["tracked_file_size_limit"] = {"passed": not oversized, "limit_bytes": LIMIT, "findings": oversized}
    checks["tracked_secret_patterns"] = {"passed": not findings, "potential_matches": findings, "recognized_examples": examples,
                                         "interpretation": "Candidates are redacted; a pattern alone does not prove a credential is real."}
    compose = (ROOT / "docker-compose.yml").read_text()
    app, db = service_block(compose, "app"), service_block(compose, "db")
    docker = (ROOT / "Dockerfile").read_text()
    runtime = re.split(r"(?m)^FROM ", docker)[-1]
    users = re.findall(r"(?m)^USER\s+([^\n]+)", runtime)
    nonroot = bool(users) and users[-1].split(":")[0].strip() not in {"0", "root"}
    dockerfiles = []
    images = []
    for path in files:
        if not Path(path).name.startswith("Dockerfile"):
            continue
        content = (ROOT / path).read_text()
        final_stage = re.split(r"(?m)^FROM ", content)[-1]
        final_users = re.findall(r"(?m)^USER\s+([^\n]+)", final_stage)
        dockerfiles.append({"path": path, "runtime_user": final_users[-1] if final_users else None,
                            "nonroot": bool(final_users) and final_users[-1].split(":")[0].strip() not in {"0", "root"}})
        images.extend(re.findall(r"(?m)^FROM\s+(?:--platform=\S+\s+)?(\S+)", content))
    images += re.findall(r"(?m)^\s+image:\s*(\S+)", compose)
    checks["container_launch"] = {"passed": all((bool(app), bool(db), app.count("platform: linux/amd64") == 1,
        db.count("platform: linux/amd64") == 1, bool(re.search(r'[\"\']8080:8080[\"\']', app)), "EXPOSE 8080" in runtime,
        nonroot, "service_healthy" in app, "healthcheck:" in app, "healthcheck:" in db, "/health" in app,
        len(re.findall(r"(?m)^FROM ", docker)) >= 2, "--require-hashes" in docker)),
        "runtime_user": users[-1] if users else None, "platform": "linux/amd64", "port": 8080,
        "image_references": images,
        "images_have_version_tags": all(re.search(r"(?:^|/)[^/:]+:v?\d[^\s@]*(?:@sha256:[a-f0-9]{64})?$", image) is not None for image in images),
        "dockerfiles": dockerfiles}
    checks["container_launch"]["passed"] &= checks["container_launch"]["images_have_version_tags"]
    checks["container_launch"]["passed"] &= all(item["nonroot"] for item in dockerfiles)
    overrides = (ROOT / "docker-compose.override.yml").read_text() if (ROOT / "docker-compose.override.yml").exists() else ""
    checks["no_root_user_override"] = {"passed": not re.search(r"(?m)^\s+user:\s*[\"']?(?:root|0)(?::|[\"']?\s*$)", service_block(overrides, "app"))}
    tree = ast.parse((ROOT / "backend" / "main.py").read_text())
    health = any(isinstance(node, ast.FunctionDef) and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                 and d.func.attr == "get" and d.args and isinstance(d.args[0], ast.Constant) and d.args[0].value == "/health"
                 for d in node.decorator_list) for node in ast.walk(tree))
    boot = ast.parse((ROOT / "backend" / "boot.py").read_text())
    port = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
               and any(k.arg == "port" and isinstance(k.value, ast.Constant) and k.value.value == 8080 for k in node.keywords)
               for node in ast.walk(boot))
    checks["health_route_and_app_port"] = {"passed": health and port, "health_route_declared": health, "uvicorn_port_8080": port}
    workflows = [{"path": p, "events": workflow_events((ROOT / p).read_text())} for p in files if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]
    checks["manual_only_workflows"] = {"passed": bool(workflows) and all(w["events"] == ["workflow_dispatch"] for w in workflows), "workflows": workflows}
    locks = [pinned_lock(ROOT / path) for path in ("backend/requirements.lock", "requirements-dev.lock", "pipeline/requirements.lock", "pipeline/rebuild.lock")]
    checks["python_hash_locks"] = {"passed": all(lock["packages"] and not lock["unpinned_requirement_line_numbers"] and not lock["packages_without_sha256"] for lock in locks), "locks": locks}
    npm = []
    for path in ("frontend/package-lock.json", "demo/package-lock.json"):
        document = json.loads((ROOT / path).read_text())
        all_packages = document.get("packages", {})
        packages = {name: p for name, p in all_packages.items() if name and not p.get("link")}
        missing_integrity = sum(not p.get("version") or not npm_integrity_covered(name, p, all_packages) for name, p in packages.items())
        npm.append({"path": path, "lockfile_version": document.get("lockfileVersion"), "packages": len(packages),
                    "bundled_packages_covered_by_parent_hash": sum(bool(p.get("inBundle")) and npm_integrity_covered(name, p, all_packages) for name, p in packages.items()),
                    "missing_version_or_integrity": missing_integrity})
    checks["npm_locks"] = {"passed": all(lock["lockfile_version"] and lock["packages"] and not lock["missing_version_or_integrity"] for lock in npm), "locks": npm}
    history = history_audit() if include_history else {"enabled": False}
    if include_history:
        checks["history_secret_patterns"] = {"passed": not history["potential_secret_matches"], "potential_matches": history["potential_secret_matches"]}
    common = Path(git("rev-parse", "--git-common-dir").decode().strip())
    common = common if common.is_absolute() else ROOT / common
    counts = {line.split(": ", 1)[0]: int(line.split(": ", 1)[1]) for line in git("count-objects", "-v").decode().splitlines()}
    sources = json.loads((ROOT / "data" / "seed" / "sources.json").read_text())
    license_files = sorted(path for path in files if "/" not in path and Path(path).name.upper().startswith(("LICENSE", "COPYING")))
    return {"schema_version": 1, "checked_at": datetime.now(UTC).isoformat(), "git_head": head,
            "passed": all(check["passed"] for check in checks.values()), "checks": checks,
            "tracked_files": len(files), "tracked_worktree_total_bytes": sum(s["bytes"] for s in sizes),
            "largest_tracked_files": sorted(sizes, key=lambda row: row["bytes"], reverse=True)[:10],
            "git_storage": {"directory_bytes": directory_bytes(common), "count_objects": counts,
                            "pack_bytes": counts["size-pack"]*1024, "loose_allocated_bytes": counts["size"]*1024,
                            "target_pack_bytes": 150_000_000, "target_pack_met": counts["size-pack"]*1024 < 150_000_000,
                            "interpretation": "Pack target is advisory; owner decides compaction. No gc, purge or rewrite performed."},
            "history": history, "licensing_inventory": {"project_license_files": license_files,
                "source_registry_records": len(sources), "nonempty_source_license_fields": sum(bool(s.get("license")) for s in sources),
                "manifest_present": (ROOT / "data" / "seed" / "manifest.json").is_file(),
                "bundled_asset_license_files": sorted(p for p in files if p.startswith("data/") and "LICENSE" in Path(p).name),
                "interpretation": "Presence inventory only; no legal verdict. Project license choice remains with the owner."},
            "limits": ["Static repository guards do not replace clean-clone or live-container checks.",
                       "Secret patterns are heuristic, not proof of absence; binary payloads are not secret-scanned.",
                       "Tracked content is read from the current worktree; git_head identifies the starting commit.",
                       "Historical large/raw files are reported separately; refs, origin and history are unchanged.",
                       "Version tags are checked; immutable image digests and external license terms are not verified."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--stdout", action="store_true", help="Print redacted report without writing")
    args = parser.parse_args()
    try:
        report = audit(args.history)
    except Exception as error:
        report = {"passed": False, "scanner_error": type(error).__name__, "interpretation": "Audit did not complete; no success is claimed."}
    if args.stdout:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps({"passed": report["passed"], "report": str(REPORT.relative_to(ROOT)),
                          "failed_checks": [name for name, check in report.get("checks", {}).items() if not check["passed"]],
                          "scanner_error": report.get("scanner_error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
