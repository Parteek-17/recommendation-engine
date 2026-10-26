"""Run the whole synthetic data generator.

    python -m data_gen.generate --size small        # 1,000 users, ~80k events, < 1 minute
    python -m data_gen.generate --size full         # 10,000 users, ~1M events
    python -m data_gen.generate --size small --no-db --seed 7

Steps: catalog -> relations -> users -> offers + consent -> simulation -> orders + reviews
-> write CSVs to data/raw/ (ground truth to data/ground_truth/) -> validate -> load into the DB.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT, settings
from data_gen import config as C
from data_gen.catalog import build_catalog
from data_gen.offers import build_consent, build_offers
from data_gen.orders import build_orders, build_reviews
from data_gen.relations import build_relations
from data_gen.simulate import Simulator
from data_gen.users import build_users
from data_gen.utils import make_rngs, write_ground_truth, write_tables

RAW_DIR = PROJECT_ROOT / "data" / "raw"
GROUND_TRUTH_DIR = PROJECT_ROOT / "data" / "ground_truth"


def generate(size: str = "small", seed: int = C.SEED, n_users: int | None = None,
             n_products: int | None = None, verbose: bool = True) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build every table in memory. Returns (tables, ground_truth)."""
    spec = dict(C.SIZES[size])
    spec["users"] = n_users or spec["users"]
    spec["products"] = n_products or spec["products"]
    rngs = make_rngs(seed)
    started = time.time()

    def step(msg: str) -> None:
        if verbose:
            print(f"  [{time.time() - started:5.1f}s] {msg}", flush=True)

    step(f"catalog: {spec['products']} products")
    categories, products = build_catalog(spec["products"], rngs["catalog"])
    step("product relations (ground truth)")
    relations = build_relations(products, rngs["relations"])
    step(f"users: {spec['users']}")
    users, hidden = build_users(spec["users"], categories, products, rngs["users"], seed)
    step("offers + consent")
    offers = build_offers(categories, products, relations, rngs["offers"])
    consent = build_consent(users, hidden, rngs["consent"])
    step("simulating behaviour day by day (the slow part)")
    sim = Simulator(products, categories, users, hidden, relations, offers, consent, spec["activity_scale"],
                    rngs["simulate"]).run()
    step(f"orders + reviews from {len(sim.purchases):,} purchases")
    orders, order_items = build_orders(sim.purchases)
    reviews = build_reviews(order_items, products, rngs["reviews"])

    tables = {
        "users": users, "categories": categories, "products": products, "sessions": sim.sessions,
        "events": sim.events, "orders": orders, "order_items": order_items, "reviews": reviews,
        "offers": offers, "offer_sends": sim.offer_sends, "user_consent": consent,
        # Created empty on purpose: filled later by the rec engine / bot / pipeline.
        "offer_rankings": None, "telegram_links": None, "audit_log": None, "id_map": None,
    }
    step(f"done: {len(sim.events):,} events, {len(sim.sessions):,} sessions, {len(orders):,} orders")
    return tables, {"user_hidden_profile": hidden, "product_relations": relations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic marketplace dataset.")
    parser.add_argument("--size", choices=list(C.SIZES), default="small")
    parser.add_argument("--seed", type=int, default=C.SEED)
    parser.add_argument("--out", type=Path, default=RAW_DIR, help="folder for the Standard Format CSVs")
    parser.add_argument("--ground-truth", type=Path, default=GROUND_TRUTH_DIR, help="folder for ground truth")
    parser.add_argument("--no-db", action="store_true", help="only write CSVs, don't load them into the DB")
    parser.add_argument("--db", type=Path, default=None, help="DB file to load into (default: DB_PATH in .env)")
    args = parser.parse_args(argv)

    out, gt = args.out.resolve(), args.ground_truth.resolve()
    if out == gt or gt.is_relative_to(out):
        print("Ground truth must NOT be saved inside the raw data folder (models would see it).")
        return 2

    print(f"Generating '{args.size}' dataset (seed {args.seed}) ...")
    tables, truth = generate(args.size, args.seed)
    write_tables(tables, out)
    write_ground_truth(truth["user_hidden_profile"], truth["product_relations"], gt)
    print(f"CSVs written to {out}\nGround truth written to {gt}\n")

    from data_gen.validate import print_report, validate_folder

    report = validate_folder(out, ground_truth=gt, strict=True)
    print_report(report)
    if not report.ok:
        print("Validation FAILED — not loading into the DB.")
        return 1

    if not args.no_db:
        from common.db import get_data_level, load_csvs_to_db

        loaded = load_csvs_to_db(out, db_path=args.db)
        print(f"\nLoaded {sum(loaded.values()):,} rows into {args.db or settings.DB_PATH} "
              f"(data level {get_data_level(args.db)}).")
        if settings.DATA_SOURCE != "synthetic":
            print(f"Note: .env has DATA_SOURCE={settings.DATA_SOURCE}. Set DATA_SOURCE=synthetic so every "
                  "module (and scripts/init_db.py) uses this data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
