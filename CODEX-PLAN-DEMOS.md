# MantisAI Demos — Codex Build Plan

## Context
MantisAI is at /root/mantis-ai. It's a Python agent OS (2,813 lines, 20 tests passing).
We need 3 standalone demo scripts that prove the engine works with real money-making use cases.
Each demo must be runnable in under 5 minutes with `python demos/<name>.py`.

## Demo 1: Prediction Market Bot
**File:** `demos/prediction_market.py`

Build a standalone script that:
1. Connects to Polymarket API (public, no auth needed) at https://clob.polymarket.com
2. Fetches trending markets with `GET /markets?limit=20&order=volume&ascending=false`
3. For each market, calculates Expected Value: `EV = (estimated_prob * payout) - price`
4. Finds mispriced contracts where `abs(EV) > 0.05`
5. Calculates Kelly criterion sizing: `kelly = (bp - q) / b` where b=payout, p=est_prob, q=1-p
6. Outputs a formatted table with: market name, current price, estimated edge, kelly size, recommendation (BUY/SELL/SKIP)

Requirements:
- Use only `urllib.request` and `json` (no pip install needed)
- Add colorful terminal output with ANSI codes
- Include a `--live` flag that refreshes every 60 seconds
- Print total portfolio EV at the bottom
- Handle API errors gracefully

## Demo 2: AI Lead Gen Agent
**File:** `demos/lead_gen.py`

Build a standalone script that:
1. Takes an Ideal Customer Profile as CLI arg: `python demos/lead_gen.py "SaaS founders, Series A, AI/ML focus"`
2. Uses DuckDuckGo search (no API key needed) via `urllib.request` to `https://html.duckduckgo.com/html/?q={query}`
3. Parses HTML results to extract company names, URLs, descriptions
4. For each lead (top 10), scrapes their homepage title/description
5. Generates a personalized outreach draft using a template system
6. Outputs everything as a formatted report + saves to `leads_output.json`

Requirements:
- Use only stdlib (urllib, html.parser, json, re)
- Add `--export csv` flag for CSV output
- Include outreach email templates with personalization placeholders
- Colorful terminal output

## Demo 3: Sports Analytics Dashboard
**File:** `demos/sports_analytics.py`

Build a standalone script that:
1. Fetches live sports odds from The Odds API (free tier, 500 requests/month)
   - Base URL: `https://api.the-odds-api.com/v4/sports/upcoming/odds`
   - Requires API key (free signup) passed as `--api-key` or `ODDS_API_KEY` env var
2. Compares odds across bookmakers to find arbitrage opportunities
3. Calculates implied probabilities from odds: `implied_prob = 1 / decimal_odds`
4. Finds +EV bets where sum of implied probs < 1.0 (arbitrage exists)
5. Calculates optimal stake allocation for guaranteed profit
6. Outputs: event, bookmakers, odds comparison, arb %, profit per $100 staked

Requirements:
- Use only `urllib.request` and `json`
- Fallback to mock data if no API key provided (so demo always runs)
- Add `--sport` flag to filter (basketball, soccer, mma, etc.)
- Colorful terminal output with profit highlighted in green

## Shared Requirements
- Each demo must have a `if __name__ == "__main__"` block
- Each demo must work with `python demos/<name>.py` (no pip install)
- Add a `demos/README.md` with quick-start for all 3
- Use argparse for CLI args
- Include error handling for network failures
- Each file should be 150-300 lines max

## Test
After building all 3, run:
```bash
python demos/prediction_market.py
python demos/lead_gen.py "AI startups San Francisco"
python demos/sports_analytics.py  # uses mock data
```
All 3 must execute without errors.
