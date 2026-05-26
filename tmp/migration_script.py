"""Add migration 0010 to migrations.py."""
from pathlib import Path

path = Path("/Volumes/T7/PyProjects/mailmind/storage/migrations.py")
content = path.read_text()

if "0010_add_llm_classification_fields" in content:
    print("Migration 0010 already exists in MIGRATIONS list")
else:
    migration_0010 = """    (
        "0010_add_llm_classification_fields",
        \"\"\"
        ALTER TABLE predictions ADD COLUMN llm_label TEXT;
        ALTER TABLE predictions ADD COLUMN llm_rationale TEXT;
        ALTER TABLE predictions ADD COLUMN llm_action_hint TEXT;
        ALTER TABLE predictions ADD COLUMN llm_needs_review INTEGER DEFAULT 0;
        ALTER TABLE predictions ADD COLUMN classifier_source TEXT DEFAULT 'rules';
        ALTER TABLE predictions ADD COLUMN llm_called_at TEXT;
        \"\"\",
    ),
"""
    old = "]\n\n\ndef _ensure_migrations_table"
    new = migration_0010 + "]\n\n\ndef _ensure_migrations_table"
    if old in content:
        content = content.replace(old, new)
        path.write_text(content)
        print("Migration 0010 added to MIGRATIONS list")
    else:
        print("Could not find insertion point - check migrations.py")