# Pass 3 Stabilization - Integration Notes

## Summary

This document describes the cleanup and integration changes made to stabilize Pass 3 (deterministic rules-based pipeline) before Pass 4 (ML stage). The changes focus on aligning data models, persistence, and audit logging with the intended architecture while preserving current working behavior.

## Changes Made

### 1. Prediction Model and Database Schema Alignment

**Files Modified:**
- `mailmind/storage/models.py`
- `mailmind/storage/migrations.py`
- `mailmind/storage/database.py`

**Changes:**
- Extended `Prediction` dataclass with new fields:
  - `primary_label` (Optional[str]): Top-priority label from scorer
  - `pipeline_used` (str): "rules" for MVP, "ml" or "hybrid" for future phases
  - `action_suggested` (Optional[str]): The suggested action, if any
  - `rule_matches` (Optional[List[str]]): Names of rules that matched
  - `scoring_breakdown` (Optional[str]): JSON serialization of scoring details
  - `ml_confidence` (Optional[float]): Nullable for rules-only MVP
  - `llm_confidence` (Optional[float]): Nullable for rules-only MVP
  - Kept `score` field for backward compatibility (deprecated)

- Added Database Migration `0007_extend_predictions_columns`:
  - Adds all new columns to `predictions` table
  - Migration is safe: uses ALTER TABLE and catches "duplicate column" errors
  - Backward compatible with existing data

- Updated `Database.save_prediction()`:
  - Persists all new fields
  - Handles JSON serialization for rule_matches and scoring_breakdown

**Backward Compatibility:**
- All new columns are nullable in the database
- Existing predictions remain queryable
- `score` field still populated alongside `priority_score`

### 2. Action Audit Logging

**Files Modified:**
- `mailmind/actions/executor.py`

**Changes:**
- Updated `_log_action_attempted()`:
  - Now calls `self.db.log_action()` to persist records
  - Logs blocked, deferred, and dry-run actions
  - Records reason for decision in `details` field

- Updated `_log_action_executed()`:
  - Now calls `self.db.log_action()` to persist records
  - Records success/failure and error details
  - Links to `ActionApplied` table via `email_gmail_id`

**Recorded Actions:**
- `status: "blocked"` - Action blocked by safety policy
- `status: "deferred"` - Action skipped due to low confidence
- `status: "dry_run"` - Dry-run mode (would execute but logging only)
- `succeeded: True/False` - Actual execution success/failure

### 3. Tightened `directly_addressed` Rule

**File Modified:**
- `mailmind/processing/rules.py`

**Changes:**
- Improved `_match_directly_addressed()` with stricter heuristics:
  - Rejects known list-sender prefixes (noreply, no-reply, bounce, postmaster, mailer-daemon, etc.)
  - Rejects if sender has bulk/automation patterns in local part
  - Rejects if many To: recipients (>3)
  - Rejects if unsubscribe/list signals present in body
  - Rejects if subject has list patterns ([list], [announce], [bulk], etc.)
  - Rejects if sender lacks proper domain

**Purpose:**
- Prevents false positives on mailing-list / announcement traffic
- Keeps rule deterministic and readable
- Maintains true 1:1 email detection for real personal messages

### 4. Durable Scoring Breakdown Storage

**Files Modified:**
- `mailmind/processing/scorer.py` (unchanged - already has `to_dict()`)
- `mailmind/processing/pipeline.py`

**Changes:**
- `Pipeline._create_prediction()` now:
  - Serializes `ScoreResult.to_dict()` to JSON
  - Stores as `scoring_breakdown` in Prediction
  - Preserves human-readable debug output in logs

**Use Cases:**
- ML comparison and validation during Pass 4
- Debugging and troubleshooting
- Audit trail of scoring decisions
- Future feedback loop training

### 5. Sender Reputation Safe Access

**File Modified:**
- `mailmind/storage/database.py`

**Added Method:**
- `get_sender_trust_score(sender: str, default: float = 0.0) -> float`
  - Safe getter that clamps values to 0.0-10.0 range
  - Returns default on missing/invalid data
  - Used by scorer in `_compute_sender_trust()`

