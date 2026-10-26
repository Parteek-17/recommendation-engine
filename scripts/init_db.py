"""Create data/app.db with every Data Contract table and load the CSVs for DATA_SOURCE.

Run:  python scripts/init_db.py              # uses DATA_SOURCE from .env (default: sample)
      python scripts/init_db.py --fresh      # delete the old DB first (start clean)
      python scripts/init_db.py --source synthetic

Safe to run again: each table that has a CSV is replaced with the CSV's rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "common" can be imported

from common.config import settings  # noqa: E402
from common.db import data_folder, get_data_level, init_db, load_csvs_to_db, table_counts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["sample", "synthetic", "external"], help="override DATA_SOURCE")
    parser.add_argument("--fresh", action="store_true", help="delete the existing database first")
    args = parser.parse_args()

    if args.fresh and settings.DB_PATH.exists():
        settings.DB_PATH.unlink()
        print(f"Deleted old database {settings.DB_PATH}")

    folder = data_folder(args.source)
    if not folder.exists():
        sys.exit(f"❌ Data folder not found: {folder}\n"
                 "   sample -> run: python scripts/make_sample_data.py\n"
                 "   synthetic -> run the Phase 1 generator first")

    init_db()
    print(f"Database: {settings.DB_PATH}")
    print(f"Loading CSVs from: {folder}")
    loaded = load_csvs_to_db(folder)

    counts = table_counts()
    for table, n in counts.items():
        mark = "loaded" if table in loaded else "(no CSV, left as is)"
        print(f"  {table:<15} {n:>6} rows  {mark}")
    print(f"✅ Done. Data level: {get_data_level()}  (see docs/DATA_STANDARD.md)")


if __name__ == "__main__":
    # Windows consoles sometimes can't print emoji; fall back gracefully instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
