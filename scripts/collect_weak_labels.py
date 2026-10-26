"""Collect short, exact weak labels from official hotel websites, respecting robots.

No OTA requests, no negative labels from absence, one <=25-word quote per page.
Raw HTML/text stay in gitignored data/raw; only quoted evidence enters the repo.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from backend.contracts import Claim, Evidence

USER_AGENT = "OknoHackathonResearch/1.0 (non-commercial hotel fact verification)"
BLOCKED = {"booking.com", "ostrovok.ru", "zenhotels.com", "expedia.com", "hotels.com", "agoda.com", "tripadvisor.com",
           "101hotels.com", "tvil.ru", "sutochno.ru", "yandex.ru", "travel.yandex.ru", "2gis.ru", "otello.ru", "bronevik.com"}


def allowed_host(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and host and not any(host == blocked or host.endswith("."+blocked) for blocked in BLOCKED)


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)
        if tag in {"p", "br", "div", "h1", "h2", "h3", "li"} and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip = max(0, self.skip-1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return "\n".join(re.sub(r"\s+", " ", line).strip() for line in "".join(self.parts).splitlines() if line.strip())


def fetch(client, url, robots_cache=None):
    for _ in range(4):
        if not allowed_host(url):
            raise ValueError("OTA or unsupported URL rejected")
        if robots_cache is not None:
            origin = urlsplit(url).scheme+"://"+urlsplit(url).netloc
            if origin not in robots_cache:
                response = fetch(client, origin+"/robots.txt")
                policy = RobotFileParser()
                if response.status_code == 200:
                    policy.parse(response.text.splitlines())
                elif response.status_code in {404, 410}:
                    policy.parse([])
                else:
                    raise ValueError("Redirect destination robots policy unavailable")
                robots_cache[origin] = policy
            if not robots_cache[origin].can_fetch(USER_AGENT, url):
                raise ValueError("Robots policy forbids URL")
        response = client.get(url, follow_redirects=False)
        if response.is_redirect:
            url = urljoin(url, response.headers["location"])
            continue
        return response
    raise ValueError("Too many redirects")


def extract(text):
    for sentence in re.split(r"[\n.!?]+", text):
        sentence = sentence.strip()
        if len(sentence) < 12:
            continue
        low = sentence.lower()
        field, value, match = None, None, None
        if "мор" in low and "вид" in low:
            match = re.search(r"вид[а-яё\s,\-]{0,65}(?:мор[еяю]|море)", low)
            if match and not re.search(r"\b(?:не|нет|без)\b", low[max(0,match.start()-20):match.end()]):
                field, value = "sea_view", {"available": True}
        if field is None:
            match = re.search(r"(?:звукоизол|шумоизол)[а-яё]*", low)
            if match and not re.search(r"\b(?:не|нет|без)\b|плох|слаб|низк|посредствен", low[max(0,match.start()-20):match.end()+25]):
                field, value = "soundproofing", {"available": True}
        if field is None and re.search(r"животн|питомц|собак|кошк", low):
            match = re.search(r"животн|питомц|собак|кошк", low)
            if re.search(r"запрещ|не допуска|не разреш", low):
                field, value = "pets", {"available": False}
            elif re.search(r"разреш|допуска|можно|принима|доступн|возможн.{0,35}проживан|проживан.{0,35}возможн|создал.{0,60}услов", low):
                field, value = "pets", {"available": True}
        if field is None and re.search(r"(?:пляж|мор[яе])", low):
            match = re.search(r"(?:пляж|мор[яе])", low)
            if re.search(r"(?:до|от|в)\s+(?:[а-яё]+\s+){0,3}(?:пляж|мор)|(?:пляж|мор)[^\n]{0,45}\d+\s*м", low) and re.search(r"\d+\s*(?:м\b|метр)", low):
                field, value = "beach_walk", {"available": True, "minutes": None}
        if field is None and re.search(r"парковк", low):
            match = re.search(r"парковк", low)
            if re.search(r"собственн|бесплатн|охраняем|для гостей.{0,25}территор|постояльц.{0,45}парков", low) and not re.search(r"\?|нет\s+парков|без\s+парков", low):
                field, value = "car_access", {"available": None, "parking_available": True}
        if field is None and re.search(r"номер[^\n]{0,55}(?:с\s+балкон|балконом)", low):
            match = re.search(r"балкон", low)
            field, value = "room_orientation", {"available": True, "balcony": True}
        if field and match:
            words = list(re.finditer(r"\S+", sentence))
            index = next((i for i,w in enumerate(words) if w.end()>match.start()), 0)
            start = max(0, index-7)
            end = min(len(words), start+25)
            quote = sentence[words[start].start():words[end-1].end()]
            # The predicate must actually appear in the retained exact quote.
            if field == "sea_view" and not ("вид" in quote.lower() and "мор" in quote.lower()):
                continue
            return field, value, quote
    return None


LABEL_FIELDS = ["id", "hotel_id", "characteristic_id", "scope", "value", "label_type", "evidence_url", "quote", "labeler", "observed_at"]


def curated_records(manifest, output, raw_dir, require_raw=False):
    """Rebuild only explicitly reviewed excerpts; no network or new inference."""
    labels, claims, urls, hotels = [], [], {}, set()
    for entry in manifest["accepted"]:
        id = entry["id"]
        artifact = json.loads((output/"website_evidence"/(id+".json")).read_text())
        quote, url = artifact["quote"], artifact["url"]
        assert 0 < len(quote.split()) <= 25
        expected = hashlib.sha256((entry["hotel_id"]+entry["characteristic_id"]+url+quote).encode()).hexdigest()[:24]
        assert id == expected and artifact["id"] == id and artifact["hotel_id"] == entry["hotel_id"]
        assert url not in urls, "Only one accepted excerpt per page"
        urls[url] = quote
        assert entry["hotel_id"] not in hotels, "One curated label per canonical hotel"
        hotels.add(entry["hotel_id"])
        raw = raw_dir/(artifact["html_sha256"]+".txt")
        html = raw.with_suffix(".html")
        if require_raw or raw.exists():
            assert quote in raw.read_text(), id
            assert hashlib.sha256(html.read_bytes()).hexdigest() == artifact["html_sha256"], id
        observed = datetime.fromisoformat(entry["observed_at"].replace("Z", "+00:00"))
        scope, value = entry["scope"], entry["value"]
        labels.append({"id": id, "hotel_id": entry["hotel_id"], "characteristic_id": entry["characteristic_id"],
                       "scope": json.dumps(scope, ensure_ascii=False), "value": json.dumps(value, ensure_ascii=False),
                       "label_type": "weak_website", "evidence_url": url, "quote": quote,
                       "labeler": entry["labeler"], "observed_at": entry["observed_at"]})
        claim = Claim(id="website-"+id, hotel_id=entry["hotel_id"], characteristic_id=entry["characteristic_id"],
                      scope=scope, value=value, source="hotel_website", method="exact_quote_v1", confidence=.55,
                      observed_at=observed, valid_until=observed+timedelta(days=180),
                      evidence=[Evidence(id="ev-website-"+id, kind="quote", label="Проверенная короткая цитата с официального сайта",
                                         source_ids=["hotel_website"], url=url, artifact="/data/eval/website_evidence/"+id+".json", quote=quote,
                                         inputs={"html_sha256": artifact["html_sha256"], "curation_manifest": "/data/eval/curation.json",
                                                 "universal_scope": False, "label_semantics": entry["semantics"]},
                                         output=value, limitations=entry["limitations"])])
        claims.append(claim.model_dump(mode="json"))
    assert len(labels) == manifest["counts"]["accepted_labels"]
    assert len(hotels) == manifest["counts"]["unique_hotels"]
    return labels, claims


def curate(path, output, raw_dir, *, validate_only=False, require_raw=False):
    manifest = json.loads(path.read_text())
    labels, claims = curated_records(manifest, output, raw_dir, require_raw)
    if validate_only:
        with (output/"labels.csv").open(newline="") as file:
            assert list(csv.DictReader(file)) == labels
        assert [json.loads(line) for line in (output/"website_claims.jsonl").read_text().splitlines() if line.strip()] == claims
        assert {p.stem for p in (output/"website_evidence").glob("*.json")} == {row["id"] for row in labels}
    else:
        with (output/"labels.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=LABEL_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(labels)
        (output/"website_claims.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False)+"\n" for c in claims))
    print(json.dumps({**manifest["counts"], "validated": True, "raw_verified": require_raw}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hotels", type=Path)
    parser.add_argument("--max-hotels", type=int, default=100)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--curate", type=Path, help="Rebuild reviewed labels/claims from a curation manifest, without network")
    parser.add_argument("--validate-curation", type=Path, help="Check reviewed labels/claims/artifacts without writing")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/website_snapshots"))
    parser.add_argument("--require-raw", action="store_true")
    args = parser.parse_args()
    if args.curate or args.validate_curation:
        curate(args.curate or args.validate_curation, args.output_dir or Path("data/eval"), args.raw_dir,
               validate_only=bool(args.validate_curation), require_raw=args.require_raw)
        return
    if args.hotels is None:
        parser.error("--hotels is required for candidate collection")
    hotels = json.loads(args.hotels.read_text())
    output = args.output_dir or Path("data/raw/website_candidates")
    if output.resolve() == Path("data/eval").resolve():
        parser.error("Collect candidates outside data/eval; reviewed data are written only with --curate")
    output.mkdir(parents=True, exist_ok=True)
    evidence_dir = output/"website_evidence"
    evidence_dir.mkdir(exist_ok=True)
    raw_dir = args.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    observed = datetime.now(UTC)
    seen, labels, claims, logs = set(), [], [], []
    with httpx.Client(timeout=httpx.Timeout(8), headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=1)) as client:
        for hotel in hotels:
            website = hotel.get("website")
            if not website or not allowed_host(website):
                continue
            host = urlsplit(website).netloc.lower()
            if host in seen or len(seen) >= args.max_hotels:
                continue
            seen.add(host)
            base = urlsplit(website).scheme+"://"+urlsplit(website).netloc
            try:
                robots_response = fetch(client, base+"/robots.txt")
                robots = RobotFileParser()
                if robots_response.status_code == 200:
                    robots.parse(robots_response.text.splitlines())
                elif robots_response.status_code in {404, 410}:
                    robots.parse([])
                else:
                    logs.append({"hotel_id": hotel["id"], "url": website, "status": "robots_unavailable", "http_status": robots_response.status_code})
                    continue
                robots_cache = {base: robots}
                queue = [website]
                processed = set()
                while queue and len(processed)<2:
                    url = queue.pop(0)
                    if url in processed or not robots.can_fetch(USER_AGENT, url):
                        continue
                    processed.add(url)
                    time.sleep(.2)
                    response = fetch(client, url, robots_cache)
                    final_url = str(response.url)
                    if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                        logs.append({"hotel_id":hotel["id"], "url":url, "status":"not_html", "http_status":response.status_code})
                        continue
                    document = Text()
                    document.feed(response.text)
                    text = document.text()
                    digest = hashlib.sha256(response.content).hexdigest()
                    (raw_dir/(digest+".html")).write_bytes(response.content)
                    (raw_dir/(digest+".txt")).write_text(text)
                    item = extract(text)
                    logs.append({"hotel_id":hotel["id"],"url":final_url,"status":"label" if item else "no_supported_quote","sha256":digest,"robots_url":base+"/robots.txt"})
                    if item:
                        characteristic, value, quote = item
                        assert quote in text and len(quote.split())<=25
                        id = hashlib.sha256((hotel["id"]+characteristic+final_url+quote).encode()).hexdigest()[:24]
                        artifact = {"id":id,"hotel_id":hotel["id"],"url":final_url,"quote":quote,"retrieved_at":observed.isoformat(),
                                    "html_sha256":digest,"robots_url":base+"/robots.txt","method":"exact_text_excerpt_v1","label_type":"weak_website",
                                    "review_status":"candidate_unreviewed"}
                        (evidence_dir/(id+".json")).write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+"\n")
                        labels.append({"id":id,"hotel_id":hotel["id"],"characteristic_id":characteristic,"scope":"{}","value":json.dumps(value),
                                       "label_type":"weak_website","evidence_url":final_url,"quote":quote,"labeler":"rule_extraction_v1","observed_at":observed.isoformat()})
                        claim = Claim(id="website-"+id,hotel_id=hotel["id"],characteristic_id=characteristic,value=value,source="hotel_website",method="exact_quote_v1",
                            evidence=[Evidence(id="ev-website-"+id,kind="quote",label="Короткая цитата с официального сайта отеля",source_ids=["hotel_website"],
                                url=final_url,artifact="/data/eval/website_evidence/"+id+".json",quote=quote,
                                inputs={"html_sha256":digest,"universal_scope":bool(re.search(r"все\s+номер",quote.lower()))},
                                limitations=["Слабая метка из описания отеля; реальный вид и распределение по номерам требуют проверки"] )],
                            confidence=.55, observed_at=observed,valid_until=observed+timedelta(days=180))
                        claims.append(claim.model_dump(mode="json"))
                    # At most one same-origin room-description page after the homepage.
                    for link in document.links:
                        absolute = urljoin(final_url, link).split("#")[0]
                        if urlsplit(absolute).netloc == urlsplit(final_url).netloc and re.search(r"room|nomer|accommodation|razmesh",urlsplit(absolute).path,re.I) and absolute not in processed:
                            queue.append(absolute)
                            break
            except (httpx.HTTPError, ValueError, KeyError) as error:
                logs.append({"hotel_id":hotel["id"],"url":website,"status":"unavailable","error":type(error).__name__})
            print(f"websites={len(seen)} labels={len(labels)}",flush=True)
    with (output/"website_labels.csv").open("w",encoding="utf-8",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=["id","hotel_id","characteristic_id","scope","value","label_type","evidence_url","quote","labeler","observed_at"])
        writer.writeheader()
        writer.writerows(labels)
    (output/"website_claims.jsonl").write_text("".join(json.dumps(c,ensure_ascii=False)+"\n" for c in claims))
    (output/"website_fetch_log.jsonl").write_text("".join(json.dumps(c,ensure_ascii=False)+"\n" for c in logs))
    print(json.dumps({"websites_checked":len(seen),"labels":len(labels),"hotels_with_labels":len({label['hotel_id'] for label in labels})}))


if __name__ == "__main__":
    main()

