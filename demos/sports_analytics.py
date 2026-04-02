#!/usr/bin/env python3
"""Sports odds arbitrage scanner — find guaranteed profit opportunities."""
import argparse
import json
import urllib.request

MOCK_DATA = [
    {"id": "1", "sport": "basketball", "home": "Lakers", "away": "Warriors",
     "bookmakers": [
         {"name": "DraftKings", "home": 2.10, "away": 1.85},
         {"name": "FanDuel", "home": 2.25, "away": 1.75},
         {"name": "BetMGM", "home": 2.05, "away": 1.90},
     ]},
    {"id": "2", "sport": "soccer", "home": "Arsenal", "away": "Chelsea",
     "bookmakers": [
         {"name": "DraftKings", "home": 1.80, "away": 4.50, "draw": 3.60},
         {"name": "FanDuel", "home": 1.95, "away": 4.20, "draw": 3.40},
         {"name": "Bet365", "home": 1.85, "away": 4.80, "draw": 3.50},
     ]},
    {"id": "3", "sport": "mma", "home": "Fighter A", "away": "Fighter B",
     "bookmakers": [
         {"name": "DraftKings", "home": 1.50, "away": 2.80},
         {"name": "FanDuel", "home": 1.45, "away": 3.10},
         {"name": "BetMGM", "home": 1.55, "away": 2.70},
     ]},
    {"id": "4", "sport": "basketball", "home": "Celtics", "away": "Bucks",
     "bookmakers": [
         {"name": "DraftKings", "home": 1.90, "away": 2.00},
         {"name": "FanDuel", "home": 2.05, "away": 1.85},
         {"name": "BetMGM", "home": 1.95, "away": 1.95},
     ]},
    {"id": "5", "sport": "soccer", "home": "Real Madrid", "away": "Barcelona",
     "bookmakers": [
         {"name": "DraftKings", "home": 2.40, "away": 2.90, "draw": 3.30},
         {"name": "FanDuel", "home": 2.55, "away": 2.80, "draw": 3.20},
         {"name": "Bet365", "home": 2.50, "away": 3.10, "draw": 3.10},
     ]},
]

def fetch_live(api_key, sport_filter=None):
    sport = sport_filter or "upcoming"
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds?apiKey={api_key}&regions=us,eu&markets=h2h&oddsFormat=decimal"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MantisAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        events = []
        for event in data:
            bms = []
            for bm in event.get("bookmakers", []):
                market = (bm.get("markets") or [{}])[0]
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                if outcomes:
                    bms.append({"name": bm["title"], **outcomes})
            if bms:
                events.append({
                    "sport": event.get("sport_key", ""),
                    "home": event.get("home_team", ""),
                    "away": event.get("away_team", ""),
                    "bookmakers": bms,
                })
        return events
    except Exception as e:
        print(f"\033[91mAPI error: {e}. Using mock data.\033[0m\n")
        return None

def find_arb(events, stake=100, min_arb=0):
    results = []
    for ev in events:
        outcomes = set()
        for bm in ev["bookmakers"]:
            outcomes.update(k for k in bm if k != "name")
        best = {}
        for outcome in outcomes:
            best_odds = 0
            best_bm = ""
            for bm in ev["bookmakers"]:
                odds = bm.get(outcome, 0)
                if isinstance(odds, (int, float)) and odds > best_odds:
                    best_odds = odds
                    best_bm = bm["name"]
            if best_odds > 0:
                best[outcome] = {"odds": best_odds, "bm": best_bm, "implied": 1 / best_odds}
        if not best:
            continue
        total_implied = sum(v["implied"] for v in best.values())
        arb_pct = (1 - total_implied) * 100
        alloc = {}
        profit = 0
        if total_implied < 1.0:
            for outcome, info in best.items():
                alloc[outcome] = round(stake * info["implied"] / total_implied, 2)
            profit = round(stake / total_implied - stake, 2)
        if arb_pct >= min_arb:
            results.append({
                "event": f"{ev['home']} vs {ev['away']}",
                "sport": ev.get("sport", ""),
                "best": best, "arb_pct": arb_pct,
                "stakes": alloc, "profit": profit, "is_arb": arb_pct > 0,
            })
    return sorted(results, key=lambda x: -x["arb_pct"])

def display(results, stake):
    print(f"\n\033[96m{'Event':<35} {'Sport':<12} {'Arb%':>6} {'Profit':>8} {'Best Odds & Books'}\033[0m")
    print("─" * 105)
    arb_count = 0
    best_arb = 0
    total_profit = 0
    for r in results:
        color = "92" if r["is_arb"] else "90"
        odds_str = " | ".join(f"{k}: {v['odds']:.2f} ({v['bm']})" for k, v in r["best"].items())
        profit_str = f"\033[92m${r['profit']:.2f}\033[0m" if r["profit"] > 0 else f"${r['profit']:.2f}"
        print(f"{r['event']:<35} {r['sport']:<12} \033[{color}m{r['arb_pct']:>5.2f}%\033[0m {profit_str:>8}  {odds_str}")
        if r["is_arb"]:
            arb_count += 1
            total_profit += r["profit"]
            best_arb = max(best_arb, r["arb_pct"])
            print(f"  \033[93m  Stakes: {r['stakes']} (total ${stake})\033[0m")
    print("─" * 105)
    print(f"\033[96mScanned: {len(results)} | Arb: {arb_count} | Best: {best_arb:.2f}% | Profit: ${total_profit:.2f}\033[0m\n")

def main():
    parser = argparse.ArgumentParser(description="Sports odds arbitrage scanner")
    parser.add_argument("--api-key", default=None, help="The Odds API key")
    parser.add_argument("--sport", default=None, help="Filter by sport")
    parser.add_argument("--stake", type=float, default=100, help="Stake amount")
    parser.add_argument("--min-arb", type=float, default=0, help="Min arb %")
    args = parser.parse_args()
    print("\033[95m╔══════════════════════════════════════════╗\033[0m")
    print("\033[95m║   MantisAI — Sports Arbitrage Scanner    ║\033[0m")
    print("\033[95m╚══════════════════════════════════════════╝\033[0m")
    events = None
    if args.api_key:
        events = fetch_live(args.api_key, args.sport)
    if events is None:
        print("\033[93mUsing mock data (pass --api-key for live odds)\033[0m")
        events = MOCK_DATA
        if args.sport:
            events = [e for e in events if e["sport"] == args.sport]
    results = find_arb(events, args.stake, args.min_arb)
    display(results, args.stake)

if __name__ == "__main__":
    main()
