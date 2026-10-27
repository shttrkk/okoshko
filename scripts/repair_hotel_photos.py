"""Repair reviewed photo links from existing caches; HTTPS upgrades require identical bytes.

Dry-run is the default. Example (official originals are never written)::

    python -m scripts.repair_hotel_photos --cache-dir data/raw/hotel_photos \
      --cache-dir data/raw/hotel_photos_extended --verify-https --apply --report /tmp/photo-repair.json

Verification-cache JSON makes a repeated offline repair reproducible. Missing verification,
changed bytes, blocked robots, foreign redirects and oversized images remove the entry.
Room approval never becomes cover approval; original visual-review batches remain historical.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from scripts.collect_hotel_photos import (
    MAX_EXTERNAL_BYTES,
    ROOT,
    USER_AGENT,
    Net,
    denied,
    external_photo_issue,
    image_host_ok,
    key_of,
    normalize_site,
    site_of,
)


def cached_metadata(cache_dirs, *, urls=None):
    """Read old original caches and newer metadata-only caches without changing either."""
    from PIL import Image

    found = {}
    for directory in cache_dirs:
        for path in sorted((directory / "site_images").glob("*.json")):
            record = json.loads(path.read_text())
            if urls is not None and not ({record.get("url"), record.get("final_url")} & urls):
                continue
            if record.get("status") != 200 or not record.get("content_type", "").startswith("image/"):
                continue
            probe = record.get("probe", {})
            metadata = {k: probe[k] for k in ("width", "height", "bytes", "sha256") if k in probe}
            if record.get("body"):
                body = path.parent / record["body"]
                if not body.is_file():
                    continue
                payload = body.read_bytes()
                try:
                    width, height = Image.open(io.BytesIO(payload)).size
                except (OSError, ValueError):
                    continue
                actual = {"width": width, "height": height, "bytes": len(payload),
                          "sha256": hashlib.sha256(payload).hexdigest()}
                if any(metadata[k] != value for k, value in actual.items() if k in metadata):
                    raise ValueError("Cached probe differs from original: " + str(path))
                metadata = actual
            if set(metadata) != {"width", "height", "bytes", "sha256"}:
                continue
            if record.get("bytes") is not None and record["bytes"] != metadata["bytes"]:
                raise ValueError("Cached byte count differs from original: " + str(path))
            for url in {record.get("url"), record.get("final_url")} - {None}:
                if url in found and found[url] != metadata:
                    raise ValueError("Conflicting cached image metadata: " + url)
                found[url] = metadata
    return found


class HTTPSVerifier:
    """Robots and domain checked, paced streaming GETs; keep metadata, never originals."""

    def __init__(self, *, client=None, verification_cache=None, online=False):
        self.client = client or httpx.Client(timeout=httpx.Timeout(12), headers={"User-Agent": USER_AGENT})
        self.online, self.cache = online, verification_cache
        self.pacer = Net(Path("."), True, False)
        self.robots, self.pages, self.requests = {}, {}, []
        self.robot_errors, self.page_errors = {}, {}

    def _allowed(self, url, website):
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or denied(url) or not image_host_ok(url, website)):
            raise ValueError("insecure or foreign redirect")

    def _request(self, url, website, role, limit, *, robots=False):
        for _ in range(5):
            self._allowed(url, website)
            if not robots:
                self._check_robots(url, website)
            self.pacer.site_domain = site_of(urlsplit(website).hostname)
            self.pacer._pace(url)
            audit = {"url": url, "role": role}
            self.requests.append(audit)
            try:
                with self.client.stream("GET", url, follow_redirects=False) as response:
                    audit["status"] = response.status_code
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        self._allowed(url, website)
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > limit:
                        raise ValueError("response exceeds byte budget")
                    payload = bytearray()
                    for chunk in response.iter_bytes():
                        payload.extend(chunk)
                        if len(payload) > limit:
                            raise ValueError("response exceeds byte budget")
                    audit["bytes"] = len(payload)
                    return bytes(payload), str(response.url), response.headers.get("content-type", "")
            except (httpx.HTTPError, ValueError) as error:
                audit["error"] = type(error).__name__ + ": " + str(error)[:200]
                raise
        raise ValueError("too many redirects")

    def _check_robots(self, url, website):
        parts = urlsplit(url)
        origin = parts.scheme + "://" + parts.netloc
        if origin in self.robot_errors:
            raise ValueError(self.robot_errors[origin])
        if origin not in self.robots:
            policy = RobotFileParser()
            try:
                payload, _, _ = self._request(origin + "/robots.txt", website, "robots", 256_000, robots=True)
                policy.parse(payload.decode("utf-8", errors="replace").splitlines())
            except (httpx.HTTPError, ValueError) as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {404, 410}:
                    policy.parse([])
                else:
                    self.robot_errors[origin] = "robots policy unavailable: " + type(error).__name__
                    raise ValueError(self.robot_errors[origin]) from error
            self.robots[origin] = policy
        if not self.robots[origin].can_fetch(USER_AGENT, url):
            raise ValueError("robots policy forbids URL")

    def verify(self, original, page_url, metadata, website):
        secure = urlsplit(original)._replace(scheme="https").geturl()
        secure_page = urlsplit(page_url)._replace(scheme="https").geturl()
        self._allowed(secure, website)
        self._allowed(secure_page, website)
        path = self.cache / (key_of(original, page_url) + ".json") if self.cache else None
        record = json.loads(path.read_text()) if path and path.exists() else None
        if record is None:
            if not self.online:
                raise ValueError("HTTPS identity has not been verified")
            if secure_page in self.page_errors:
                raise ValueError(self.page_errors[secure_page])
            if secure_page not in self.pages:
                try:
                    _, final_page, ctype = self._request(secure_page, website, "source_page", 2_000_000)
                    if "html" not in ctype.lower():
                        raise ValueError("source page is not HTML")
                except (httpx.HTTPError, ValueError) as error:
                    self.page_errors[secure_page] = "source page unavailable: " + str(error)[:200]
                    raise
                self.pages[secure_page] = final_page
            payload, final, ctype = self._request(secure, website, "image", MAX_EXTERNAL_BYTES)
            if not ctype.lower().startswith("image/") or any(kind in ctype.lower() for kind in ("svg", "gif")):
                raise ValueError("HTTPS file is not a supported image")
            record = {"original_url": original, "url": final, "page_url": self.pages[secure_page],
                      "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                      "verified_at": datetime.now(UTC).isoformat()}
        self._allowed(record["url"], website)
        self._allowed(record["page_url"], website)
        if record.get("original_url") != original or any(record.get(k) != metadata[k] for k in ("bytes", "sha256")):
            raise ValueError("HTTPS bytes or SHA-256 differ from the reviewed file")
        if path and self.online and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        return record


def recount(index, hotels):
    rank = {"building": 0, "official_site": 1, "street": 2, "room": 3}
    index["photos"] = {hid: sorted(rows, key=lambda r: rank[r["kind"]])
                       for hid, rows in sorted(index["photos"].items()) if rows}
    best = [next((r["kind"] for r in rows if r["kind"] != "room"), "none") for rows in index["photos"].values()]
    index["counts"] = {kind: best.count(kind) for kind in ("building", "official_site", "street")}
    index["counts"]["none"] = len(hotels) - sum(index["counts"].values())
    index["room_hotel_count"] = sum(any(r["kind"] == "room" for r in rows) for rows in index["photos"].values())
    index["room_photo_count"] = sum(r["kind"] == "room" for rows in index["photos"].values() for r in rows)


def repair(index, review, hotels, metadata, verifier):
    index, review = copy.deepcopy(index), copy.deepcopy(review)
    websites = {h["id"]: normalize_site(h.get("website")) for h in hotels}
    report = {"before": {k: index[k] for k in ("counts", "room_hotel_count", "room_photo_count")},
              "metadata_restored": [], "removed": [], "upgraded": []}
    for hotel_id, rows in list(index["photos"].items()):
        retained = []
        for entry in rows:
            if entry["kind"] not in {"official_site", "room"}:
                retained.append(entry)
                continue
            original, kind = entry["url"], entry["kind"]
            approved = review["accept"].get(hotel_id, {})
            reason = None
            if not (approved.get("official_site") == original if kind == "official_site" else original in approved.get("room", [])):
                reason = "no exact visual approval for this photo kind"
            cached = metadata.get(original)
            if cached:
                if any(entry.get(k) is not None and entry[k] != cached[k] for k in ("bytes", "sha256", "width", "height")):
                    reason = "cached bytes, digest or dimensions differ from the reviewed index"
                else:
                    if not entry.get("bytes") or not entry.get("sha256"):
                        report["metadata_restored"].append({"hotel_id": hotel_id, "url": original, **cached})
                    entry.update(cached)
            if type(entry.get("bytes")) is not int or entry["bytes"] <= 0:
                reason = "exact image byte size is unavailable"
            elif entry["bytes"] > MAX_EXTERNAL_BYTES:
                reason = "external image exceeds 2,000,000 bytes"
            if not websites.get(hotel_id):
                reason = "missing seed website domain"
            upgrade = None
            if not reason and (urlsplit(original).scheme == "http" or urlsplit(entry["page_url"]).scheme == "http"):
                try:
                    upgrade = verifier.verify(original, entry["page_url"], entry, websites[hotel_id])
                    entry.update({k: upgrade[k] for k in ("url", "page_url")})
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    reason = type(error).__name__ + ": " + str(error)[:240]
            reason = reason or external_photo_issue(entry, websites.get(hotel_id))
            pins = review.setdefault("image_pages", {}).setdefault(hotel_id, {})
            if reason:
                removed = {"hotel_id": hotel_id, "kind": kind, "ref": original, "reason": reason,
                           "bytes": entry.get("bytes"), "sha256": entry.get("sha256")}
                report["removed"].append(removed)
                if not any(r["hotel_id"] == hotel_id and r["kind"] == kind and r["ref"] == original for r in review["reject"]):
                    review["reject"].append(removed)
                if kind == "official_site":
                    approved.pop("official_site", None)
                else:
                    approved["room"] = [url for url in approved.get("room", []) if url != original]
                pins.pop(original, None)
                continue
            if upgrade:
                change = {"hotel_id": hotel_id, "kind": kind, **upgrade}
                report["upgraded"].append(change)
                review.setdefault("https_upgrades", []).append(change)
                if kind == "official_site":
                    approved["official_site"] = entry["url"]
                else:
                    approved["room"] = [entry["url"] if url == original else url for url in approved["room"]]
                pins.pop(original, None)
                pins[entry["url"]] = entry["page_url"]
            retained.append(entry)
        index["photos"][hotel_id] = retained
    recount(index, hotels)
    report["after"] = {k: index[k] for k in ("counts", "room_hotel_count", "room_photo_count")}
    report["requests"] = verifier.requests
    report["get_count"] = len(verifier.requests)
    report["robots_get_count"] = sum(r["role"] == "robots" for r in verifier.requests)
    if report["metadata_restored"] or report["removed"] or report["upgraded"]:
        index["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return index, review, report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", type=Path, default=ROOT / "data/seed/hotel_photos.json")
    parser.add_argument("--review", type=Path, default=ROOT / "data/eval/hotel_photos_review.json")
    parser.add_argument("--hotels", type=Path, default=ROOT / "data/seed/hotels.json")
    parser.add_argument("--cache-dir", type=Path, action="append", default=[])
    parser.add_argument("--verification-cache", type=Path, default=ROOT / "data/raw/hotel_photos_repair/https")
    parser.add_argument("--verify-https", action="store_true", help="Allow paced, robots-checked HTTPS verification GETs")
    parser.add_argument("--apply", action="store_true", help="Write the repaired index and exact review pins")
    parser.add_argument("--report", type=Path, help="Write the metadata-only audit report")
    args = parser.parse_args()
    verifier = HTTPSVerifier(verification_cache=args.verification_cache, online=args.verify_https)
    try:
        original_index = json.loads(args.index.read_text())
        urls = {r["url"] for rows in original_index["photos"].values() for r in rows if not r["local"]}
        index, review, report = repair(original_index, json.loads(args.review.read_text()),
                                       json.loads(args.hotels.read_text()), cached_metadata(args.cache_dir, urls=urls), verifier)
    finally:
        verifier.client.close()
    if args.apply:
        args.index.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n")
        args.review.write_text(json.dumps(review, ensure_ascii=False, indent=1) + "\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in report.items() if k != "requests"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
