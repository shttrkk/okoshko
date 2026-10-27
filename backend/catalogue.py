"""Local, reviewed eligibility of hotel identities, independent of hotel claims.

An expired review still requires review. A missing or malformed ledger is a
configuration error on cold start; after a successful load the last valid
snapshot remains in force and a warning is emitted until the file is repaired.
"""
from __future__ import annotations

import json
import logging
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import overload
from urllib.parse import urlsplit

from backend.contracts import Hotel
from backend.settings import DATA

SCHEMA = "hotel_identity_reviews_v1"
METHOD = "primary_sources_and_geometry_identity_check_v1"
EVIDENCE_URL = "/data/seed/catalogue_reviews.json"
HOLD = "hold_for_identity_review"
MAX_BYTES = 1024 * 1024
_LOG = logging.getLogger(__name__)
_LOCK = RLock()


class CatalogueReviewError(RuntimeError):
    """The required local catalogue review ledger cannot be validated."""


@dataclass
class _Snapshot:
    signature: tuple
    reviews: dict[str, dict]


_CACHE: dict[Path, _Snapshot] = {}


def _signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except OSError as exc:
        return "unreadable", type(exc).__name__, exc.errno


def _object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc_date(value) -> datetime:
    if not _text(value):
        raise ValueError("missing review date")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("review dates must be UTC with timezone")
    return parsed


def _web_url(value) -> bool:
    if not _text(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username


def _validate(document) -> dict[str, dict]:
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError("unsupported catalogue review schema")
    if document.get("policy_applied") is not True or document.get("method") != METHOD:
        raise ValueError("invalid catalogue review policy")
    if not _text(document.get("purpose_ru")):
        raise ValueError("missing catalogue review purpose")
    _utc_date(document.get("generated_at"))
    rows = document.get("reviews")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("invalid review list")
    reviews = {}
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("hotel_id")):
            raise ValueError("invalid review row")
        hotel_id = row["hotel_id"]
        if hotel_id != hotel_id.strip() or hotel_id in reviews:
            raise ValueError("duplicate or noncanonical hotel ID")
        if row.get("disposition") != HOLD or row.get("method") != METHOD:
            raise ValueError("unsupported review disposition or method")
        if not _text(row.get("reason_ru")) or not _text(row.get("source")):
            raise ValueError("missing review provenance")
        confidence = row.get("confidence")
        if not isinstance(confidence, dict) or confidence.get("kind") != "expert_uncalibrated":
            raise ValueError("review confidence must be explicitly expert and uncalibrated")
        value = confidence.get("value")
        if (isinstance(value, bool) or not isinstance(value, int | float)
                or not math.isfinite(value) or not 0 <= value <= 1 or not _text(confidence.get("meaning_ru"))):
            raise ValueError("invalid expert confidence")
        observed, until = _utc_date(row.get("observed_at")), _utc_date(row.get("valid_until"))
        if until < observed:
            raise ValueError("review validity precedes observation")
        evidence = row.get("evidence")
        if (not isinstance(evidence, list) or not evidence
                or not all(isinstance(item, dict) and _text(item.get("kind")) for item in evidence)
                or not any(_web_url(item.get("url")) for item in evidence)):
            raise ValueError("missing primary review evidence")
        for item in evidence:
            if item.get("url") is not None and not _web_url(item["url"]):
                raise ValueError("invalid evidence URL")
            if "quote" in item and (not _text(item["quote"]) or not _web_url(item.get("url"))):
                raise ValueError("review quote needs a primary URL")
        limitations = row.get("limitations_ru")
        if not isinstance(limitations, list) or not limitations or not all(_text(text) for text in limitations):
            raise ValueError("missing identity review limitations")
        reviews[hotel_id] = row
    return reviews


def _snapshot(data_dir: Path) -> dict[str, dict]:
    path = Path(data_dir).resolve() / "seed/catalogue_reviews.json"
    with _LOCK:
        signature = _signature(path)
        cached = _CACHE.get(path)
        if cached is not None and cached.signature == signature:
            return cached.reviews
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("catalogue review ledger exceeds size limit")
            document = json.loads(raw, object_pairs_hook=_object)
            reviews = _validate(document)
            if _signature(path) != signature:
                raise ValueError("catalogue review ledger changed during loading")
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            message = f"Catalogue review ledger is unavailable or invalid: {path} ({type(exc).__name__})"
            if cached is None:
                raise CatalogueReviewError(message) from exc
            _LOG.warning("%s; retaining the last validated eligibility snapshot", message)
            _CACHE[path] = _Snapshot(signature, cached.reviews)
            return cached.reviews
        _CACHE[path] = _Snapshot(signature, reviews)
        return reviews


def load_reviews(data_dir: Path = DATA) -> dict[str, dict]:
    """Validate local configuration for boot, returning an independent snapshot."""
    return deepcopy(_snapshot(data_dir))


def review_for(hotel_id: str, data_dir: Path = DATA) -> dict | None:
    """Exact ID lookup; no name matching, tag inference or network requests."""
    return deepcopy(_snapshot(data_dir).get(hotel_id))


def searchable(hotel: Hotel | dict, data_dir: Path = DATA) -> bool:
    hotel_id = hotel.id if isinstance(hotel, Hotel) else hotel.get("id")
    review = review_for(hotel_id, data_dir)
    return review is None or review["disposition"] != HOLD


@overload
def annotate(hotel: Hotel, data_dir: Path = DATA) -> Hotel: ...


@overload
def annotate(hotel: dict, data_dir: Path = DATA) -> dict: ...


def annotate(hotel: Hotel | dict, data_dir: Path = DATA) -> Hotel | dict:
    """Return the same input type, copying metadata without changing raw seeds."""
    hotel_id = hotel.id if isinstance(hotel, Hotel) else hotel.get("id")
    review = review_for(hotel_id, data_dir)
    result = hotel.model_copy(deep=True) if isinstance(hotel, Hotel) else deepcopy(hotel)
    tags = result.tags if isinstance(result, Hotel) else result.get("tags")
    if not isinstance(tags, dict):
        tags = {}
    if review is not None:
        fields = ("disposition", "reason_ru", "source", "method", "confidence", "observed_at", "valid_until")
        tags["catalogue_review"] = {key: review[key] for key in fields}
        tags["catalogue_review"]["evidence_url"] = EVIDENCE_URL
    else:
        # A stale annotation on a copied card is not an authoritative review.
        tags.pop("catalogue_review", None)
    if isinstance(result, Hotel):
        result.tags = tags
    else:
        result["tags"] = tags
    return result
