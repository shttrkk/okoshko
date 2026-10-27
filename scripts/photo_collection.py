"""Bounded official-site exploration and QA contact sheets for hotel-photo collection.

Only seed website domains and their linked gallery/rooms/about pages are visited. New official
originals are never written: Net(preview_only=True) keeps a small ignored QA preview and original
byte hash/dimensions. No image is approved by this module; review decisions are explicit URL lists.
"""
from __future__ import annotations

import html
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from scripts.collect_hotel_photos import (
    BAD_IMAGE_URL,
    MIN_SITE_WIDTH,
    PARKED,
    PageImages,
    clean_html,
    denied,
    image_host_ok,
    page_identity,
    probe_image,
    site_of,
    site_rejections,
)

PAGE_TOPIC = re.compile(r"галере|фото|номер|об отеле|об гостинице|gallery|photo|rooms?|accommodation|about", re.I)
NOT_PAGE = re.compile(r"\.(?:jpe?g|png|webp|svg|pdf|zip|mp4)(?:$|[?])|booking|reservation|брон|politic|privacy", re.I)


def document_from(net, url):
    page = net.get(url, "pages")
    if not page or page.get("error") or page.get("status") != 200 or "html" not in page.get("content_type", ""):
        return None, page, (page or {}).get("error") or f"http {(page or {}).get('status')}"
    raw = net.body(page, "pages")
    charset = re.search(r"charset=([\w-]+)", page["content_type"]) or re.search(rb"charset=[\"']?([\w-]+)", raw[:4000])
    encoding = charset.group(1) if charset else "utf-8"
    encoding = encoding.decode() if isinstance(encoding, bytes) else encoding
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    document = PageImages()
    document.feed(text)
    return document, page, None


def page_key(url):
    parts = urlsplit(url)
    return ((parts.hostname or "").lower().removeprefix("www."), parts.path.rstrip("/"), parts.query)


def linked_pages(document, page_url, website, *, prefer_rooms=False):
    """Only explicit, same-domain gallery/room/about links; never synthesize paths."""
    urls = []
    for link in document.links:
        url = urljoin(page_url, html.unescape(link["href"]))
        parts = urlsplit(url)
        if (parts.scheme not in {"https", "http"} or parts.fragment and page_key(url) == page_key(page_url)
                or denied(url) or not image_host_ok(url, website) or NOT_PAGE.search(url)
                or not PAGE_TOPIC.search(link["text"] + " " + parts.path)):
            continue
        url = parts._replace(fragment="").geturl()
        if page_key(url) != page_key(page_url) and all(page_key(url) != page_key(u) for u in urls):
            urls.append(url)
    # Dedicated photo galleries usually yield more candidates with fewer requests.
    preferred = r"room|nomer|accommodation" if prefer_rooms else r"gal|photo|foto"
    return sorted(urls, key=lambda u: (not bool(re.search(preferred, u, re.I)), len(u)))


def collect(hotels, net, review, *, max_pages=3, max_images=12):
    sites, reasons = site_rejections(hotels)
    rejected = {(r["hotel_id"], r["kind"], r["ref"]) for r in review["reject"]}
    results, logs = {}, []
    for hotel in hotels:
        hotel_id, website = hotel["id"], sites.get(hotel["id"])
        if not website:
            continue
        if hotel_id in reasons or (hotel_id, "official_site", "site") in rejected:
            logs.append({"hotel_id": hotel_id, "url": website, "status": "skipped",
                         "reason": reasons.get(hotel_id, "site rejected on manual review")})
            continue
        net.site_domain = site_of(urlsplit(website).hostname)
        approved = review["accept"].get(hotel_id, {})
        approved_urls = {approved.get("official_site"), *approved.get("room", [])} - {None}
        page_pins = review.get("image_pages", {}).get(hotel_id, {})
        pins = ({approved["official_site"]: page_pins.get(approved["official_site"], website)}
                if approved.get("official_site") else {})
        pins.update(page_pins)
        image_limit = max(max_images, len(approved_urls))
        queue = [website] + list(dict.fromkeys(u for u in pins.values() if image_host_ok(u, website) and not denied(u)))
        seen_pages, seen_images, rows = set(), set(), []
        while queue and len(seen_pages) < max_pages and len(rows) < image_limit:
            url = queue.pop(0)
            if page_key(url) in seen_pages:
                continue
            seen_pages.add(page_key(url))
            document, page, error = document_from(net, url)
            final_url = (page or {}).get("final_url", url)
            if error or not image_host_ok(final_url, website) or denied(final_url):
                logs.append({"hotel_id": hotel_id, "url": url, "status": "page_unavailable",
                             "reason": error or "redirect outside seed website domain"})
                continue
            sochi, named = page_identity(hotel, document, final_url, website)
            if PARKED.search(" ".join(document.title + document.text)[:4000]) or not (sochi and named):
                logs.append({"hotel_id": hotel_id, "url": final_url, "status": "identity_failed",
                             "reason": f"sochi_area={sochi} hotel_name={named}"})
                continue
            queue.extend(u for u in linked_pages(document, final_url, website, prefer_rooms=bool(review["accept"].get(hotel_id, {}).get("official_site"))) if all(page_key(u) != page_key(v) for v in queue) and page_key(u) not in seen_pages)
            title = clean_html(document.meta.get("og:title") or " ".join(document.title))[:160] or hotel["name"]
            candidates = [("hero_img", image) for image, source in pins.items() if page_key(source) == page_key(final_url)
                          or len(seen_pages) == 1 and page_key(source) == page_key(website)]
            candidates += [("og_image", document.meta[k]) for k in ("og:image", "og:image:url", "twitter:image") if document.meta.get(k)]
            candidates += [("hero_img", img["src"]) for img in document.images if not img["chrome"]
                           and not BAD_IMAGE_URL.search(img["src"] + " " + img["context"])
                           and not (img["width"] and img["width"] < MIN_SITE_WIDTH)]
            notes = []
            probes = 0
            page_successes = 0
            page_limit = min(4, max_images) if len(seen_pages) == 1 and queue else max_images
            for match, src in candidates:
                absolute = urljoin(final_url, html.unescape(src.strip()))
                if (absolute in seen_images or not absolute.startswith(("https://", "http://"))
                        or BAD_IMAGE_URL.search(absolute) or denied(absolute) or not image_host_ok(absolute, website)):
                    continue
                seen_images.add(absolute)
                if absolute not in approved_urls and any((hotel_id, kind, absolute) in rejected for kind in ("official_site", "room")):
                    continue
                if probes >= image_limit * 2 or len(rows) >= image_limit:
                    break
                if page_successes >= page_limit and absolute not in approved_urls:
                    continue
                probes += 1
                size, note = probe_image(net, absolute)
                served = size["final_url"] if size else absolute
                if size and (denied(served) or not image_host_ok(served, website)):
                    size, note = None, "redirect outside seed website domain"
                notes.append({"url": absolute, "result": note})
                if not size:
                    continue
                if any(r["url"] == served or r.get("sha256") == size["sha256"] for r in rows):
                    continue
                page_successes += 1
                rows.append({"kind": "official_site", "url": served, "local": False, "page_url": final_url,
                             "title": title, "author": None, "license": None, "license_url": None,
                             "match": match, "distance_m": None, **{k: size[k] for k in ("width", "height", "bytes", "sha256")},
                             "verified_by_eye": False})
            logs.append({"hotel_id": hotel_id, "url": final_url, "status": "inspected", "probes": notes})
        if rows:
            results[hotel_id] = rows
        print(f"official expansion {hotel_id} pages={len(seen_pages)} candidates={len(rows)} requests={net.requests}", flush=True)
    return results, logs


