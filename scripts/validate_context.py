#!/usr/bin/env python3
"""
Validates the quality of a generated CONTEXT.md.

Exits 0 on success, 1 on hard failures, prints warnings for soft issues.
Writes a JSON report to validate_report.json.

Usage:
    python validate_context.py [--context CONTEXT.md] [--claude CLAUDE.md]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_SECTIONS = [
    "project_overview",
    "tech_stack",
    "directory_tree",
    "env_vars",
    "open_todos",
    "recent_commits",
]

CONTAMINATION_PATTERNS = [
    ".venv", "node_modules", "dist/", "build/", "__pycache__",
    "site-packages", "PIL/", "Pillow",
]

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"ghp_[A-Za-z0-9]{36}",
    r"AIza[A-Za-z0-9\-_]{35}",
    r"AKIA[A-Z0-9]{16}",
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
]

MAX_SIZE_KB = 50
CLAUDE_MD_MAX_AGE_DAYS = 90


def check_sections(content: str) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for section in REQUIRED_SECTIONS:
        start = f"<!-- AUTO:START:{section} -->"
        end = f"<!-- AUTO:END:{section} -->"
        if start not in content or end not in content:
            errors.append(f"Missing required section: {section}")
            continue
        body = re.search(
            rf"{re.escape(start)}\s*(.*?)\s*{re.escape(end)}", content, re.DOTALL
        )
        if not body or not body.group(1).strip():
            warnings.append(f"Section '{section}' is present but empty")
    return errors, warnings


def check_contamination(content: str) -> list[str]:
    # Only scan inside AUTO sections (not the generator's own exclusion-list comments)
    warnings = []
    auto_sections = re.findall(r"<!-- AUTO:START:\w+ -->(.*?)<!-- AUTO:END:\w+ -->", content, re.DOTALL)
    # Only flag lines that look like actual tree/table entries, not description prose
    combined_lines = [
        line for section in auto_sections
        for line in section.splitlines()
        if re.search(r"(├──|└──|│\s+|\|\s+`)", line)  # tree or table-row lines only
    ]
    combined = "\n".join(combined_lines)
    for pattern in CONTAMINATION_PATTERNS:
        if pattern in combined:
            warnings.append(f"Module map may be contaminated with build artifact: '{pattern}'")
    return warnings


def check_secrets(content: str) -> list[str]:
    errors = []
    for pattern in SECRET_PATTERNS:
        m = re.search(pattern, content)
        if m:
            errors.append(f"Potential secret leaked matching pattern: {pattern}")
    return errors


def check_size(context_path: Path) -> list[str]:
    warnings = []
    size_kb = context_path.stat().st_size / 1024
    if size_kb > MAX_SIZE_KB:
        warnings.append(
            f"CONTEXT.md is {size_kb:.1f} KB — exceeds {MAX_SIZE_KB} KB soft limit. "
            "Large context files reduce AI efficiency. Consider splitting."
        )
    return warnings


def check_test_count(content: str) -> list[str]:
    warnings = []
    m = re.search(r"<!-- AUTO:START:test_count -->(.*?)<!-- AUTO:END:test_count -->", content, re.DOTALL)
    if m and "(unknown)" in m.group(1):
        warnings.append("Test count is '(unknown)' — pytest may not be installed or tests directory not found")
    return warnings


def check_claude_md(claude_path: Path) -> list[str]:
    warnings = []
    if not claude_path.exists():
        warnings.append(f"CLAUDE.md not found at {claude_path} — run bootstrap.sh to create it")
        return warnings

    mtime = datetime.fromtimestamp(claude_path.stat().st_mtime, tz=timezone.utc)
    age_days = (datetime.now(timezone.utc) - mtime).days
    if age_days > CLAUDE_MD_MAX_AGE_DAYS:
        warnings.append(
            f"CLAUDE.md was last modified {age_days} days ago — consider reviewing it for accuracy"
        )
    return warnings


def check_timestamp(content: str) -> list[str]:
    warnings = []
    m = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", content)
    if not m:
        warnings.append("CONTEXT.md is missing 'Last updated' timestamp")
    return warnings


def validate(context_path: Path, claude_path: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not context_path.exists():
        errors.append(f"CONTEXT.md not found at {context_path}")
        return {"passed": False, "errors": errors, "warnings": warnings}

    content = context_path.read_text(errors="replace")

    # Hard checks
    sec_errors, sec_warnings = check_sections(content)
    errors.extend(sec_errors)
    warnings.extend(sec_warnings)

    secret_errors = check_secrets(content)
    errors.extend(secret_errors)

    # Soft checks
    warnings.extend(check_contamination(content))
    warnings.extend(check_size(context_path))
    warnings.extend(check_test_count(content))
    warnings.extend(check_claude_md(claude_path))
    warnings.extend(check_timestamp(content))

    passed = len(errors) == 0
    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "context_size_kb": round(context_path.stat().st_size / 1024, 1),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Validate CONTEXT.md quality.")
    parser.add_argument("--context", default="CONTEXT.md")
    parser.add_argument("--claude", default="CLAUDE.md")
    parser.add_argument("--report", default="validate_report.json")
    args = parser.parse_args()

    context_path = Path(args.context)
    claude_path = Path(args.claude)

    report = validate(context_path, claude_path)

    # Write JSON report
    Path(args.report).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    if report["warnings"]:
        print("\nWarnings:", file=sys.stderr)
        for w in report["warnings"]:
            print(f"  ⚠  {w}", file=sys.stderr)

    if report["errors"]:
        print("\nErrors:", file=sys.stderr)
        for e in report["errors"]:
            print(f"  ✗  {e}", file=sys.stderr)
        print("\nValidation FAILED.", file=sys.stderr)
        sys.exit(1)

    print("\nValidation passed.", file=sys.stderr)


if __name__ == "__main__":
    main()
