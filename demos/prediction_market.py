#!/usr/bin/env python3
"""Polymarket prediction market scanner — find mispriced contracts."""
import argparse
import json
import time
import urllib.request

def fetch_markets(limit=20):
    url = f"https://gamma-api.polymarket.com/markets?limit={limit}&order=volume&ascending=false&active=true&closed=false"
    req = urllib.request.Request(url, headers={"User-Agent": "MantisAI/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"\033[91mAPI error: {e}\033[0m")
        return []

def analyze(markets):
    results = []
    for m in markets:
        question = m.get("question", "")
        prices_raw = m.get("outcomePrices", "[]")
        outcomes = m.get("outcomes", "[]")
        volume = float(m.get("volumeNum", 0) or m.get("volume", 0) or 0)

        # Parse JSON string arrays
        if isinstance(prices_raw, str):
            try:
                prices = [float(p) for p in json.loads(prices_raw)]
            except (json.JSONDecodeError, ValueError):
                continue
        elif isinstance(prices_raw, list):
            prices = [float(p) for p in prices_raw]
        else:
            continue

        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = []

        if len(prices) < 2:
            continue

        yes_price = prices[0]
        no_price = prices[1]
        total = yes_price + no_price
        if total == 0:
            continue

        spread = abs(1.0 - total)
        edge = spread  # mispricing = deviation from 1.0

        # Kelly: f = edge / (payout - 1) simplified
        kelly = edge / max(total, 0.01)

        if edge > 0.01:
            rec = "\033[92mBUY\033[0m" if edge > 0.05 else "\033[93mWATCH\033[0m"
        else:
            rec = "\033[90mSKIP\033[0m"

        results.append({
            "question": question[:58],
            "outcomes": outcomes[:2],
            "yes": yes_price,
            "no": no_price,
            "spread": spread,
            "edge": edge,
            "kelly": kelly,
            "volume": volume,
            "rec": rec,
        })
    return results

def display(results):
    print(f"\n\033[96m{'Question':<60} {'Yes':>6} {'No':>6} {'Spread':>7} {'Edge%':>7} {'Kelly%':>7} {'Rec':>6}\033[0m")
    print("─" * 105)
    opps = 0
    best_edge = 0
    for r in results:
        edge_pct = r["edge"] * 100
        kelly_pct = r["kelly"] * 100
        if edge_pct > 1:
            opps += 1
        best_edge = max(best_edge, edge_pct)
        edge_color = "92" if edge_pct > 5 else "93" if edge_pct > 2 else "0"
        print(f"{r['question']:<60} {r['yes']:>6.3f} {r['no']:>6.3f} {r['spread']:>7.3f} "
              f"\033[{edge_color}m{edge_pct:>6.2f}%\033[0m {kelly_pct:>6.2f}% {r['rec']}")

    print("─" * 105)
    print(f"\033[96mScanned: {len(results)} markets | Opportunities: {opps} | Best edge: {best_edge:.2f}%\033[0m\n")

def main():
    parser = argparse.ArgumentParser(description="Polymarket prediction market scanner")
    parser.add_argument("--limit", type=int, default=20, help="Number of markets to scan")
    parser.add_argument("--live", action="store_true", help="Refresh every 60 seconds")
    args = parser.parse_args()

    print("\033[95m╔══════════════════════════════════════════╗\033[0m")
    print("\033[95m║   MantisAI — Prediction Market Scanner   ║\033[0m")
    print("\033[95m╚══════════════════════════════════════════╝\033[0m")

    while True:
        markets = fetch_markets(args.limit)
        if markets:
            results = analyze(markets)
            display(results)
        else:
            print("\033[91mNo markets fetched. Retrying...\033[0m")

        if not args.live:
            break
        print(f"\033[90mRefreshing in 60s... (Ctrl+C to stop)\033[0m")
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            print("\n\033[90mStopped.\033[0m")
            break

if __name__ == "__main__":
    main()