def reviewed_rows(hotel_id, rows, review):
    """No inferred approval: exact URL pin for cover, explicit URL list for guestrooms."""
    accepted = review["accept"].get(hotel_id, {})
    rooms = accepted.get("room", [])
    if isinstance(rooms, str):
        raise ValueError("room review must be a URL list")
    if accepted.get("official_site") in rooms:
        raise ValueError("Cover URL must not also be a room URL")
    selected = []
    for row in rows:
        if row["url"] == accepted.get("official_site"):
            selected.append({**row, "kind": "official_site", "verified_by_eye": True})
        if row["url"] in rooms:
            selected.append({**row, "kind": "room", "verified_by_eye": True})
    return selected


def contact_sheets(candidates, hotels, net, destination: Path, *, batch_size=12):
    """Create numbered ignored QA sheets, plus an exact image-number → URL manifest."""
    from PIL import Image, ImageDraw, ImageOps

    destination.mkdir(parents=True, exist_ok=True)
    names = {h["id"]: h["name"] for h in hotels}
    manifest = []
    for hotel_id, rows in candidates.items():
        for row in rows:
            manifest.append({"number": len(manifest) + 1, "hotel_id": hotel_id, "hotel_name": names[hotel_id], **row})
    for start in range(0, len(manifest), batch_size):
        batch = manifest[start:start + batch_size]
        sheet = Image.new("RGB", (1260, 340 * ((len(batch) + 2) // 3)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, row in enumerate(batch):
            net.site_domain = site_of(urlsplit(row["url"]).hostname)
            record = net.get(row["url"], "site_images")
            origin = Path(record.get("_read_cache", net.cache))
            preview = origin / "site_images" / record.get("preview", record.get("body", ""))
            if not preview.is_file():
                raise ValueError(f"QA preview unavailable for {row['url']}; refresh this candidate before reviewing")
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(preview.read_bytes()))).convert("RGB")
            image.thumbnail((410, 280))
            x, y = (i % 3) * 420, (i // 3) * 340
            sheet.paste(image, (x + (420 - image.width) // 2, y))
            # IDs/URLs live in the manifest; labels use ASCII so system font support is not required.
            draw.text((x + 5, y + 284), f"{row['number']} {row['hotel_id'][:30]}", fill="black")
            draw.text((x + 5, y + 302), urlsplit(row["url"]).path[-53:], fill="black")
        sheet.save(destination / f"sheet-{start // batch_size + 1:03}.jpg", quality=80)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def prune_reviewed_previews(net, review):
    """Drop only redundant local QA thumbnails after an explicit accept/reject decision.

    Contact sheets remain the visual audit artifact. Dimensions/hash/byte metadata remain usable
    for offline rebuilding. The optional read-only fallback cache is never modified.
    """
    decided = {r["ref"] for r in review["reject"] if r["ref"] != "site"}
    for accepted in review["accept"].values():
        if accepted.get("official_site"):
            decided.add(accepted["official_site"])
        decided.update(accepted.get("room", []))
    count = 0
    for path in (net.cache / "site_images").glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("preview") and (record.get("url") in decided or record.get("final_url") in decided):
            preview = path.parent / record["preview"]
            if preview.exists():
                preview.unlink()
                count += 1
    return count
