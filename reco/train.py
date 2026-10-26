"""Step 12 — Train everything and save it to models/.

    python -m reco.train

1. Load data (app.db, DATA_SOURCE from .env).
2. Train the LightGBM ranker on rolling windows before the test period (see ranker.py).
3. Train all candidate models on ALL data (so serving knows the newest products/behaviour).
4. Attach the ranker and save everything to models/reco_bundle.pkl.

Run `python -m reco.evaluate` afterwards to measure quality (writes reco/results.md).
"""

from __future__ import annotations

import time
from pathlib import Path

from common.logging_utils import get_logger
from reco.data import load_data, make_split
from reco.engine import Recommender
from reco.ranker import train_ranker

log = get_logger(__name__)


def train_serving_engine(db_path: str | Path | None = None) -> Recommender:
    data = load_data(db_path)
    if data.events.empty:
        raise RuntimeError("No events found. Run `python scripts/init_db.py` first (see docs/SETUP_GUIDE.md).")
    split = make_split(data.events)
    ranker = train_ranker(data, split)
    engine = Recommender.fit(data)
    engine.ranker = ranker
    return engine


def main() -> None:
    from reco.service import MODEL_PATH, save_engine

    start = time.time()
    engine = train_serving_engine()
    save_engine(engine)
    print(f"Trained on data level {engine.level} in {time.time() - start:.1f}s -> {MODEL_PATH}")
    print("  models:", ", ".join(n for n, m in [("popularity", engine.popularity), ("item_cf", engine.item_cf),
                                                ("als", engine.als), ("content", engine.content),
                                                ("ranker", engine.ranker)] if m is not None))
    if engine.ranker is not None:
        top = list(engine.ranker.importance().items())[:5]
        print("  ranker's most-used features:", ", ".join(f"{k} ({v})" for k, v in top))


if __name__ == "__main__":
    main()
