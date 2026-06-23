"""CLI-Einstieg für Analyse und Empfehlungen."""
import argparse
import json

from analyzer import portfolio, signals
from analyzer.portfolio import evaluate_portfolio


def main():
    parser = argparse.ArgumentParser(description="Trading Bot Analyse")
    parser.add_argument("--portfolio", action="store_true", help="Depot bewerten")
    parser.add_argument("--recommend", action="store_true", help="Empfehlungen generieren")
    parser.add_argument("--buy", type=str, help="Symbol kaufen")
    parser.add_argument("--sell", type=str, help="Symbol verkaufen")
    parser.add_argument("--amount", type=float, default=None, help="Betrag in EUR")
    args = parser.parse_args()

    if args.portfolio:
        p, alerts = evaluate_portfolio()
        print(json.dumps(p, indent=2, default=str))
        if alerts:
            print("\n--- Alerts ---")
            for a in alerts:
                print(a)

    if args.recommend:
        recs = signals.generate_recommendations()
        print(json.dumps(recs, indent=2, default=str))

    if args.buy:
        res = portfolio.buy(args.buy, amount_eur=args.amount)
        print(json.dumps(res, indent=2, default=str))

    if args.sell:
        res = portfolio.sell(args.sell)
        print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
