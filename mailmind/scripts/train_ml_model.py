#!/usr/bin/env python3
"""Train the Pass 4 ML model from historical database data.

Usage:
    python mailmind/scripts/train_ml_model.py [--db-path PATH] [--min-samples N]

This script:
1. Connects to the local MailMind SQLite database
2. Collects labeled email data (using predictions.primary_label as targets)
3. Trains a TF-IDF + LogisticRegression classifier
4. Saves the model to ~/.mailmind/models/pass4_baseline.joblib
5. Stores training metadata in the database (system_state)

If insufficient labeled data exists, the script warns and exits gracefully.
Run the rules pipeline on a representative set of emails first to generate
training data.
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

# Ensure project is on path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mailmind.storage.database import Database
from mailmind.ml.train import train_model_from_db, get_model_metadata_from_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger(__name__)


_DEFAULT_DB = os.environ.get("MAILMIND_DB_PATH", "~/.mailmind/mailmind.db")


def main(db_path: str = _DEFAULT_DB, min_samples: int = 10):
    """Train the ML model."""
    print("=" * 80)
    print("MailMind Pass 4 - ML Model Training")
    print("=" * 80)
    print()

    # 1. Open database
    print(f"[1/4] Opening database: {db_path}")
    db = Database(db_path)
    print("   ✓ Database connected")
    print()

    # 2. Check existing data
    print(f"[2/4] Checking training data (min_samples={min_samples})...")
    count = db.execute_sql(
        "SELECT COUNT(*) as cnt FROM predictions WHERE primary_label IS NOT NULL AND primary_label != ''"
    ).fetchone()["cnt"]
    print(f"   • {count} labeled predictions available")

    email_count = db.execute_sql(
        "SELECT COUNT(*) as cnt FROM emails WHERE parsed = 1"
    ).fetchone()["cnt"]
    print(f"   • {email_count} parsed emails available")
    print()

    # 3. Train model
    print(f"[3/4] Training ML model...")
    classifier = train_model_from_db(db, min_samples=min_samples)

    if classifier is None:
        print()
        print("   ⚠  NOT ENOUGH LABELED DATA TO TRAIN")
        print()
        print("   To generate training data, run the rules pipeline first:")
        print("     python mailmind/scripts/debug_pass3_pipeline.py")
        print()
        print("   This will process real emails and store predictions with")
        print("   primary_label values that can be used for training.")
        print()
        db.close()
        return

    print("   ✓ Model trained and saved")
    print()

    # 4. Show model info
    print(f"[4/4] Model summary:")
    meta = classifier.metadata
    if meta:
        print(f"   • Classes: {meta.class_names}")
        print(f"   • Training samples: {meta.num_samples}")
        print(f"   • Version: {meta.version}")
        print(f"   • Trained at: {meta.trained_at}")
    print()

    # Verify persisted metadata
    stored_meta = get_model_metadata_from_db(db)
    if stored_meta:
        print(f"   ✓ Model metadata saved to database")
    else:
        print(f"   ⚠  Model metadata not persisted (continues anyway)")

    db.close()

    print("=" * 80)
    print("Training complete. ML is now available for inference.")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train ML model for MailMind")
    parser.add_argument(
        "--db-path",
        default=_DEFAULT_DB,
        help="Path to MailMind SQLite database",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=10,
        help="Minimum number of labeled samples required for training",
    )
    args = parser.parse_args()
    main(db_path=args.db_path, min_samples=args.min_samples)