**Purpose:**
- Bounded, safe access pattern for future reputation learning
- Prevents invalid values from corrupting scoring

### 6. Improved Rule Matching Persistence

**Files Modified:**
- `mailmind/processing/pipeline.py`
- `mailmind/storage/database.py`

**Changes:**
- `rule_matches` field now stores comma-separated rule names
- Stored in Prediction for audit trail
- Primary label priority order standardized in `PriorityScorer._determine_primary_label()`:
  1. URGENT
  2. WORK
  3. FINANCE
  4. PERSONAL
  5. CALENDAR
  6. NOTIFICATION
  7. NEWSLETTER
  8. MASS_EMAIL
  9. SPAMCANDIDATE
  10. DEFER

### 7. Configuration Centralization

**Files Reviewed:**
- `mailmind/actions/safety.py` (already has configurable params)
- `mailmind/actions/executor.py` (uses SafetyPolicy config)

**Status:**
- SafetyPolicy already supports:
  - `protected_domains`: Email domains exempt from auto-actions
  - `protected_senders`: Specific senders exempt from auto-actions
  - `max_actions_per_hour`: Rate limiting
  - `dry_run`: Enable/disable actual email modification
  - `no_auto_delete`: Never allow deletion (hardcoded)
  - `no_auto_archive_categories`: {"URGENT", "FINANCE", "PERSONAL"}

- No changes needed; existing design is sound
- Support for adding runtime config hooks later

### 8. Debug Script Enhancement

**File Modified:**
- `mailmind/scripts/debug_pass3_pipeline.py`

**Changes:**
- Expanded output to show all new Pass 3+ fields
- Displays scoring breakdown JSON details
- Verifies database persistence
- Shows action audit logging results
- Checks integrity of persisted predictions

**New Output Sections:**
- PREDICTION MODEL (with extended fields)
- ACTION EXECUTION AND AUDIT LOGGING
- DATABASE PERSISTENCE RESULTS
- DATABASE INTEGRITY CHECK

## Migration Path

### For Existing Databases

1. **First Run After Update:**
   - Migration `0007_extend_predictions_columns` runs automatically
   - Adds new columns silently
   - No data loss - all new columns are nullable
   - Existing predictions remain unchanged

2. **No Manual Steps Required:**
   - `apply_migrations()` is idempotent
   - Duplicate column errors are caught and logged
   - Safe to run repeatedly

### For New Installations

- New database created with full schema
- All fields available from start

## Important Assumptions

1. **Email Model:**
   - Assumes `email.recipients` and `email.to_addresses` attributes exist
   - Falls back to `recipients` if `to_addresses` absent
   - Compatible with current parser output

2. **Scoring Breakdown:**
   - `ScoreResult.to_dict()` is JSON-serializable
   - Stored as string, parsed on retrieval if needed
   - No schema required for JSON flexibility

3. **Rule Matches:**
   - Stored as comma-separated string
   - Simple format for backward compat
   - Can upgrade to JSON in future without schema change

4. **Action Logging:**
   - `ActionApplied` model uses `params` dict (JSON serialized)
   - `dry_run` is boolean (0/1 in database)
   - `succeeded` can be NULL (not yet executed, blocked, or deferred)

5. **Confidence Thresholds:**
   - Rules pipeline always uses 0.85 confidence
   - Actions use thresholds from `executor.py` CONFIDENCE_THRESHOLDS
   - ML/LLM confidence values placeholders for Phase 4+

## Method Names to Verify

The following method signatures are used throughout the codebase:

```python
# Database methods
db.save_prediction(pred: Prediction) -> int
db.log_action(action: ActionApplied) -> int
db.get_predictions_for_email(gmail_id: str) -> List[sqlite3.Row]
db.get_sender_trust_score(sender: str, default: float = 0.0) -> float

# Pipeline methods
pipeline.process(email: Email, auto_action: bool = False) -> Prediction

# Executor methods
executor.suggest_action(email: Email, score: ScoreResult) -> Optional[str]
executor.execute_action(email: Email, action: str, score: ScoreResult) -> bool

# Scorer method
scorer.compute_score(email: Email, rule_matches: List[RuleMatch], 
                     sender_reputation: Optional[SenderReputation]) -> ScoreResult

# Safety policy method
safety_policy.evaluate(action: str, email: Email, confidence: float, 
                       primary_label: Optional[str]) -> SafetyDecision
```

