"""Reproducible hotel photo index: licensed Commons buildings and official-site links.

Official images are linked from the seed website domain only. ``--expand-official`` follows a
bounded set of explicitly linked gallery/rooms/about pages and produces candidates for exact-URL
manual eye review. ``room`` is only assigned by the review file, at most eight per hotel; cover and
room URLs cannot overlap. Counts use the best non-room image and separate room counters.

No new official original is stored. Ignored QA previews/contact sheets support manual review;
dimensions, original bytes and SHA-256 support offline rebuilds. ``--read-cache-dir`` reads an older
cache without modifying it. ``--skip-commons`` preserves approved buildings; ``--only`` preserves
unselected hotels. Robots, denylist and at most one request per second per host apply before each
request/redirect. Commons uses the public APIs and only approved free licences.

Examples (Pillow required for probing/QA; install requirements-dev.lock)::

    .venv/bin/python -m scripts.collect_hotel_photos --expand-official --skip-commons --require-review
    .venv/bin/python -m scripts.collect_hotel_photos --offline --expand-official --require-review
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import re
import time
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import requests

from scripts.collect_weak_labels import BLOCKED, allowed_host, fetch
from scripts.collect_weak_labels import USER_AGENT as WEAK_LABEL_AGENT

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data/seed"
# Same product token as the weak-label collector, so robots.txt groups match identically.
USER_AGENT = WEAK_LABEL_AGENT.split(" ")[0] + " (non-commercial hotel photo index; one-time collection)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
OVERPASS_API = "https://overpass-api.de/api/interpreter"
RADIUS_M = 150
CATEGORY_MEMBER_MAX_M = 400
THUMB_WIDTH = 800
MAX_WEBP_BYTES = 150_000
MIN_SITE_WIDTH = 480
ASPECT = (1.0, 2.4)
SHARED_SITE_SPREAD_M = 300
PUBLIC_PREFIX = "/data/seed/photos/hotels/"
# Without a contact address in the User-Agent, Wikimedia allows ~10 API requests per minute.
HOST_INTERVAL_S = {"commons.wikimedia.org": 6.5, "www.wikidata.org": 6.5, "upload.wikimedia.org": 2.0}

# Aggregators, OTAs, maps, classifieds, link shorteners and social networks (pages and image CDNs).
EXTRA_BLOCKED = {
    "101hotels.ru", "airbnb.com", "airbnb.ru", "avito.ru", "avito.st", "bit.ly", "bnovo.ru", "booking.ru",
    "bstatic.com", "business.site", "cian.ru", "clck.ru", "cdninstagram.com", "dzen.ru", "emergingtravel.com",
    "facebook.com", "fbcdn.net", "flamp.ru", "google.com", "googleusercontent.com", "hotellook.com",
    "hotellook.ru", "instagram.com", "irecommend.ru", "kayak.com", "kvartirka.com", "level.travel", "mycdn.me",
    "ok.ru", "onetwotrip.com", "otpusk.com", "otzovik.com", "ozon.travel", "pinterest.com", "sutochno.com",
    "t.me", "taplink.cc", "taplink.ws", "telegram.me", "telegram.org", "tiktok.com", "tophotels.pro",
    "tophotels.ru", "travelata.ru", "travelline.ru", "trip.com", "tripadvisor.ru", "trivago.com", "trivago.ru",
    "twitter.com", "userapi.com", "vk.com", "vk.ru", "vkuserphoto.ru", "wa.me", "whatsapp.com", "worldota.net",
    "x.com", "ya.ru", "yandex.com", "yandex.net", "youtube.com", "ytimg.com", "zoon.ru", "2gis.com",
}
DENYLIST = BLOCKED | EXTRA_BLOCKED
# Pages that are not the hotel's own property site (identified while reviewing seed websites).
EXCLUDED_SITES = {
    "sochiadm.ru": "city administration portal, not a hotel site",
    "pullmanhotels.com": "global brand homepage, not a property page",
    "tortugabayhotel.com": "property outside Sochi (wrong Overture website)",
    "anapasochi.ru": "sanatorium aggregator, not a single hotel",
}
PARKED = re.compile(r"срок регистрации домена ист|домен прода[её]тся|domain (?:is )?for sale|this domain (?:has )?expired|купить (?:этот )?домен",
                    re.I)
BAD_IMAGE_URL = re.compile(r"logo|icon|favicon|sprite|placeholder|blank|pixel|spinner|loader|preload|watermark|banner-?ad|"
                           r"captcha|counter|mc\.yandex|1x1|expired|\.svg(?:$|\?)|\.gif(?:$|\?)|^data:|/-/resizeb?/\d{1,2}x/|/-/empty/", re.I)
FREE_LICENSE = re.compile(r"(?i)^(?:cc0(?: 1\.0)?|public domain|pd(?:-[\w.-]+)?|cc by(?:-sa)? \d\.\d(?: [a-z-]+)?)$")
PHOTO_MIME = {"image/jpeg", "image/png", "image/webp", "image/tiff"}

GENERIC_WORDS = """
отель отеля отелей гостиница гостиницы гостиничный комплекс гк hotel hotels otel apartments apartment apart aparts
апартаменты апартамент аппартаменты апарт мини mini гостевой гостевые дом guest house guesthouse хостел hostel пансионат
новый санаторий санатории санатория sanatorium resort курорт spa спа бутик boutique park парк sochi сочи adler адлер
в на и г с the by of in and at center centre центр club клуб residence резиденция inn family семейный посуточно квартира
квартиры имени им new вилла villa lite лайт plus плюс бар bar sea море lux люкс grand гранд city сити apartment hotel
оздоровительный пансионат кавказских минеральных вод фгуп юг спорт sport group груп лига отельеров congress
море моря sea black черное черного beach пляж бич
"""
HOTEL_CONTEXT = ("hotel", "otel", "gostin", "sanator", "pansionat", "resort", "hostel", "apart", "kurort", "motel")
SOCHI_AREA = ("sochi", "adler", "sirius", "imeretin", "hosta", "macest", "dagomis", "kudeps", "lazarev")

TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i",
            "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
            "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
            "я": "ya", "і": "i", "ї": "i", "є": "e"}


def canon(text):
    """Script-independent token form: ru->latin transliteration plus folding of common spelling variants."""
    s = (text or "").lower().replace("ь", "\x02").replace("ъ", "")
    s = re.sub("\x02(?=[еёюяи])", "y", s).replace("\x02", "")
    s = "".join(TRANSLIT.get(ch, ch) for ch in s)
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    for a, b in (("shch", "sh"), ("sch", "sh"), ("ch", "\x01"), ("ck", "k")):
        s = s.replace(a, b)
    s = re.sub("c(?=[eiy])", "s", s).replace("c", "k").replace("\x01", "ch")
    for a, b in (("kh", "h"), ("ph", "f"), ("th", "t"), ("ts", "c"), ("tz", "c"), ("w", "v"), ("x", "ks"), ("q", "k"),
                 ("y", "i"), ("j", "i")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"([a-z])\1+", r"\1", s)
    s = re.sub(r"(?<=[^aeiou ])i(?=[aeou])", "", s)  # я/ю as "ya"/"a": Заполярье ~ Zapolarye ~ Zapolyarye
    return re.sub(r"([a-z])\1+", r"\1", s).strip()


GENERIC = {canon(w) for w in GENERIC_WORDS.split()}


def tokens(text):
    return canon(text).split()


def same_word(a, b):
    """Equal tokens, or the same stem under Russian inflection (Жемчужина/Жемчужины, Дзержинского/Dzerzhinsky)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5:
        return False
    common = len(os.path.commonprefix([a, b]))
    return common >= max(5, max(len(a), len(b)) - 3)


