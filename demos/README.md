# MantisAI Demos

Three standalone scripts proving MantisAI's engine works with real money-making use cases. Zero dependencies — just Python stdlib.

## Prediction Market Scanner

Scan Polymarket for mispriced contracts, calculate edge and Kelly sizing.

```bash
python demos/prediction_market.py              # scan top 20 markets
python demos/prediction_market.py --limit 50   # more markets
python demos/prediction_market.py --live       # refresh every 60s
```

## Sports Arbitrage Scanner

Find guaranteed-profit arbitrage across bookmakers.

```bash
python demos/sports_analytics.py                        # mock data demo
python demos/sports_analytics.py --api-key YOUR_KEY     # live odds (free at theoddsapi.com)
python demos/sports_analytics.py --sport basketball     # filter by sport
python demos/sports_analytics.py --stake 500            # custom stake
```

## Lead Generation Agent

Find, research, and draft outreach for prospects matching your ICP.

```bash
python demos/lead_gen.py "AI startups San Francisco"
python demos/lead_gen.py "SaaS founders Series A" --limit 20
python demos/lead_gen.py "fintech companies NYC" --export json
python demos/lead_gen.py "developer tools" --export csv
```