## Testing the Changes

### Run the Debug Harness

```bash
# From PyProjects root directory:
python mailmind/scripts/debug_pass3_pipeline.py
```

This will:
1. Authenticate with Gmail
2. Fetch and parse 1 real email
3. Run complete pipeline through all components
4. Persist prediction with all new fields
5. Log an action to audit table
6. Verify database integrity
7. Print detailed output of all steps

### Expected Output Sections

The debug script now shows:
- Email metadata (ID, sender, subject)
- Rules matched with confidence scores
- Complete scoring breakdown
- Full prediction model (including new Pass 3+ fields)
- Action suggestions and audit logging results
- Database persistence verification

### Verify Database Schema

```sql
-- Check new columns in predictions table
PRAGMA table_info(predictions);

-- Should show these columns:
-- primary_label TEXT
-- pipeline_used TEXT DEFAULT 'rules'
-- action_suggested TEXT
-- rule_matches TEXT
-- scoring_breakdown TEXT
-- ml_confidence REAL
-- llm_confidence REAL
```

## Future Work (Pass 4+)

### ML Integration Points

1. **Prediction Model:**
   - `pipeline_used` changed to "ml" when ML stage active
   - `ml_confidence` populated by ML classifier
   - `rule_matches` still populated (ensemble with rules)

2. **Scoring:**
   - Score may be weighted average of rules + ML
   - Breakdown preserved for hybrid tracing

3. **Action Logging:**
   - Already tracks all decision points
   - Audit trail ready for feedback loop

### Breaking Changes to Avoid in Pass 4

- Do NOT change `ActionApplied` model without migration
- Do NOT remove `score` field from Prediction (still used backward compat)
- Ensure JSON in scoring_breakdown remains parseable
- Rule names should remain stable (already persisted)

## Known Limitations / Next Steps

1. **Sender Reputation:**
   - Currently only safe-reads from database
   - No feedback loop implemented yet (Phase 5+)
   - Trust score defaults to 0.0 if not found

2. **Rate Limiting:**
   - In-memory only (resets on process restart)
   - Should migrate to database for persistence (Phase 4+)

3. **Configuration:**
   - Still hardcoded in SafetyPolicy
   - Should add YAML/config file loading (nice-to-have)

4. **Composable Rules:**
   - Rule matching is still boolean
   - Future: support confidence-weighted rule interactions

## Summary of Files Changed

| File | Changes | Impact |
|------|---------|--------|
| `storage/models.py` | Extended Prediction dataclass | Data models |
| `storage/migrations.py` | Added migration 0007 | Schema evolution |
| `storage/database.py` | Updated save_prediction(), added get_sender_trust_score() | Persistence |
| `processing/rules.py` | Rewrote _match_directly_addressed() | Rule precision |
| `processing/pipeline.py` | Updated _create_prediction() to populate all fields | Data population |
| `processing/scorer.py` | No changes (already supports needed features) | Baseline |
| `actions/executor.py` | Updated _log_action_attempted/_executed() | Audit logging |
| `actions/safety.py` | No changes (already well-designed) | Baseline |
| `scripts/debug_pass3_pipeline.py` | Enhanced output and validation | Validation |

## Version Compatibility

- **Python:** 3.11+ only
- **Dependencies:** No new external dependencies added
- **Local-First:** All changes are privacy-preserving, no remote calls added

## Questions / Support

If you encounter issues:

1. Check database schema with: `PRAGMA table_info(predictions);`
2. Review debug output: `python mailmind/scripts/debug_pass3_pipeline.py`
3. Check migration logs in: `SELECT * FROM _migrations;`
4. Verify action audit: `SELECT * FROM actions_applied;`

