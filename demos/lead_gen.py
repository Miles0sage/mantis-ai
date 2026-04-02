#!/usr/bin/env python3
"""AI lead generation agent — find and research prospects."""
import argparse
import csv
import json
import re
import urllib.request
import urllib.parse
from html.parser import HTMLParser

class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_title = False
        self._in_snippet = False
        self._current = {}
        self._text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if "result__title" in cls:
            self._in_title = True
            self._text = ""
        elif "result__snippet" in cls:
            self._in_snippet = True
            self._text = ""
        elif tag == "a" and self._in_title:
            href = attrs_dict.get("href", "")
            if "uddg=" in href:
                match = re.search(r'uddg=([^&]+)', href)
                if match:
                    self._current["url"] = urllib.parse.unquote(match.group(1))
            elif href.startswith("http"):
                self._current["url"] = href

    def handle_data(self, data):
        if self._in_title or self._in_snippet:
            self._text += data

    def handle_endtag(self, tag):
        if self._in_title and tag in ("a", "h2", "span"):
            self._current["title"] = self._text.strip()
            self._in_title = False
        elif self._in_snippet and tag in ("td", "div", "span"):
            self._current["snippet"] = self._text.strip()
            self._in_snippet = False
            if self._current.get("title") and self._current.get("url"):
                self.results.append(dict(self._current))
                self._current = {}

def search_ddg(query, limit=10):
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        parser = SearchParser()
        parser.feed(html)
        return parser.results[:limit]
    except Exception as e:
        print(f"\033[91mSearch error: {e}\033[0m")
        return []

def fetch_meta(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read(8000).decode("utf-8", errors="ignore")
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)
        return {
            "title": title.group(1).strip() if title else "",
            "description": desc.group(1).strip() if desc else "",
        }
    except Exception:
        return {"title": "", "description": ""}

def score_lead(lead, icp_keywords):
    text = f"{lead.get('title', '')} {lead.get('snippet', '')} {lead.get('meta_title', '')} {lead.get('meta_desc', '')}".lower()
    matches = sum(1 for kw in icp_keywords if kw.lower() in text)
    return min(10, int(matches / max(len(icp_keywords), 1) * 10) + 3)

def generate_outreach(lead):
    company = lead.get("title", "").split(" - ")[0].split(" | ")[0][:40]
    return (
        f"Hi {company} team,\n\n"
        f"I came across {company} while researching {lead.get('snippet', '')[:50]}... "
        f"and I think there's a strong fit.\n\n"
        f"We're building AI automation tools that could help you scale faster. "
        f"Would you be open to a quick 15-min call this week?\n\n"
        f"Best,\nMiles"
    )

def display(leads):
    print(f"\n\033[96m{'#':>3} {'Score':>5} {'Company':<40} {'URL'}\033[0m")
    print("─" * 100)
    for i, lead in enumerate(leads, 1):
        score = lead["score"]
        color = "92" if score >= 7 else "93" if score >= 5 else "90"
        company = lead.get("title", "")[:38]
        url = lead.get("url", "")[:48]
        print(f"{i:>3} \033[{color}m{score:>5}/10\033[0m {company:<40} {url}")
        if lead.get("snippet"):
            print(f"    \033[90m{lead['snippet'][:90]}\033[0m")
    print("─" * 100)

def main():
    parser = argparse.ArgumentParser(description="AI lead generation agent")
    parser.add_argument("icp", help="Ideal Customer Profile description")
    parser.add_argument("--limit", type=int, default=10, help="Number of leads")
    parser.add_argument("--export", choices=["csv", "json"], help="Export format")
    args = parser.parse_args()

    print("\033[95m╔══════════════════════════════════════════╗\033[0m")
    print("\033[95m║     MantisAI — Lead Generation Agent     ║\033[0m")
    print("\033[95m╚══════════════════════════════════════════╝\033[0m")
    print(f"\n\033[93mICP:\033[0m {args.icp}\n")

    icp_keywords = [w for w in args.icp.split() if len(w) > 2]
    results = search_ddg(args.icp, args.limit)
    print(f"\033[90mFound {len(results)} results, enriching...\033[0m")

    leads = []
    for r in results:
        meta = fetch_meta(r.get("url", ""))
        lead = {**r, "meta_title": meta["title"], "meta_desc": meta["description"]}
        lead["score"] = score_lead(lead, icp_keywords)
        lead["outreach"] = generate_outreach(lead)
        leads.append(lead)

    leads.sort(key=lambda x: -x["score"])
    display(leads)

    if leads:
        print(f"\n\033[96mTop Lead Outreach Draft:\033[0m")
        print(f"\033[93m{leads[0]['outreach']}\033[0m\n")

    if args.export == "json":
        with open("leads_output.json", "w") as f:
            json.dump(leads, f, indent=2)
        print(f"\033[92mSaved to leads_output.json\033[0m")
    elif args.export == "csv":
        with open("leads_output.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["title", "url", "score", "snippet"])
            w.writeheader()
            for l in leads:
                w.writerow({k: l.get(k, "") for k in ["title", "url", "score", "snippet"]})
        print(f"\033[92mSaved to leads_output.csv\033[0m")

if __name__ == "__main__":
    main()