def name_variants(hotel, extra=()):
    names = [hotel["name"], *extra] + [hotel.get("tags", {}).get(k) for k in
                                       ("name", "name:ru", "name:en", "official_name", "alt_name", "old_name", "old_name:en")]
    variants = []
    for name in names:
        distinctive = [t for t in tokens(name) if t not in GENERIC and len(t) >= 3 and not t.isdigit()]
        if distinctive and max(map(len, distinctive)) >= 4 and distinctive not in variants:
            variants.append(distinctive)
    return variants


def field_match(variants, text):
    """Return the matched variant if every distinctive token occurs in this one text field."""
    words = tokens(text)
    if not words:
        return None
    context = any(w.startswith(HOTEL_CONTEXT) for w in words)
    for variant in variants:
        if all(any(same_word(t, w) for w in words) for t in variant):
            # A single common word (Марина, Магнолия) must sit next to a hotel word in the same field.
            if len(variant) >= 2 or context:
                return variant
    return None


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def site_of(host):
    normalized = (host or "").lower().removeprefix("www.").encode("idna").decode("ascii")
    labels = normalized.split(".")
    return ".".join(labels[-2:])


def denied(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return not allowed_host(url) or any(host == d or host.endswith("." + d) for d in DENYLIST)


def clean_html(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", value or ""))).strip()


def key_of(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


class Net:
    """Cached, paced HTTP access. Every outgoing request passes the denylist and a 1 req/s/host pacer."""

    def __init__(self, cache: Path, offline: bool, refresh: bool, read_cache: Path | None = None, preview_only: bool = False):
        self.cache, self.offline, self.refresh = cache, offline, refresh
        self.read_cache = read_cache
        self.preview_only = preview_only
        self.site_domain = None
        self.last, self.robots, self.wiki_robots = {}, {}, {}
        self.cooldown = {}
        self.requests = 0
        # Websites: httpx client shared with collect_weak_labels.fetch (robots before every page/redirect).
        self.client = None if offline else httpx.Client(
            timeout=httpx.Timeout(12), headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=2),
            event_hooks={"request": [lambda request: self._pace(str(request.url))]})
        # Wikimedia edge rejects the httpx client fingerprint; the data pipeline already uses requests there.
        self.session = None if offline else requests.Session()
        if self.session:
            self.session.headers["User-Agent"] = USER_AGENT

    def _pace(self, url):
        if denied(url):
            raise ValueError("Denylisted host: " + (urlsplit(url).hostname or ""))
        host = urlsplit(url).hostname
        if self.site_domain and site_of(host) != self.site_domain:
            raise ValueError("Redirect outside seed website domain")
        wait = max(self.last.get(host, 0.0) + HOST_INTERVAL_S.get(host, 1.0),
                   self.cooldown.get(host, 0.0)) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last[host] = time.monotonic()
        self.requests += 1

    def _cached(self, kind, key):
        path = self.cache / kind / (key + ".json")
        if path.exists() and not self.refresh:
            return path, json.loads(path.read_text())
        if self.read_cache and not self.refresh:
            fallback = self.read_cache / kind / (key + ".json")
            if fallback.exists():
                value = json.loads(fallback.read_text())
                value["_read_cache"] = str(self.read_cache)
                return path, value
        return path, None

    @staticmethod
    def _store(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
        return value

    def api(self, endpoint, params, method="GET"):
        """MediaWiki/Wikidata/Overpass API call (APIs are the sanctioned programmatic channel; robots.txt
        governs crawling of pages, which this function never does)."""
        path, value = self._cached("api", key_of(endpoint, params, method))
        if value is not None or self.offline:
            return value
        for attempt in range(8):
            self._pace(endpoint)
            if method == "POST":
                response = self.session.post(endpoint, data=params, timeout=(10, 90))
            else:
                response = self.session.get(endpoint, params=params, timeout=(10, 60))
            if response.status_code in {429, 502, 503, 504}:
                retry_after = response.headers.get("retry-after", "")
                time.sleep(max(int(retry_after) if retry_after.isdigit() else 0, 10 * 2 ** min(attempt, 4)))
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                time.sleep(int(response.headers.get("retry-after", "5") or 5))
                continue
            return self._store(path, data)
        raise RuntimeError("API unavailable: " + endpoint)

    def mediawiki(self, endpoint, params, method="GET"):
        """Run a query with continuation; merge page records (formatversion=2) by title.
        Long title batches go as POST (Cyrillic titles overflow GET URLs)."""
        base = {"action": "query", "format": "json", "formatversion": "2", "maxlag": "5", **params}
        pages, cont, lists = {}, {}, {}
        for _ in range(50):
            data = self.api(endpoint, {**base, **cont}, method=method)
            if data is None:
                return None
            query = data.get("query", {})
            for page in query.get("pages", []):
                merged = pages.setdefault(page["title"], {})
                for k, v in page.items():
                    if isinstance(v, list) and isinstance(merged.get(k), list):
                        merged[k] = merged[k] + [x for x in v if x not in merged[k]]
                    elif k not in merged:
                        merged[k] = v
            for k, v in query.items():
                if k != "pages" and isinstance(v, list):
                    lists.setdefault(k, []).extend(v)
            if "continue" not in data:
                break
            cont = data["continue"]
        return {"pages": pages, **lists}

    def get(self, url, kind):
        """Robots-checked GET via collect_weak_labels.fetch; returns cached {status, url, type, body_file}."""
        path, value = self._cached(kind, key_of(url))
        if value is not None or self.offline:
            return value
        try:
            response = fetch(self.client, url, self.robots)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            return self._store(path, {"url": url, "error": type(error).__name__ + ": " + str(error)[:160]})
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "")
            try:
                delay = max(0, int(retry_after))
            except ValueError:
                try:
                    until = parsedate_to_datetime(retry_after)
                    delay = max(0, (until - datetime.now(UTC)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            self.cooldown[urlsplit(str(response.url)).hostname] = time.monotonic() + delay
        metadata = {"url": url, "final_url": str(response.url), "status": response.status_code,
                    "content_type": response.headers.get("content-type", ""), "bytes": len(response.content),
                    "retrieved_at": datetime.now(UTC).isoformat()}
        if self.preview_only and kind == "site_images":
            # QA only: retain dimensions/hash and a small review preview, never the official original.
            from PIL import Image, ImageOps

            try:
                image = Image.open(io.BytesIO(response.content))
                metadata["probe"] = {"width": image.width, "height": image.height,
                                     "sha256": hashlib.sha256(response.content).hexdigest(),
                                     "bytes": len(response.content), "final_url": str(response.url)}
                frame = ImageOps.exif_transpose(image).convert("RGB")
                frame.thumbnail((420, 300))
                preview = path.with_suffix(".qa.jpg")
                preview.parent.mkdir(parents=True, exist_ok=True)
                frame.save(preview, "JPEG", quality=76)
                metadata["preview"] = preview.name
            except Exception as error:  # noqa: BLE001 - reject undecodable remote input
                metadata["error"] = "undecodable " + type(error).__name__
            return self._store(path, metadata)
        body = path.with_suffix(".bin")
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(response.content)
        return self._store(path, {**metadata, "body": body.name})

    def wiki_file(self, url):
        """Commons thumbnail download (requests transport) after an upload host robots.txt check."""
        path, value = self._cached("thumbs", key_of(url))
        if value is not None or self.offline:
            return value
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if origin not in self.wiki_robots:
            self._pace(origin + "/robots.txt")
            robots = self.session.get(origin + "/robots.txt", timeout=(10, 30))
            policy = RobotFileParser()
            policy.parse(robots.text.splitlines() if robots.status_code == 200 else [] if robots.status_code in {404, 410}
                         else ["User-agent: *", "Disallow: /"])
            self.wiki_robots[origin] = policy
        if not self.wiki_robots[origin].can_fetch(USER_AGENT, url):
            return self._store(path, {"url": url, "error": "Robots policy forbids URL"})
        for attempt in range(6):
            self._pace(url)
            response = self.session.get(url, timeout=(10, 60))
            if response.status_code not in {429, 503}:
                break
            retry_after = response.headers.get("retry-after", "")
            time.sleep(max(int(retry_after) if retry_after.isdigit() else 0, 10 * 2 ** min(attempt, 4)))
        if response.status_code != 200:
            return {"url": url, "error": f"http {response.status_code}"}  # transient; not cached
        body = path.with_suffix(".bin")
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(response.content)
        return self._store(path, {"url": url, "final_url": response.url, "status": response.status_code,
                                  "content_type": response.headers.get("content-type", ""), "bytes": len(response.content),
                                  "body": body.name, "retrieved_at": datetime.now(UTC).isoformat()})

    def body(self, record, kind):
        return (Path(record.get("_read_cache", self.cache)) / kind / record["body"]).read_bytes() if record and record.get("body") else b""


# ---------------------------------------------------------------- Commons / Wikidata / OSM: building photos

def osm_refs(hotels, net):
    """OSM ids of hotel features and their matched buildings; tags from seed plus current Overpass tags."""
    refs = {}
    for hotel in hotels:
        ids = {hotel["id"], hotel["tags"].get("building_id")} | {r["id"] for r in hotel["tags"].get("source_records", [])}
        refs[hotel["id"]] = sorted(i for i in ids if i and i.startswith(("osm-node-", "osm-way-")))
    nodes = sorted({i[9:] for v in refs.values() for i in v if i.startswith("osm-node-")})
    ways = sorted({i[8:] for v in refs.values() for i in v if i.startswith("osm-way-")})
    query = f"[out:json][timeout:60];(node(id:{','.join(nodes)});way(id:{','.join(ways)}););out tags;"
    tags = {}
    try:
        data = net.api(OVERPASS_API, {"data": query}, method="POST") or {}
        tags = {f"osm-{e['type']}-{e['id']}": e.get("tags", {}) for e in data.get("elements", [])}
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print("overpass unavailable, using seed tags only:", type(error).__name__, flush=True)
    for hotel in hotels:
        tags.setdefault(hotel["id"], {})
        for k, v in hotel["tags"].items():
            if isinstance(v, str):
                tags[hotel["id"]].setdefault(k, v)
    return refs, tags


def commons_file_from_tag(value):
    value = (value or "").strip()
    match = re.search(r"(?:File|Файл|Image):([^|#?]+\.(?:jpe?g|png|webp|tiff?))", value.replace("%20", " "), re.I)
    if match:
        return "File:" + match.group(1).replace("_", " ")
    return None


def explicit_files(hotels, net, refs, tags):
    """Wikidata P18 for explicit OSM wikidata links, and OSM image/wikimedia_commons tags."""
    found = {}
    qids = {}
    for hotel in hotels:
        for ref in refs[hotel["id"]]:
            t = tags.get(ref, {})
            qid = t.get("wikidata", "")
            if re.fullmatch(r"Q\d+", qid):
                qids.setdefault(qid, set()).add(hotel["id"])
            for key in ("wikimedia_commons", "image"):
                title = commons_file_from_tag(t.get(key))
                if title:
                    found.setdefault(hotel["id"], []).append({"title": title, "match": "osm_tag",
                                                              "match_text": f"{ref} {key}={t[key]}"})
    labels = {}
    if qids:
        data = net.api(WIKIDATA_API, {"action": "wbgetentities", "format": "json", "ids": "|".join(sorted(qids)),
                                      "props": "claims|labels", "languages": "ru|en"})  # single read; WDQS lag makes maxlag fail or {}
        for qid, entity in data.get("entities", {}).items():
            names = [v["value"] for v in entity.get("labels", {}).values()]
            for claim in entity.get("claims", {}).get("P18", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if value:
                    for hotel_id in qids[qid]:
                        found.setdefault(hotel_id, []).append({"title": "File:" + value, "match": "wikidata_p18",
                                                               "match_text": f"{qid} P18"})
            for hotel_id in qids[qid]:
                labels[hotel_id] = names
    return found, labels


def geosearch(hotels, net):
    hits = {}
    for i, hotel in enumerate(hotels):
        data = net.mediawiki(COMMONS_API, {"list": "geosearch", "gscoord": f"{hotel['lat']}|{hotel['lon']}",
                                           "gsradius": RADIUS_M, "gsnamespace": "6|14", "gslimit": 500, "gsprimary": "all"})
        hits[hotel["id"]] = (data or {}).get("geosearch", [])
        if i % 25 == 0:
            print(f"geosearch {i}/{len(hotels)} requests={net.requests}", flush=True)
    return hits


CATEGORY_ROOTS = ("Category:Hotels in Sochi",)
CATEGORY_CONTAINER = re.compile(r"^Category:(?:Hotels|Health resorts|Sanatori(?:ums|a)|Resorts) in ", re.I)


def category_tree(net):
    """Leaf categories under Commons 'Hotels in Sochi' (incl. health resorts/sanatoriums), names only."""
    seen, queue, leaves = set(), list(CATEGORY_ROOTS), set()
    while queue and len(seen) < 30:
        category = queue.pop(0)
        if category in seen:
            continue
        seen.add(category)
        data = net.mediawiki(COMMONS_API, {"list": "categorymembers", "cmtitle": category, "cmtype": "subcat", "cmlimit": 500})
        for member in (data or {}).get("categorymembers", []):
            if CATEGORY_CONTAINER.match(member["title"]):
                queue.append(member["title"])
            else:
                leaves.add(member["title"])
    return sorted(leaves)


def file_metadata(titles, net):
    meta = {}
    titles = sorted(set(titles))
    for i in range(0, len(titles), 50):
        data = net.mediawiki(COMMONS_API, {
            "titles": "|".join(titles[i:i + 50]), "prop": "imageinfo|categories|coordinates",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": THUMB_WIDTH, "iiextmetadatamultilang": 1,
            "iiextmetadatafilter": "ImageDescription|ObjectName|LicenseShortName|LicenseUrl|Artist|Credit|AttributionRequired",
            "clshow": "!hidden", "cllimit": "max", "coprimary": "all", "colimit": "max"}, method="POST")
        for title, page in (data or {}).get("pages", {}).items():
            meta[title] = page
        if (i // 50) % 20 == 0:
            print(f"metadata {i}/{len(titles)} requests={net.requests}", flush=True)
    return meta


def ext(page, key):
    value = ((page.get("imageinfo") or [{}])[0].get("extmetadata", {}).get(key) or {}).get("value", "")
    if isinstance(value, dict):
        return " | ".join(clean_html(v) for k, v in value.items() if k != "_type" and isinstance(v, str))
    return clean_html(value)


def photo_record(page):
    """Licence/author/size checks for one Commons file; None if it cannot be reused here."""
    info = (page.get("imageinfo") or [{}])[0]
    license = ext(page, "LicenseShortName").split(" | ")[0]
    author = ext(page, "Artist") or None
    if not info.get("thumburl") or info.get("mime") not in PHOTO_MIME or not FREE_LICENSE.match(license):
        return None
    if author is None and not re.match(r"(?i)cc0|public domain|pd", license):
        return None
    if min(info.get("width", 0), info.get("height", 0)) < 400:
        return None
    return {"title": page["title"], "page_url": info.get("descriptionurl"), "thumb_url": info["thumburl"],
            "author": author[:300] if author else None, "license": license, "license_url": ext(page, "LicenseUrl") or None,
            "width": info.get("width"), "height": info.get("height"), "pageid": page.get("pageid")}


def building_candidates(hotels, net):
    refs, tags = osm_refs(hotels, net)
    explicit, wikidata_labels = explicit_files(hotels, net, refs, tags)
    hits = geosearch(hotels, net)
    variants = {h["id"]: name_variants(h, wikidata_labels.get(h["id"], ())) for h in hotels}
    by_id = {h["id"]: h for h in hotels}
    # Categories about the hotel (geotagged within the radius and named like it) contribute their files.
    matched = {}
    for hotel_id, rows in hits.items():
        for row in rows:
            if row["ns"] == 14 and variants[hotel_id] and field_match(variants[hotel_id], row["title"].split(":", 1)[1]):
                matched.setdefault(hotel_id, {})[row["title"]] = row["dist"]
    # Hotel/sanatorium categories of Sochi without coordinates: name match now, location verified below.
    tree = category_tree(net)
    tree_coords = {}
    if tree:
        data = net.mediawiki(COMMONS_API, {"titles": "|".join(tree), "prop": "coordinates", "coprimary": "all"}, method="POST")
        tree_coords = {t: p.get("coordinates", []) for t, p in (data or {}).get("pages", {}).items()}
    for hotel in hotels:
        for category in tree:
            if variants[hotel["id"]] and field_match(variants[hotel["id"]], category.split(":", 1)[1]):
                dists = [distance_m(hotel["lat"], hotel["lon"], c["lat"], c["lon"]) for c in tree_coords.get(category, [])]
                matched.setdefault(hotel["id"], {}).setdefault(category, min(dists) if dists else None)
    category_members = {}
    for hotel_id, categories in matched.items():
        for category, dist in sorted(categories.items()):
            data = net.mediawiki(COMMONS_API, {"list": "categorymembers", "cmtitle": category, "cmtype": "file", "cmlimit": 100})
            members = [m["title"] for m in (data or {}).get("categorymembers", [])]
            category_members.setdefault(hotel_id, []).append((category, dist, members))
    titles = [r["title"] for rows in hits.values() for r in rows if r["ns"] == 6]
    titles += [c["title"] for rows in explicit.values() for c in rows]
    titles += [t for rows in category_members.values() for _, _, members in rows for t in members]
    meta = file_metadata(titles, net)
    candidates = {}

    def add(hotel_id, page, **fields):
        record = photo_record(page) if page else None
        if record:
            coords = page.get("coordinates") or []
            hotel = by_id[hotel_id]
            dists = [distance_m(hotel["lat"], hotel["lon"], c["lat"], c["lon"]) for c in coords]
            fields.setdefault("distance_m", round(min(dists), 1) if dists else None)
            candidates.setdefault(hotel_id, []).append({**record, **fields})

    for hotel_id, rows in explicit.items():
        for row in rows:
            add(hotel_id, meta.get(row["title"]), match=row["match"], match_text=row["match_text"], field="explicit")
    for hotel_id, rows in hits.items():
        if not variants[hotel_id]:
            continue
        for row in rows:
            page = meta.get(row["title"])
            if row["ns"] != 6 or not page:
                continue
            fields = [("title", re.sub(r"\.\w+$", "", row["title"].split(":", 1)[1]))]
            fields += [("description", ext(page, "ImageDescription")), ("description", ext(page, "ObjectName"))]
            fields += [("category", c["title"].split(":", 1)[1]) for c in page.get("categories", [])]
            for field, text in fields:
                if field_match(variants[hotel_id], text):
                    add(hotel_id, page, match="commons_name_match", match_text=f"{field}: {text[:200]}", field=field,
                        distance_m=round(row["dist"], 1))
                    break
    for hotel_id, rows in category_members.items():
        hotel = by_id[hotel_id]
        for category, dist, members in rows:
            member_dists = [distance_m(hotel["lat"], hotel["lon"], c["lat"], c["lon"])
                            for title in members for c in (meta.get(title) or {}).get("coordinates") or []]
            near = [d for d in member_dists if d <= CATEGORY_MEMBER_MAX_M]
            if not ((dist is not None and dist <= CATEGORY_MEMBER_MAX_M) or near):
                continue  # same name, but nothing places this category at the hotel
            where = f"category at {dist:.0f} m" if dist is not None else f"{len(near)} geotagged member(s) within {CATEGORY_MEMBER_MAX_M} m"
            for title in members:
                page = meta.get(title)
                if not page:
                    continue
                before = len(candidates.get(hotel_id, []))
                add(hotel_id, page, match="commons_name_match", match_text=f"category: {category} ({where})", field="category_geo")
                added = candidates.get(hotel_id, [])
                if len(added) > before and added[-1]["distance_m"] is not None and added[-1]["distance_m"] > CATEGORY_MEMBER_MAX_M:
                    added.pop()
    rank = {"explicit": 0, "title": 1, "category": 2, "category_geo": 2, "description": 3}
    for hotel_id, rows in candidates.items():
        unique = {}
        for row in sorted(rows, key=lambda r: (rank[r["field"]], -(r["width"] >= 1200), r["distance_m"] if r["distance_m"] is not None else 999)):
            unique.setdefault(row["title"], row)
        candidates[hotel_id] = list(unique.values())
    return candidates


def to_webp(data):
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    for width in (THUMB_WIDTH, 720, 640):
        frame = image if image.width <= width else image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        for quality in (80, 74, 68, 62, 56, 50):
            out = io.BytesIO()
            frame.save(out, "WEBP", quality=quality, method=6)
            if out.tell() <= MAX_WEBP_BYTES:
                return out.getvalue(), frame.size
    raise ValueError("Cannot fit webp under size limit")


# ---------------------------------------------------------------- official websites: linked images

class PageImages(HTMLParser):
    CHROME = {"header", "nav", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.images, self.text, self.title = {}, [], [], []
        self.links, self.anchor = [], None
        self.skip = self.chrome = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "").strip() for k, v in attrs}
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.skip += 1
        if tag in self.CHROME:
            self.chrome += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]
        if tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.meta.setdefault("image_src", a["href"])
        context = " ".join(a.get(k, "") for k in ("class", "id", "alt"))
        if tag == "a" and a.get("href"):
            self.anchor = {"href": a["href"], "text": a.get("title", "")}
            self.links.append(self.anchor)
            if re.search(r"\.(?:jpe?g|png|webp)(?:$|[?])", a["href"], re.I):
                self._image(a["href"], None, None, context)
        if tag == "img":
            src = a.get("data-original") or a.get("data-src") or a.get("data-lazy-src") or a.get("data-lazy") or ""
            srcset = a.get("data-srcset") or a.get("srcset") or ""
            if srcset:
                parts = [p.strip().split() for p in srcset.split(",") if p.strip()]
                widest = max(parts, key=lambda p: int(re.sub(r"\D", "", p[1]) or 0) if len(p) > 1 else 0)
                src = src or widest[0]
            src = src or a.get("src", "")
            self._image(src, a.get("width"), a.get("height"), context)
        elif a.get("data-original") or a.get("data-bg") or a.get("data-background"):
            self._image(a.get("data-original") or a.get("data-bg") or a.get("data-background"), None, None, context)
        elif "background-image" in a.get("style", ""):
            match = re.search(r"url\(['\"]?([^'\")]+)", a["style"])
            if match:
                self._image(match.group(1), None, None, context)

    def _image(self, src, width, height, context):
        def num(v):
            return int(v) if v and v.isdigit() else None
        if src:
            self.images.append({"src": src, "width": num(width), "height": num(height), "context": context,
                                "chrome": self.chrome > 0})

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.skip = max(0, self.skip - 1)
        if tag in self.CHROME:
            self.chrome = max(0, self.chrome - 1)
        if tag == "a":
            self.anchor = None
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor["text"] += " " + data
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.text.append(data)


def normalize_site(url):
    url = (url or "").strip()
    if url and not re.match(r"(?i)^https?://", url):
        url = "http://" + url
    parts = urlsplit(url)
    return parts._replace(netloc=parts.netloc.lower(), fragment="").geturl() if parts.netloc else None


def site_rejections(hotels):
    """Reasons to skip a hotel's website before any request (denylist, non-property pages, shared sites)."""
    sites, reasons = {}, {}
    for hotel in hotels:
        url = normalize_site(hotel.get("website"))
        if url:
            sites[hotel["id"]] = url
    by_page = {}
    for hotel_id, url in sites.items():
        by_page.setdefault(url.rstrip("/").replace("://www.", "://").split("://", 1)[1], []).append(hotel_id)
    coords = {h["id"]: (h["lat"], h["lon"]) for h in hotels}
    for hotel_id, url in sites.items():
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        site = site_of(host)
        page = url.rstrip("/").replace("://www.", "://").split("://", 1)[1]
        group = by_page[page]
        spread = max(distance_m(*coords[a], *coords[b]) for a in group for b in group)
        siblings = [u for other, u in sites.items() if other not in group and site_of(urlsplit(u).hostname) == site]
        if denied(url):
            reasons[hotel_id] = "denylisted aggregator/OTA/social host"
        elif site in EXCLUDED_SITES:
            reasons[hotel_id] = EXCLUDED_SITES[site]
        elif spread > SHARED_SITE_SPREAD_M:
            reasons[hotel_id] = f"same page used by {len(group)} hotels {spread:.0f} m apart (chain page, not property-specific)"
        elif siblings and host == site and parts.path.strip("/") in {"", "ru", "en"}:
            reasons[hotel_id] = "chain root page; other hotels use deeper pages of the same site"
    return sites, reasons


def page_identity(hotel, document, final_url, original_url):
    """The page must name the hotel and place it in the Sochi area."""
    words = tokens(" ".join(document.title) + " " + " ".join(document.meta.get(k, "") for k in
                    ("og:title", "og:site_name", "og:description", "description", "twitter:title")) + " " + " ".join(document.text))
    hosts = {(urlsplit(u).hostname or "").lower().removeprefix("www.") for u in (final_url, original_url)}
    segments = {canon(s).replace(" ", "") for h in hosts for s in re.split(r"[.\-]", h)}
    host_text = " ".join(canon(h).replace(" ", "") for h in hosts)
    sochi = any(w.startswith(SOCHI_AREA) for w in words) or any(a in host_text for a in SOCHI_AREA)
    names = [t for t in tokens(hotel["name"]) if t not in GENERIC]
    named = any(len(t) >= 4 and (any(same_word(t, w) for w in words) or t in host_text) for t in names)
    if not named and names and "".join(names) in segments:
        named = True  # initials such as "F & G" -> fg-hotel.ru
    full = canon(hotel["name"])
    if not named and not names and len(full.split()) >= 2:
        # Names made only of generic words ("City Hostel") must appear verbatim in the page title.
        heading = canon(" ".join(document.title) + " | " + document.meta.get("og:title", "") + " | " + document.meta.get("og:site_name", ""))
        named = re.search(r"(?:^| )" + re.escape(full) + r"(?: |$)", heading) is not None
    return sochi, named


def probe_image(net, url):
    record = net.get(url, "site_images")
    if not record or record.get("error"):
        return None, (record or {}).get("error", "not cached")
    if record["status"] != 200:
        return None, f"http {record['status']}"
    ctype = record["content_type"].split(";")[0].strip().lower()
    if not ctype.startswith("image/") or "svg" in ctype or "gif" in ctype:
        return None, "content-type " + ctype
    from PIL import Image

    try:
        if record.get("probe"):
            width, height = record["probe"]["width"], record["probe"]["height"]
        else:
            width, height = Image.open(io.BytesIO(net.body(record, "site_images"))).size
    except Exception as error:  # noqa: BLE001 - any undecodable payload is simply rejected
        return None, "undecodable " + type(error).__name__
    if width < MIN_SITE_WIDTH:
        return None, f"too small {width}x{height}"
    if not ASPECT[0] <= width / height <= ASPECT[1]:
        return None, f"aspect {width}x{height}"
    if record.get("probe"):
        return record["probe"], "ok"
    payload = net.body(record, "site_images")
    return {"width": width, "height": height, "final_url": record.get("final_url", url),
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}, "ok"


def image_host_ok(image_url, page_url):
    host = (urlsplit(image_url).hostname or "").lower()
    page_site = site_of(urlsplit(page_url).hostname)
    return site_of(host) == page_site


def official_candidates(hotels, net, rejected=frozenset()):
    """``rejected`` holds review decisions: (hotel_id, "official_site", image URL or "site")."""
    sites, reasons = site_rejections(hotels)
    results, log = {}, []
    for i, hotel in enumerate(hotels):
        hotel_id, url = hotel["id"], sites.get(hotel["id"])
        if not url:
            continue
        if hotel_id in reasons:
            log.append({"hotel_id": hotel_id, "url": url, "status": "skipped", "reason": reasons[hotel_id]})
            continue
        if (hotel_id, "official_site", "site") in rejected:
            log.append({"hotel_id": hotel_id, "url": url, "status": "skipped", "reason": "site rejected on manual review"})
            continue
        net.site_domain = site_of(urlsplit(url).hostname)
        page = net.get(url, "pages")
        if not page or page.get("error") or page.get("status") != 200 or "html" not in page.get("content_type", ""):
            log.append({"hotel_id": hotel_id, "url": url, "status": "page_unavailable",
                        "reason": (page or {}).get("error") or f"http {(page or {}).get('status')} {(page or {}).get('content_type')}"})
            continue
        final_url = page["final_url"]
        if denied(final_url) or not image_host_ok(final_url, url):
            log.append({"hotel_id": hotel_id, "url": url, "status": "skipped", "reason": "redirected outside permitted seed website domain"})
            continue
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
        if PARKED.search(" ".join(document.title) + " " + " ".join(document.text)[:3000]):
            log.append({"hotel_id": hotel_id, "url": final_url, "status": "skipped", "reason": "parked or expired domain"})
            continue
        sochi, named = page_identity(hotel, document, final_url, url)
        if not (sochi and named):
            log.append({"hotel_id": hotel_id, "url": final_url, "status": "identity_failed",
                        "reason": f"sochi_area={sochi} hotel_name={named}"})
            continue
        title = clean_html(document.meta.get("og:title") or " ".join(document.title))[:160] or hotel["name"]
        meta_urls = [document.meta.get(k) for k in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image",
                                                    "twitter:image:src", "image_src")]
        candidates = [("og_image", u) for u in dict.fromkeys(u for u in meta_urls if u)][:2]
        hero = [img for img in document.images if not img["chrome"] and not BAD_IMAGE_URL.search(img["src"] + " " + img["context"])
                and not (img["width"] and img["width"] < MIN_SITE_WIDTH) and not (img["height"] and img["height"] < 200)]
        candidates += [("hero_img", img["src"]) for img in hero[:6]]
        tried, chosen, notes = set(), None, []
        hero_probes = 0
        for match, src in candidates:
            absolute = urljoin(final_url, html.unescape(src.strip()))
            if absolute in tried or not absolute.startswith(("http://", "https://")):
                continue
            tried.add(absolute)
            if BAD_IMAGE_URL.search(absolute):
                notes.append(f"{match}: rejected url pattern")
                continue
            if (hotel_id, "official_site", absolute) in rejected:
                notes.append(f"{match}: rejected on manual review")
                continue
            if denied(absolute) or not image_host_ok(absolute, url):
                notes.append(f"{match}: foreign image host {urlsplit(absolute).hostname}")
                continue
            if match == "hero_img":
                if chosen is None and hero_probes >= 3:
                    break
                hero_probes += 1
            size, note = probe_image(net, absolute)
            served = size["final_url"] if size else absolute
            if size and (denied(served) or not image_host_ok(served, url)):
                size, note = None, "redirected to foreign image host " + (urlsplit(served).hostname or "")
            if size and (hotel_id, "official_site", served) in rejected:
                size, note = None, "rejected on manual review"
            notes.append(f"{match}: {note}")
            if size:
                chosen = {"kind": "official_site", "url": served, "local": False, "page_url": final_url, "title": title,
                          "author": None, "license": None, "license_url": None, "match": match, "distance_m": None,
                          "width": size["width"], "height": size["height"], "bytes": size["bytes"], "sha256": size["sha256"]}
                break
        log.append({"hotel_id": hotel_id, "url": final_url, "status": "image" if chosen else "no_valid_image", "notes": notes})
        if chosen:
            results[hotel_id] = chosen
        if i % 20 == 0:
            print(f"official {i}/{len(hotels)} images={len(results)} requests={net.requests}", flush=True)
    return results, log


# ---------------------------------------------------------------- assembly

METHOD = (
    "Фото собраны разовым офлайн-этапом подготовки данных; во время работы сервис ничего не скачивает. "
    "Уровень «building» — свободно лицензированное фото самого здания с Wikimedia Commons: изображение P18 из Wikidata "
    "по явной ссылке OSM, теги OSM image/wikimedia_commons или файлы и категории Commons в радиусе 150 м от точки отеля, "
    "в названии, описании или категории которых нормализованное имя отеля (с транслитерацией ru↔en, без общих слов "
    "«отель», «гостиница», «hotel», «апартаменты», «гостевой дом») совпадает с названием; берутся только CC0, PD, CC BY "
    "и CC BY-SA, автор, лицензия и страница файла сохраняются, миниатюра 800 px хранится локально в webp. Уровень "
    "«official_site» — крупное фото с собственного сайта отеля, его страниц галереи/номеров/об отеле на том же домене, что seed.website "
    "(не OTA, не агрегаторы, не соцсети; robots.txt соблюдается); сайт должен называть отель и Сочи/Адлер, общие сайты сетей "
    "без страницы конкретного объекта пропускаются; такие изображения не копируются в репозиторий — хранится только ссылка "
    "на файл и страницу, права остаются у отеля. Картинки проверены на размер (ширина от 480 px, пропорции 1,0–2,4) и "
    "отсутствие признаков логотипа; verified_by_eye=true означает ручной просмотр. Фото не доказывают вид из окна, этаж или "
    "состояние номера; отели без фото остаются без записи, интерфейс показывает схему. Фото «room» — вручную проверенные интерьеры гостевых номеров с того же домена, максимум восемь на отель; это не доказательство этажа или стороны. Счётчики counts — по лучшему уровню без room, отдельные room_hotel_count/room_photo_count считают номера."
)


def load_review(path):
    if not path or not path.exists():
        return {"accept": {}, "reject": []}
    review = json.loads(path.read_text())
    review.setdefault("accept", {})
    review.setdefault("reject", [])
    return review


def build(args):
    all_hotels = json.loads(args.hotels.read_text())
    hotels = all_hotels
    if args.only:
        hotels = [h for h in hotels if h["id"] in set(args.only)]
    net = Net(args.cache_dir, args.offline, args.refresh, args.read_cache_dir, preview_only=True)
    review = load_review(args.review)
    rejected = {(r["hotel_id"], r["kind"], r["ref"]) for r in review["reject"]}
    accepted = review["accept"]
    previous = json.loads(args.output.read_text()).get("photos", {}) if args.output.exists() else {}
    selected_ids = {h["id"] for h in hotels}
    photos = {h: rows for h, rows in previous.items() if h not in selected_ids}
    for hotel_id in selected_ids:
        # A scoped website expansion must never delete approved Commons files or legacy linked photos.
        kept = [r for r in previous.get(hotel_id, []) if (args.skip_commons and r["kind"] == "building")
                or r["kind"] in {"room", "street"}
                or (args.skip_official and r["kind"] == "official_site")
                or (args.expand_official and r["kind"] in {"official_site", "room", "street"})]
        if kept:
            photos[hotel_id] = kept
    report = {"building_candidates": {}, "official_log": []}

    building = building_candidates(hotels, net) if not args.skip_commons else {}
    args.photo_dir.mkdir(parents=True, exist_ok=True)
    for hotel in hotels:
        hotel_id = hotel["id"]
        rows = [r for r in building.get(hotel_id, []) if (hotel_id, "building", r["title"]) not in rejected]
        # The reviewer may pin the clearest of several correct files.
        rows.sort(key=lambda r: r["title"] != accepted.get(hotel_id, {}).get("building"))
        report["building_candidates"][hotel_id] = [{k: r[k] for k in ("title", "match", "match_text", "distance_m", "license")}
                                                   for r in building.get(hotel_id, [])]
        for row in rows[:3]:
            record = net.wiki_file(row["thumb_url"])
            data = net.body(record, "thumbs")
            if not record or record.get("status") != 200 or not data:
                continue
            webp, (width, height) = to_webp(data)
            target = args.photo_dir / (hotel_id + ".webp")
            target.write_bytes(webp)
            entry = {"kind": "building", "url": PUBLIC_PREFIX + hotel_id + ".webp", "local": True, "page_url": row["page_url"],
                     "title": row["title"].split(":", 1)[1], "author": row["author"], "license": row["license"],
                     "license_url": row["license_url"], "match": row["match"], "distance_m": row["distance_m"],
                     "width": width, "height": height, "match_text": row["match_text"],
                     "sha256": hashlib.sha256(webp).hexdigest(), "bytes": len(webp),
                     "verified_by_eye": accepted.get(hotel_id, {}).get("building") == row["title"]}
            photos.setdefault(hotel_id, []).append(entry)
            break

    if not args.skip_official and args.expand_official:
        from scripts.photo_collection import collect, contact_sheets, reviewed_rows

        candidates, log = collect(hotels, net, review, max_pages=args.max_pages, max_images=args.max_images)
        report["official_log"] = log
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        (args.cache_dir / "official_candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=1) + "\n")
        if args.qa_dir:
            unreviewed = {h: [r for r in rows if r["url"] != accepted.get(h, {}).get("official_site")
                             and r["url"] not in accepted.get(h, {}).get("room", [])] for h, rows in candidates.items()}
            contact_sheets(unreviewed, hotels, net, args.qa_dir)
        for hotel_id, rows in candidates.items():
            additions = reviewed_rows(hotel_id, rows, review) if args.require_review else rows
            current = photos.setdefault(hotel_id, [])
            existing = {(r["kind"], r["url"]) for r in current}
            current.extend(r for r in additions if (r["kind"], r["url"]) not in existing)
    elif not args.skip_official:
        official, log = official_candidates(hotels, net, rejected)
        report["official_log"] = log
        for hotel_id, entry in official.items():
            if (hotel_id, "official_site", entry["url"]) in rejected:
                continue
            entry["verified_by_eye"] = accepted.get(hotel_id, {}).get("official_site") == entry["url"]
            photos.setdefault(hotel_id, []).append(entry)

    if args.require_review:
        photos = {h: [e for e in rows if e["verified_by_eye"] and (
            e["kind"] not in {"official_site", "room"}
            or e["kind"] == "official_site" and accepted.get(h, {}).get("official_site") == e["url"]
            or e["kind"] == "room" and e["url"] in accepted.get(h, {}).get("room", []))] for h, rows in photos.items()}
    websites = {h["id"]: normalize_site(h.get("website")) for h in all_hotels}
    for hotel_id, rows in photos.items():
        valid = []
        for entry in rows:
            if entry["kind"] in {"official_site", "room"} and (
                not websites.get(hotel_id) or any(denied(url) or not image_host_ok(url, websites[hotel_id])
                                                for url in (entry["url"], entry["page_url"]))):
                report["official_log"].append({"hotel_id": hotel_id, "url": entry["url"], "status": "removed",
                                               "reason": "outside seed website domain"})
            else:
                valid.append(entry)
        photos[hotel_id] = valid
    rank = {"building": 0, "official_site": 1, "street": 2, "room": 3}
    photos = {h: sorted(rows, key=lambda r: rank[r["kind"]]) for h, rows in photos.items() if rows}
    for hotel_id, rows in photos.items():
        if len({r["url"] for r in rows}) != len(rows):
            raise ValueError(f"Duplicate cover/room image URL for {hotel_id}")
        rooms = [r for r in rows if r["kind"] == "room"]
        if len(rooms) > 8:
            raise ValueError(f"More than eight reviewed rooms for {hotel_id}")
    referenced = {h + ".webp" for h, rows in photos.items() if any(e["local"] for e in rows)}
    if not args.skip_commons and not args.only:
        for stale in args.photo_dir.glob("*.webp"):
            if stale.name not in referenced:
                stale.unlink()
    best = [next((r["kind"] for r in rows if r["kind"] != "room"), "none") for rows in photos.values()]
    counts = {kind: best.count(kind) for kind in ("building", "official_site", "street")}
    counts["none"] = len(all_hotels) - sum(counts.values())
    output = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "method": METHOD, "counts": counts,
              "room_hotel_count": sum(any(r["kind"] == "room" for r in rows) for rows in photos.values()),
              "room_photo_count": sum(r["kind"] == "room" for rows in photos.values() for r in rows),
              "photos": dict(sorted(photos.items()))}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.prune_qa_previews:
        from scripts.photo_collection import prune_reviewed_previews

        report["pruned_qa_previews"] = prune_reviewed_previews(net, review)
    (args.cache_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    local_bytes = sum(p.stat().st_size for p in args.photo_dir.glob("*.webp"))
    print(json.dumps({**counts, "local_files": len(referenced), "local_bytes": local_bytes, "requests": net.requests}))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotels", type=Path, default=SEED / "hotels.json")
    parser.add_argument("--output", type=Path, default=SEED / "hotel_photos.json")
    parser.add_argument("--photo-dir", type=Path, default=SEED / "photos/hotels")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/raw/hotel_photos")
    parser.add_argument("--read-cache-dir", type=Path, help="Optional read-only fallback response cache")
    parser.add_argument("--expand-official", action="store_true", help="Inspect bounded same-domain gallery/room/about links")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-images", type=int, default=16)
    parser.add_argument("--prune-qa-previews", action="store_true", help="Remove redundant previews only for explicitly reviewed URLs; keep contact sheets and metadata")
    parser.add_argument("--qa-dir", type=Path, help="Ignored directory for contact sheets; does not approve images")
    parser.add_argument("--review", type=Path, default=ROOT / "data/eval/hotel_photos_review.json")
    parser.add_argument("--offline", action="store_true", help="Use only cached responses; no network")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached responses and re-request")
    parser.add_argument("--require-review", action="store_true", help="Keep only entries accepted in the review file")
    parser.add_argument("--skip-commons", action="store_true")
    parser.add_argument("--skip-official", action="store_true")
    parser.add_argument("--only", nargs="*", help="Restrict to these hotel ids (debugging)")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
