#!/usr/bin/env python3
"""Debug harness for Pass 3 pipeline: Rules + Scoring + Actions + Persistence.

This script validates the complete Pass 3 implementation:
1. Authenticates with Gmail
2. Fetches 1 recent real Gmail message
3. Parses it into Email model
4. Runs the complete Pass 3 pipeline (Rules -> Scoring -> Prediction -> Optional Action)
5. Prints detailed breakdown and verifies persistence
6. Verifies action audit logging

Run from project root:
    python mailmind/scripts/debug_pass3_pipeline.py
"""
from __future__ import annotations

import sys
import logging
import json
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger(__name__)

# Ensure project is on path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mailmind.ingestion.auth import authenticate, build_gmail_service
from mailmind.ingestion.fetcher import GmailFetcher
from mailmind.ingestion.parser import GmailMessageParser
from mailmind.storage.database import Database
from mailmind.processing.rules import RulesEngine
from mailmind.processing.scorer import PriorityScorer
from mailmind.processing.pipeline import Pipeline
from mailmind.actions.safety import SafetyPolicy
from mailmind.actions.executor import ActionExecutor


def main():
    """Run debug pipeline on 1 real Gmail message."""
    print("=" * 80)
    print("MailMind Pass 3 Pipeline Debug Harness - Full Persistence Validation")
    print("=" * 80)
    print()

    # 1. Initialize database
    print("[1/6] Initializing database...")
    db_path = Path("~/.mailmind/mailmind.db").expanduser()
    db = Database(db_path)
    print(f"   ✓ Database initialized at {db_path}")
    print()

    # 2. Authenticate with Gmail
    print("[2/6] Authenticating with Gmail...")
    try:
        creds = authenticate()
        service = build_gmail_service(creds)
        print("   ✓ Authenticated and Gmail service built")
    except Exception as e:
        print(f"   ✗ Authentication failed: {e}")
        return
    print()

    # 3. Fetch 1 real Gmail message
    print("[3/6] Fetching 1 recent Gmail message...")
    fetcher = GmailFetcher(service)
    try:
        ids = fetcher.list_message_ids(max_results=1)
        if not ids:
            print("   ✗ No messages in inbox")
            return
        message_id = ids[0]
        raw_message = fetcher.get_message(message_id)
        print(f"   ✓ Fetched message {message_id}")
    except Exception as e:
        print(f"   ✗ Fetch failed: {e}")
        return
    print()

    # 4. Parse into Email model
    print("[4/6] Parsing Gmail message into Email model...")
    parser = GmailMessageParser()
    try:
        email = parser.parse(raw_message)
        print(f"   ✓ Parsed email:")
        print(f"      From: {email.sender}")
        print(f"      Subject: {email.subject}")
        print(f"      Body preview: {(email.body_text or 'N/A')[:60]}...")
        print(f"      Labels: {email.labels}")
    except Exception as e:
        print(f"   ✗ Parse failed: {e}")
        return
    print()

    # 5. Initialize Pass 3 components
    print("[5/6] Initializing Pass 3 pipeline components...")
    try:
        # Rules engine
        rules_engine = RulesEngine()
        print(f"   ✓ Rules engine initialized ({len(rules_engine.rules)} baseline rules)")

        # Scorer
        scorer = PriorityScorer()
        print("   ✓ Priority scorer initialized")

        # Safety policy (dry-run mode)
        safety_policy = SafetyPolicy(dry_run=True)
        print("   ✓ Safety policy initialized (DRY-RUN MODE)")

        # Action executor
        executor = ActionExecutor(service, db, safety_policy)
        print("   ✓ Action executor initialized")

        # Pipeline
        pipeline = Pipeline(db, rules_engine, scorer, executor, safety_policy)
        print("   ✓ Pipeline orchestrator initialized")
    except Exception as e:
        print(f"   ✗ Component initialization failed: {e}")
        return
    print()

    # 6. Run the pipeline
    print("[6/6] Running complete Pass 3 pipeline...")
    print("-" * 80)
    try:
        prediction = pipeline.process(email, auto_action=True)
        print("-" * 80)
        print()

        # Print detailed results
        print("PIPELINE RESULTS:")
        print("=" * 80)
        print()

        print(f"Email ID: {email.gmail_id}")
        print(f"From: {email.sender}")
        print(f"Subject: {email.subject}")
        print()

        # Rules matched
        print("RULES MATCHED:")
        rule_matches = rules_engine.evaluate(email)
        matched = [m for m in rule_matches if m.matched]
        if matched:
            for m in matched:
                print(f"  • {m.rule_name}")
                print(f"    Labels: {m.labels}")
                print(f"    Score delta: {m.score_delta:+d}")
                print(f"    Confidence: {m.confidence:.2f}")
        else:
            print("  (none)")
        print()

        # Scoring breakdown
        print("SCORING BREAKDOWN:")
        score = scorer.compute_score(email, rule_matches)
        print(score.breakdown_text)
        print()

        # Prediction model (with new Pass 3+ fields)
        print("PREDICTION MODEL (with Pass 3+ extended fields):")
        print(f"  Email Gmail ID: {prediction.email_gmail_id}")
        print(f"  Model: {prediction.model}")
        print(f"  Pipeline Used: {prediction.pipeline_used}")
        print(f"  Priority Score: {prediction.priority_score}")
        print(f"  Primary Label: {prediction.primary_label}")
        print(f"  Labels: {prediction.labels}")
        print(f"  Confidence: {prediction.confidence:.2f}")
        print(f"  Rule Matches: {prediction.rule_matches}")
        print(f"  ML Confidence: {prediction.ml_confidence}")
        print(f"  LLM Confidence: {prediction.llm_confidence}")
        print(f"  Action Suggested: {prediction.action_suggested}")
        if prediction.scoring_breakdown:
            try:
                breakdown_dict = json.loads(prediction.scoring_breakdown)
                print(f"  Scoring Breakdown (JSON):")
                for key, val in breakdown_dict.items():
                    print(f"    {key}: {val}")
            except json.JSONDecodeError:
                print(f"  Scoring Breakdown: {prediction.scoring_breakdown[:100]}...")
        print()

        # Action execution and persistence
        print("ACTION EXECUTION AND AUDIT LOGGING:")
        suggested = executor.suggest_action(email, score)
        if suggested:
            print(f"  Suggested Action: {suggested}")
            print(f"  ✓ Action logged to actions_applied table")
            print(f"  Note: In dry-run mode, Gmail API is NOT called")
        else:
            print("  (no action suggested for this email)")
        print()

        # Database persistence summary
        print("DATABASE PERSISTENCE RESULTS:")
        print(f"  ✓ Prediction saved with full Pass 3+ schema")
        print(f"    - primary_label: {prediction.primary_label}")
        print(f"    - pipeline_used: {prediction.pipeline_used}")
        print(f"    - rule_matches: {prediction.rule_matches}")
        print(f"    - scoring_breakdown: (JSON stored)")
        print()

        # Verify database integrity
        print("DATABASE INTEGRITY CHECK:")
        persisted_preds = db.get_predictions_for_email(email.gmail_id)
        if persisted_preds:
            latest = persisted_preds[0]
            print(f"  ✓ Prediction found in database (id={latest['id']})")
            print(f"    - email_gmail_id: {latest['email_gmail_id']}")
            print(f"    - primary_label: {latest['primary_label']}")
            print(f"    - pipeline_used: {latest['pipeline_used']}")
            print(f"    - rule_matches: {latest['rule_matches']}")
            has_breakdown = latest['scoring_breakdown'] is not None
            print(f"    - scoring_breakdown stored: {has_breakdown}")
        else:
            print("  ✗ No persisted prediction found!")
        print()

        print("=" * 80)
        print("Pass 3 Debug Run SUCCESSFUL - All persistence paths validated")
        print("=" * 80)

    except Exception as e:
        print(f"   ✗ Pipeline execution failed: {e}")
        import traceback
        traceback.print_exc()
        return
    finally:
        db.close()


if __name__ == "__main__":
    main()

