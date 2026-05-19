"""Temporary script to append query functions."""
import sys

code = """

# ---------------------------------------------------------------------------
# Action Queue queries
# ---------------------------------------------------------------------------

def _row_to_queue_item_dict(row):
    \"\"\"Convert an action_queue row to a safe dict.\"\"\"
    return {
        "id": row["id"],
        "email_gmail_id": row["email_gmail_id"],
        "prediction_id": row["prediction_id"],
        "suggested_action": row["suggested_action"],
        "primary_label": row["primary_label"],
        "confidence": row["confidence"],
        "auto_eligible": row["auto_eligible"],
        "status": row["status"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
    }


def get_pending_queue(db, limit=100):
    \"\"\"Return pending items from the action queue.\"\"\"
    rows = db.execute_sql(
        "SELECT * FROM action_queue WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_queue_item_dict(r) for r in rows]


def approve_queue_item(db, queue_id):
    \"\"\"Mark a queue item as approved.\"\"\"
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


def reject_queue_item(db, queue_id):
    \"\"\"Mark a queue item as rejected.\"\"\"
    import time
    now = int(time.time())
    with db.transaction() as cur:
        cur.execute(
            "UPDATE action_queue SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, queue_id),
        )


def log_correction(
    db,
    email_gmail_id,
    original_label,
    corrected_label,
    original_action=None,
    corrected_action=None,
    source="review_dashboard",
):
    \"\"\"Log a user correction.\"\"\"
    with db.transaction() as cur:
        cur.execute(
            \"\"\"
            INSERT INTO user_corrections
                (email_gmail_id, original_label, corrected_label,
                 original_action, corrected_action, source)
            VALUES (?, ?, ?, ?, ?, ?)
            \"\"\",
            (
                email_gmail_id,
                original_label,
                corrected_label,
                original_action,
                corrected_action,
                source,
            ),
        )


def get_recent_corrections(db, limit=50):
    \"\"\"Return the most recent user corrections.\"\"\"
    rows = db.execute_sql(
        "SELECT * FROM user_corrections ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
"""

with open("mailmind/storage/queries.py", "a") as f:
    f.write(code)

print("Done")
