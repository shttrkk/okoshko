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


def fetch(client, url):
    for _ in range(4):
        if not allowed_host(url):
            raise ValueError("OTA or unsupported URL rejected")
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
            if match and not re.search(r"\b(?:не|нет|без)\b", low[max(0,match.start()-20):match.end()+20]):
                field, value = "soundproofing", {"available": True}
        if field is None and re.search(r"животн|питомц|собак|кошк", low):
            match = re.search(r"животн|питомц|собак|кошк", low)
            if re.search(r"запрещ|не допуска|не разреш", low):
                field, value = "pets", {"available": False}
            elif re.search(r"разреш|допуска|возможн.{0,35}проживан|проживан.{0,35}с\s+", low):
                field, value = "pets", {"available": True}
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hotels", type=Path, required=True)
    parser.add_argument("--max-hotels", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval"))
    args = parser.parse_args()
    hotels = json.loads(args.hotels.read_text())
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    evidence_dir = output/"website_evidence"
    evidence_dir.mkdir(exist_ok=True)
    raw_dir = Path("data/raw/website_snapshots")
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
                queue = [website]
                processed = set()
                while queue and len(processed)<2:
                    url = queue.pop(0)
                    if url in processed or not robots.can_fetch(USER_AGENT, url):
                        continue
                    processed.add(url)
                    time.sleep(.2)
                    response = fetch(client, url)
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
                                    "html_sha256":digest,"robots_url":base+"/robots.txt","method":"exact_text_excerpt_v1","label_type":"weak_website"}
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

