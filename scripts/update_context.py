#!/usr/bin/env python3
"""Regenerate auto-updated sections of CONTEXT.md.

Zero dependencies (stdlib only).
Idempotent — running twice produces identical output.
Preserves all manually curated sections between AUTO markers.

Usage:
    python scripts/update_context.py
"""

from __future__ import annotations

import ast
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_PATH = PROJECT_ROOT / "CONTEXT.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_test_file(path: str) -> bool:
    return "/tests/" in path or "/test_" in path or path.endswith("/conftest.py")


def _unparse_annotation(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return str(getattr(node, "id", str(node)))


def _unparse_default(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _build_function_signature(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    is_method: bool = False,
) -> str:
    """Build a human-readable function signature from an AST FunctionDef node."""
    args = func_node.args
    parts: list[str] = []

    # Positional args (skip 'self' if it's a method)
    for arg in args.args:
        name = arg.arg
        if is_method and not parts and name in ("self", "cls"):
            continue
        annotation = _unparse_annotation(arg.annotation)
        if annotation:
            parts.append(f"{name}: {annotation}")
        else:
            parts.append(name)

    # *args (vararg)
    if args.vararg:
        annotation = _unparse_annotation(args.vararg.annotation)
        if annotation:
            parts.append(f"*{args.vararg.arg}: {annotation}")
        else:
            parts.append(f"*{args.vararg.arg}")

    # Keyword-only args
    if args.kwonlyargs:
        if not args.vararg:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            annotation = _unparse_annotation(arg.annotation)
            default_str = _unparse_default(default)
            seg = f"{arg.arg}: {annotation}" if annotation else arg.arg
            if default_str is not None:
                seg += f" = {default_str}"
            parts.append(seg)

    # **kwargs
    if args.kwarg:
        annotation = _unparse_annotation(args.kwarg.annotation)
        if annotation:
            parts.append(f"**{args.kwarg.arg}: {annotation}")
        else:
            parts.append(f"**{args.kwarg.arg}")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Section generators
# ---------------------------------------------------------------------------

def generate_module_map() -> str:
    """Walk mailmind/ with ast.parse, extract top-level classes and public functions."""
    pkg = PROJECT_ROOT / "mailmind"
    lines: list[str] = [
        "| Module | Purpose | Key Class/Function |",
        "|---|---|---|",
    ]

    for py_file in sorted(pkg.rglob("*.py")):
        rel = py_file.relative_to(PROJECT_ROOT)
        rel_str = str(rel)

        if _is_test_file(rel_str):
            continue
        if rel.name.startswith("._"):
            continue
        if "__pycache__" in rel_str:
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        # Module docstring first line
        doc = ast.get_docstring(tree)
        purpose = (doc.split("\n")[0] if doc else "").strip()

        # Top-level classes and public functions
        symbols: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    symbols.append(f"{node.name}()")

        # Skip empty __init__.py files with no purpose and no symbols
        if rel.name == "__init__.py" and not purpose and not symbols:
            continue

        # Truncate to avoid very long cells
        MAX_SYMBOLS = 8
        if len(symbols) > MAX_SYMBOLS:
            symbols = symbols[:MAX_SYMBOLS] + [f"… +{len(symbols) - MAX_SYMBOLS} more"]
        key_str = ", ".join(symbols) if symbols else "—"

        md_path = f"`{rel_str}`"
        lines.append(f"| {md_path} | {purpose} | {key_str} |")

    return "\n".join(lines)


def generate_key_interfaces() -> str:
    """Generate constructor signatures for important classes using ast."""
    targets: dict[str, tuple[str, str]] = {
        "Pipeline": ("mailmind/processing/pipeline.py", "Pipeline"),
        "PriorityScorer": ("mailmind/processing/scorer.py", "PriorityScorer"),
        "RulesEngine": ("mailmind/processing/rules.py", "RulesEngine"),
        "QueueManager": ("mailmind/processing/queue_manager.py", "QueueManager"),
        "DeepSeekClient": ("mailmind/llm/deepseek.py", "DeepSeekClient"),
        "MailMindConfig": ("mailmind/config.py", "MailMindConfig"),
    }

    blocks: list[str] = []

    for display_name, (file_path, class_name) in targets.items():
        full_path = PROJECT_ROOT / file_path
        if not full_path.exists():
            continue
        source = full_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(full_path))
        except SyntaxError:
            continue

        cls_node = None
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                cls_node = node
                break

        if cls_node is None:
            continue

        init_node = None
        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                init_node = node
                break

        lines_for_class: list[str] = [f"### {display_name}", "```python"]

        if display_name == "MailMindConfig":
            lines_for_class.append("@dataclass")
        lines_for_class.append(f"class {display_name}:")

        if init_node:
            args_str = _build_function_signature(init_node, is_method=True)
            lines_for_class.append(f"    def __init__({args_str})")

        # Public methods (up to 8)
        public_methods = []
        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                args = _build_function_signature(node, is_method=False)
                public_methods.append(f"    def {node.name}({args})")

        if public_methods:
            lines_for_class.extend(public_methods[:8])

        lines_for_class.append("```")
        blocks.append("\n".join(lines_for_class))

    return "\n\n".join(blocks)


def generate_env_vars() -> str:
    """Grep for os.environ.get calls across the codebase and build a table."""
    pkg = PROJECT_ROOT / "mailmind"
    matches: dict[str, tuple[str, str]] = {}

    for py_file in sorted(pkg.rglob("*.py")):
        rel = str(py_file.relative_to(PROJECT_ROOT))
        if _is_test_file(rel):
            continue
        if py_file.name.startswith("._"):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        pattern = re.compile(
            r'os\.environ\.get\s*\(\s*["\']([A-Z_][A-Z_0-9]+)["\']\s*,?\s*["\']([^"\']*)["\']?\s*\)'
        )
        for m in pattern.finditer(source):
            var_name = m.group(1)
            default_val = m.group(2)
            if var_name not in matches:
                matches[var_name] = (default_val, rel)

    purposes = {
        "MAILMIND_DB_PATH": "SQLite database path",
        "MAILMIND_DATA_DIR": "Config + data directory (`~/.mailmind` by default)",
        "MAILMIND_POLL_SECONDS": "Poll interval in seconds (--watch mode)",
        "MAILMIND_FETCH_MAX": "Max emails per fetch run",
        "MAILMIND_DRY_RUN": 'Set to "1" to skip real Gmail label writes',
        "MAILMIND_USER_EMAIL": "User's primary email address (for scoring boosts)",
        "MAILMIND_ACCOUNTS": "Comma-separated list of additional account emails",
        "MAILMIND_ENV_FILE": "Path to a .env file to load on startup",
        "MAILMIND_RETENTION_DAYS": "How many days of history to keep in the DB",
        "DEEPSEEK_API_KEY": "DeepSeek API key; absent → LLM disabled",
        "DEEPSEEK_MODEL": "DeepSeek model name override",
        "DEEPSEEK_BASE_URL": "DeepSeek API base URL override",
        "DEEPSEEK_MAX_CALLS_PER_RUN": "Max LLM API calls per pipeline run",
        "OPENAI_API_KEY": "OpenAI API key (alternative LLM backend)",
        "LLM_PROVIDER": "LLM backend to use: `auto`, `deepseek`, or `openai`",
        "LLM_ENABLED": 'Set to "true" to enable LLM classification',
        "LLM_ML_THRESHOLD": "Min ML confidence before LLM is called",
        "LLM_RULES_THRESHOLD": "Min rules confidence before LLM is skipped",
        "LLM_MAX_BODY_CHARS": "Max body chars sent to LLM per email",
        "DASHBOARD_PASSWORD": "Password to protect the Streamlit dashboard",
        "DASHBOARD_SECRET": "HMAC secret for dashboard session tokens",
        "CONTENT_WEIGHT": "Content-signal weight in sender/content blend (0–1)",
        "SENDER_WEIGHT": "Sender-signal weight in blend (0–1)",
        "BLEND_ENABLED": 'Set to "false" to disable sender/content blending',
        "SENDER_PRIOR_MIN_COUNT": "Min sender history count before prior is trusted",
    }

    lines: list[str] = [
        "| Variable | Default | Purpose |",
        "|---|---|---|",
    ]

    for var_name in sorted(matches.keys()):
        default_val, _ = matches[var_name]
        display_default = f"`{default_val}`" if default_val else '`""`'
        purpose = purposes.get(var_name)
        if purpose is None:
            continue  # skip undocumented internal tuning vars
        lines.append(f"| `{var_name}` | {display_default} | {purpose} |")

    return "\n".join(lines)


def generate_test_count() -> int | None:
    """Run pytest --collect-only and extract test count."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "mailmind/tests/", "--collect-only", "-q"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        m = re.search(r"(\d+)\s+tests?\s+collected", output)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)\s+selected", output)
        if m:
            return int(m.group(1))
        return None
    except Exception:
        return None


def generate_open_todos() -> str:
    """Grep for # TODO across all .py files, exclude tests and ._ files."""
    pkg = PROJECT_ROOT / "mailmind"
    results: list[str] = []

    for py_file in sorted(pkg.rglob("*.py")):
        rel = str(py_file.relative_to(PROJECT_ROOT))
        if _is_test_file(rel):
            continue
        if py_file.name.startswith("._"):
            continue

        try:
            file_lines = py_file.read_text(encoding="utf-8").split("\n")
        except Exception:
            continue

        for i, line in enumerate(file_lines, start=1):
            if "# TODO" in line:
                idx = line.find("# TODO")
                todo_text = line[idx:].strip()
                results.append(f"- `[{rel}:{i}]` {todo_text}")

    if not results:
        return "None found."
    return "\n".join(results)


# ---------------------------------------------------------------------------
# Main update logic
# ---------------------------------------------------------------------------

AUTO_START_PAT = re.compile(r"<!--\s*AUTO:START:(\w+)\s*-->")
AUTO_END_PAT = re.compile(r"<!--\s*AUTO:END:(\w+)\s*-->")


def update_context() -> None:
    """Read CONTEXT.md, replace auto sections, write back."""
    if not CONTEXT_PATH.exists():
        print(f"ERROR: {CONTEXT_PATH} not found. Run from the repo root.")
        raise SystemExit(1)

    original = CONTEXT_PATH.read_text(encoding="utf-8")
    lines = original.split("\n")

    # Generate fresh content for each auto section
    generated: dict[str, list[str]] = {}

    generated["module_map"] = generate_module_map().split("\n")
    generated["key_interfaces"] = generate_key_interfaces().split("\n")
    generated["env_vars"] = generate_env_vars().split("\n")
    generated["open_todos"] = generate_open_todos().split("\n")

    test_count = generate_test_count()
    count_str = str(test_count) if test_count is not None else "(unknown)"
    generated["current_pass_notes"] = [
        f"Pass 8 complete. {count_str} tests passing.",
        "Next: Pass 9 — TBD",
    ]

    # --- Rebuild file, replacing auto sections ---
    result_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        start_match = AUTO_START_PAT.match(line)
        if start_match:
            section_name = start_match.group(1)
            i += 1
            while i < len(lines):
                end_match = AUTO_END_PAT.match(lines[i])
                if end_match and end_match.group(1) == section_name:
                    break
                i += 1

            if section_name in generated:
                content = generated[section_name]
                # Trim empty lines at edges
                while content and content[0] == "":
                    content.pop(0)
                while content and content[-1] == "":
                    content.pop()

                result_lines.append(f"<!-- AUTO:START:{section_name} -->")
                result_lines.extend(content)
                result_lines.append(f"<!-- AUTO:END:{section_name} -->")

                # Skip past AUTO:END line
                i += 1

                # Consume all consecutive blank lines after the block, emit exactly one
                blank_count = 0
                while i < len(lines) and lines[i] == "":
                    blank_count += 1
                    i += 1
                if blank_count:
                    result_lines.append("")
            else:
                result_lines.append(f"<!-- AUTO:START:{section_name} -->")
                result_lines.append(f"<!-- AUTO:END:{section_name} -->")
                print(
                    f"WARNING: Unknown auto section '{section_name}' "
                    "— preserved empty."
                )
                i += 1

            continue

        result_lines.append(line)
        i += 1

    # Update date (the date line may have a "> " prefix)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result_text = "\n".join(result_lines)
    # Match the date anywhere on the line (with or without blockquote prefix)
    result_text = re.sub(
        r"(Last updated: )\d{4}-\d{2}-\d{2}",
        rf"\g<1>{today}",
        result_text,
    )

    # Normalize trailing whitespace for idempotent comparison
    result_text = result_text.rstrip() + "\n"
    original_normalized = original.rstrip() + "\n"

    if result_text != original_normalized:
        CONTEXT_PATH.write_text(result_text, encoding="utf-8")
        print(f"CONTEXT.md updated ({today})")
    else:
        print("CONTEXT.md is already up to date.")


if __name__ == "__main__":
    update_context()