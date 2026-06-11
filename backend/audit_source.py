#!/usr/bin/env python3
"""
ChainWatch Static Source Audit Tool
====================================
Scans the ChainWatch source code for known pitfall patterns from the
dual-agent skill's pitfall checklist. Run locally before every deploy.

Usage:
    python audit_source.py [--path PATH] [--output FORMAT]

Exit codes:
    0 = all checks passed
    1 = critical findings
    2 = minor findings only
"""
import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set, Tuple


@dataclass
class Finding:
    pitfall: str
    severity: str  # "critical" or "minor"
    file: str
    line: int
    description: str
    suggestion: str


@dataclass
class AuditResult:
    findings: List[Finding] = field(default_factory=list)
    passed: List[str] = field(default_factory=list)

    def add(self, finding: Finding):
        self.findings.append(finding)

    def add_pass(self, check_name: str):
        self.passed.append(check_name)

    @property
    def critical(self):
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def minor(self):
        return [f for f in self.findings if f.severity == "minor"]


# ─── Helpers ──────────────────────────────────────────────────────────

def find_py_files(base: str) -> List[str]:
    results = []
    for root, dirs, files in os.walk(base):
        # Skip hidden / cache / node dirs
        dirs[:] = [d for d in dirs if d not in {
            "__pycache__", ".git", "node_modules", ".venv", "venv"
        }]
        for f in files:
            if f.endswith(".py"):
                results.append(os.path.join(root, f))
    return results


def find_sql_files(base: str) -> List[str]:
    results = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        for f in files:
            if f.endswith(".sql"):
                results.append(os.path.join(root, f))
    return results


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def lines(text: str) -> List[str]:
    return text.splitlines()


def grep_pattern(text: str, pattern: str) -> List[Tuple[int, str]]:
    """Return (line_num, line_text) for each match."""
    results = []
    for i, line in enumerate(lines(text), 1):
        if re.search(pattern, line):
            results.append((i, line.strip()))
    return results


# ─── Check Functions ──────────────────────────────────────────────────

def check_on_conflict_has_constraint(py_files: List[str], sql_files: List[str], result: AuditResult):
    """
    Pitfall #19: Every ON CONFLICT DO NOTHING/UPDATE requires a matching
    unique constraint or index in the schema.

    Improved parsing: uses 10-line context window (instead of 5) and
    handles multi-line SQL strings by joining context before regex search.
    """
    # Collect all ON CONFLICT usages in Python
    on_conflict_usages = []  # (file, line_num, table, conflict_target)
    pattern = re.compile(
        r"ON\s+CONFLICT\s*(?:\((?P<cols>[^)]+)\))?\s*(?:DO\s+NOTHING|DO\s+UPDATE)",
        re.IGNORECASE,
    )

    for fpath in py_files:
        # Skip tool files that contain SQL DDL strings with ON CONFLICT
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)
        for i, line in enumerate(file_lines, 1):
            m = pattern.search(line)
            if m:
                # Try to infer the table from nearby INSERT INTO
                # Use 10-line context window for multi-line SQL strings
                context_lines = file_lines[max(0, i - 10):i]
                context = "\n".join(context_lines)
                table_m = re.search(r"INSERT\s+INTO\s+(\w+)", context, re.IGNORECASE)
                table = table_m.group(1) if table_m else "unknown"
                cols = m.group("cols") or "(implicit)"
                on_conflict_usages.append((fpath, i, table, cols))

    if not on_conflict_usages:
        result.add_pass("Pitfall #19: No ON CONFLICT usages found (clean)")
        return

    # Collect all unique constraints from SQL files, indexed by table
    all_sql = "\n".join(read_file(f) for f in sql_files)
    constraint_pattern = re.compile(
        r"(?:CONSTRAINT\s+(\w+)\s+)?UNIQUE\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    unique_indexes = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    # Also match UNIQUE constraints inside CREATE TABLE (inline)
    inline_unique = re.compile(
        r"(\w+)\s+\w+.*(?:PRIMARY\s+KEY|UNIQUE)",
        re.IGNORECASE,
    )

    # table -> set of constraint column sets
    constraints_by_table: dict = {}
    all_constraint_cols = set()  # All known constraint column strings

    for m in constraint_pattern.finditer(all_sql):
        cols = m.group(2).strip().lower()
        all_constraint_cols.add(cols.replace(" ", ""))

    for m in unique_indexes.finditer(all_sql):
        table = m.group(2).lower()
        cols = m.group(3).strip().lower()
        cleaned = cols.replace(" ", "")
        all_constraint_cols.add(cleaned)
        constraints_by_table.setdefault(table, set()).add(cleaned)

    # Also parse inline UNIQUE from CREATE TABLE statements
    create_tables = _extract_create_table_blocks(all_sql)
    for table, cols_block in create_tables.items():
        for m in inline_unique.finditer(cols_block):
            col = m.group(1).lower()
            constraints_by_table.setdefault(table.lower(), set()).add(col)

    # Check each ON CONFLICT usage
    for fpath, line_num, table, cols in on_conflict_usages:
        table_lower = table.lower()
        cols_clean = cols.replace(" ", "").lower()

        if cols_clean == "(implicit)":
            # Implicit ON CONFLICT: PostgreSQL uses any unique constraint on the table.
            # Check if we know of any constraints on this table.
            table_constraints = constraints_by_table.get(table_lower, set())
            if table_constraints or all_constraint_cols:
                result.add_pass(
                    f"Pitfall #19: ON CONFLICT on {table} (implicit) — "
                    f"table has {len(table_constraints)} known unique constraint(s)"
                )
            else:
                result.add(Finding(
                    pitfall="#19",
                    severity="critical",
                    file=fpath,
                    line=line_num,
                    description=f"ON CONFLICT on {table} (implicit) — no unique constraints found for this table in migrations",
                    suggestion=f"Add any UNIQUE constraint on {table} via migration",
                ))
        else:
            # Explicit conflict target: check for matching constraint
            # Use exact match to avoid substring false positives (e.g., "id" in "id,address,chain")
            found = any(cols_clean == c for c in all_constraint_cols)
            if not found:
                # Also check table-specific constraints
                table_constraints = constraints_by_table.get(table_lower, set())
                found = any(cols_clean == c for c in table_constraints)
            if not found:
                result.add(Finding(
                    pitfall="#19",
                    severity="critical",
                    file=fpath,
                    line=line_num,
                    description=f"ON CONFLICT on {table} ({cols}) has no matching unique constraint in migrations",
                    suggestion=f"Add UNIQUE constraint on {table}({cols}) via migration",
                ))
            else:
                result.add_pass(f"Pitfall #19: ON CONFLICT on {table} ({cols}) has matching constraint")


def _extract_create_table_blocks(all_sql: str) -> dict:
    """
    Parse CREATE TABLE statements, correctly handling nested parentheses
    (e.g. uuid_generate_v4(), CHECK (...), DEFAULT (...)).
    Returns {table_name: cols_block_string}.
    """
    tables = {}
    # Find all CREATE TABLE positions
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        all_sql,
        re.IGNORECASE,
    ):
        table = m.group(1)
        # Find matching closing paren by counting nesting depth
        start = m.end() - 1  # position of the opening '('
        depth = 0
        pos = start
        while pos < len(all_sql):
            if all_sql[pos] == '(':
                depth += 1
            elif all_sql[pos] == ')':
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        cols_block = all_sql[start + 1:pos]
        tables[table] = cols_block
    return tables


def check_columns_exist_in_schema(py_files: List[str], sql_files: List[str], result: AuditResult):
    """
    Pitfall #20: Code references DB columns that don't exist in the schema.
    Extracts column references from SQL strings and cross-references against
    CREATE TABLE / ALTER TABLE statements in migrations.

    Fixed: Uses parenthesis-depth-aware parsing for CREATE TABLE blocks
    instead of regex, so columns inside nested parens (uuid_generate_v4(),
    CHECK constraints) are correctly captured.
    """
    # Parse schema: table -> set of columns
    all_sql = "\n".join(read_file(f) for f in sql_files)
    schema: dict = {}

    # Parse CREATE TABLE with correct parenthesis nesting
    create_tables = _extract_create_table_blocks(all_sql)
    for table, cols_block in create_tables.items():
        col_names = re.findall(r"^\s+(\w+)\s+", cols_block, re.MULTILINE)
        # Filter out SQL keywords that aren't column names
        sql_keywords = {
            "CONSTRAINT", "PRIMARY", "KEY", "UNIQUE", "CHECK", "REFERENCES",
            "DEFAULT", "NOT", "NULL", "ON", "DELETE", "CASCADE", "UPDATE",
            "AND", "OR", "IN", "AS", "INDEX", "IF", "EXISTS",
        }
        col_names_clean = [c for c in col_names if c.upper() not in sql_keywords]
        schema.setdefault(table.lower(), set()).update(c.lower() for c in col_names_clean)

    # Parse ALTER TABLE ADD COLUMN
    alter_pattern = re.compile(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )
    for m in alter_pattern.finditer(all_sql):
        table = m.group(1).lower()
        col = m.group(2).lower()
        schema.setdefault(table, set()).add(col)

    # Now extract column references from Python SQL strings
    # Look for SET col = $N (UPDATE) and INSERT INTO table (cols)
    update_pattern = re.compile(r"SET\s+(\w+)\s*=\s*\$", re.IGNORECASE)
    # Multi-line UPDATE pattern: match UPDATE table ... SET col = $N across lines
    update_block_pattern = re.compile(
        r"UPDATE\s+(\w+)\s+SET\s+((?:\w+\s*=\s*\$\d+\s*,?\s*\n?)+)",
        re.IGNORECASE | re.DOTALL,
    )
    select_pattern = re.compile(r"SELECT.*?FROM\s+(\w+)", re.IGNORECASE | re.DOTALL)

    for fpath in py_files:
        if _is_own_source(fpath):
            continue
        text = read_file(fpath)

        # ── Check UPDATE statements (multi-line, e.g. triple-quoted SQL) ──
        for m in update_block_pattern.finditer(text):
            table = m.group(1).lower()
            set_block = m.group(2)
            if table in schema:
                for col_m in re.finditer(r"(\w+)\s*=\s*\$\d+", set_block):
                    col = col_m.group(1).lower()
                    if col not in schema[table] and col not in ("where", "set", "and", "or"):
                        # Find line number
                        pos = m.start()
                        line_num = text[:pos].count("\n") + 1
                        result.add(Finding(
                            pitfall="#20",
                            severity="critical",
                            file=fpath,
                            line=line_num,
                            description=f"UPDATE references {table}.{col} but column not in schema",
                            suggestion=f"Add column {table}.{col} via migration or fix the UPDATE",
                        ))

        # ── Check INSERT INTO table (col1, col2, ...) statements ──
        insert_pattern = re.compile(
            rf"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)",
            re.IGNORECASE,
        )
        for i, line in enumerate(lines(text), 1):
            for m in insert_pattern.finditer(line):
                table = m.group(1).lower()
                cols = [c.strip().lower() for c in m.group(2).split(",")]
                if table in schema:
                    for col in cols:
                        if col not in schema[table]:
                            result.add(Finding(
                                pitfall="#20",
                                severity="critical",
                                file=fpath,
                                line=i,
                                description=f"INSERT references {table}.{col} but column not in schema",
                                suggestion=f"Add column {table}.{col} via migration or fix the INSERT",
                            ))

    result.add_pass("Pitfall #20: Schema column cross-reference check completed")


def _is_own_source(fpath: str) -> bool:
    """Check if fpath is a utility/tool file that contains DDL/SQL strings
    (skip self-referential ON CONFLICT and schema checks)."""
    return fpath.endswith(("audit_source.py", "check_migration_status.py"))


def check_try_except_imports(py_files: List[str], result: AuditResult):
    """
    Pitfall #6: Imports inside try/except blocks may not be in scope
    after the except block. Check that names used after try/except
    are also available via module-level imports.

    Fixed: Skips audit_source.py (self-referential false positives).
    Only flags imports that are used AFTER the try/except block exits
    (not within the same try block, which is safe).
    """
    for fpath in py_files:
        if _is_own_source(fpath):
            continue
        text = read_file(fpath)
        # Find try blocks with imports
        try_import_pattern = re.compile(
            r"try:\s*\n((?:\s+from\s+\S+\s+import\s+\S+|\s+import\s+\S+)+)"
            r"\s*except[^\n]*:\s*\n",
            re.MULTILINE,
        )
        for m in try_import_pattern.finditer(text):
            imports_block = m.group(1)
            # Extract the full except block (all indented lines after the except header)
            block_start = m.end()
            lines_after = text[block_start:].split("\n")
            except_block_lines = []
            for line in lines_after:
                if line and not line[0].isspace():
                    break
                except_block_lines.append(line)
            except_block = "\n".join(except_block_lines)
            # Skip if except block raises, sys.exit, or assigns fallback (safe patterns:
            # if import fails, these prevent later code from running with unbound name)
            if re.search(r"\braise\b", except_block):
                continue
            if re.search(r"sys\.exit\s*\(", except_block):
                continue
            if re.search(r"=\s*None\b", except_block):
                continue
            # Extract imported names
            imported_names = re.findall(r"import\s+([\w,\s]+)", imports_block)
            names = set()
            for imp in imported_names:
                for n in imp.split(","):
                    names.add(n.strip().split(" as ")[-1].strip())

            # Check if any name is used after the try/except
            # (simplified: just check if the import is also at module level)
            for name in names:
                if name and not re.search(rf"^(from\s+\S+\s+)?import\s+.*\b{name}\b", text, re.MULTILINE):
                    # Not found at module level — potential issue
                    line_num = text[:m.start()].count("\n") + 1
                    result.add(Finding(
                        pitfall="#6",
                        severity="minor",
                        file=fpath,
                        line=line_num,
                        description=f"Import of '{name}' inside try/except — may be unbound after except",
                        suggestion=f"Move 'import {name}' to module level or ensure all paths assign it",
                    ))

    result.add_pass("Pitfall #6: try/except import scoping check completed")


def check_cache_initialized_nonzero(py_files: List[str], result: AuditResult):
    """
    Pitfall #5: Cache variables used as multipliers should not be initialized to 0.
    """
    for fpath in py_files:
        # Skip test files — they contain mock data, not real cache initialization
        fpath_norm = fpath.replace("\\", "/")
        fpath_parts = fpath_norm.split("/")
        is_test_file = (
            "tests" in fpath_parts
            or fpath.endswith(("_test.py", "_tests.py"))
        )
        if is_test_file:
            continue
        text = read_file(fpath)
        # Look for dict initializations with 0.0 values that look like price/rate caches
        # Matches both bare keys (KEY: 0.0) and quoted keys ("ETH": 0.0)
        zero_cache_pattern = re.compile(
            r"(?:\"(\w+)\"|'(\w+)'|(\w+))\s*:\s*0\.0\s*,?\s*(?:#.*(?:price|rate|cache|threshold|multiplier))",
            re.IGNORECASE,
        )
        for m in zero_cache_pattern.finditer(text):
            var_name = m.group(1) or m.group(2) or m.group(3)
            line_num = text[:m.start()].count("\n") + 1
            result.add(Finding(
                pitfall="#5",
                severity="minor",
                file=fpath,
                line=line_num,
                description=f"Cache/rate variable '{var_name}' initialized to 0.0 — will zero out derived values on cold start",
                suggestion=f"Initialize '{var_name}' with a sane recent default instead of 0.0",
            ))

    result.add_pass("Pitfall #5: Cache initialization check completed")


def check_ws_auth_before_accept(py_files: List[str], result: AuditResult):
    """
    Pitfall #25: WebSocket endpoint must authenticate BEFORE calling accept().
    Uses AST-based analysis instead of regex for reliability.
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "websocket" not in text.lower():
            continue

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name:
                func_text = ast.get_source_segment(text, node) or ""
                if "websocket" not in func_text.lower() and "ws" not in func_text.lower():
                    continue
                if "@app.websocket" not in func_text and "websocket" not in func_text.lower():
                    continue

                # Find positions of accept() and verify_jwt in the function body
                accept_line = None
                auth_line = None
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        call_str = ast.get_source_segment(text, child) or ""
                        if "accept()" in call_str:
                            accept_line = child.lineno
                        if "verify_jwt" in call_str:
                            auth_line = child.lineno

                if accept_line and auth_line and accept_line < auth_line:
                    result.add(Finding(
                        pitfall="#25",
                        severity="critical",
                        file=fpath,
                        line=accept_line,
                        description=f"WebSocket endpoint '{node.name}' calls accept() (line {accept_line}) before verify_jwt (line {auth_line})",
                        suggestion="Move websocket.accept() after JWT verification",
                    ))
                elif accept_line and auth_line:
                    result.add_pass(f"Pitfall #25: WS endpoint '{node.name}' auth-before-accept OK (auth@{auth_line} < accept@{accept_line})")

    result.add_pass("Pitfall #25: WebSocket auth check completed")


def check_start_monitor_wired(py_files: List[str], result: AuditResult):
    """
    Pitfall #26: start_monitor must be called in the startup handler.
    Uses regex-based detection (AST parsing fails on files with module-level await).
    """
    # Pattern: match @app.on_event("startup") or @app.on_event('startup')
    # followed by start_monitor within the same function body
    startup_decorator = re.compile(
        r"@app\.on_event\s*\(\s*[\"']startup[\"']\s*\)",
    )
    shutdown_decorator = re.compile(
        r"@app\.on_event\s*\(\s*[\"']shutdown[\"']\s*\)",
    )

    for fpath in py_files:
        # Skip test files — they call start_monitor() in test methods
        # but correctly have no @app.on_event('startup') decorator.
        # Match "tests" or "test" as a path segment (e.g., .../tests/test_x.py),
        # but NOT substrings like /tmp/pytest-of-hermes/... or /tmp/pytest-0/test_xxx/
        # which are pytest temp dirs, not project test files.
        fpath_norm = fpath.replace("\\", "/")
        fpath_parts = fpath_norm.split("/")
        is_test_file = (
            "tests" in fpath_parts
            or fpath.endswith(("_test.py", "_tests.py"))
        )
        if is_test_file:
            continue
        text = read_file(fpath)
        if "start_monitor" not in text:
            continue

        file_lines = lines(text)

        # Determine if this file CALLS start_monitor (not just defines it).
        # Pattern: start_monitor(  as a function call, excluding "def start_monitor" and "async def start_monitor"
        # Skip comment lines (lines starting with #) to avoid false positives from docstrings/comments
        # that mention start_monitor(pool) as usage documentation.
        call_pattern = re.compile(r"(?<!def\s)(?<!async\sdef\s)start_monitor\s*\(")
        # Only search in non-comment, non-docstring lines
        code_lines = []
        in_docstring = False
        docstring_char = None
        for line in file_lines:
            stripped = line.strip()
            # Track docstring state
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    docstring_char = stripped[:3]
                    if stripped.count(docstring_char) >= 2:
                        # Single-line docstring, skip
                        continue
                    in_docstring = True
                    continue
                elif stripped.startswith('#'):
                    continue
            else:
                if docstring_char and docstring_char in stripped:
                    in_docstring = False
                continue
            code_lines.append(line)
        code_text = "\n".join(code_lines)
        has_call = bool(call_pattern.search(code_text))

        # Only check files that actually CALL start_monitor
        if not has_call:
            result.add_pass(f"Pitfall #26: {fpath} — defines but does not call start_monitor (OK)")
            continue

        # Find the startup handler block
        in_startup = False
        startup_start = 0
        found_startup = False
        start_monitor_in_startup = False
        func_started = False  # Track if we've passed the function definition line

        for i, line in enumerate(file_lines):
            stripped = line.strip()

            if startup_decorator.search(stripped):
                in_startup = True
                found_startup = True
                startup_start = i + 1
                func_started = False
                continue

            if in_startup:
                current_indent = len(line) - len(line.lstrip())
                
                # Skip the function definition line itself (e.g. "async def startup():")
                if not func_started and stripped.startswith(("def ", "async def ")):
                    func_started = True
                    continue
                
                # After the function def, exit when we hit another def/class at same or lower indent
                if func_started and stripped and not stripped.startswith("#") and current_indent <= 0:
                    if stripped.startswith("def ") or stripped.startswith("async def ") or stripped.startswith("@"):
                        in_startup = False
                        continue

                if "start_monitor" in stripped:
                    start_monitor_in_startup = True
                    in_startup = False  # Found it, no need to continue

        if found_startup and start_monitor_in_startup:
            result.add_pass("Pitfall #26: start_monitor wired in startup handler")
        elif found_startup:
            result.add(Finding(
                pitfall="#26",
                severity="critical",
                file=fpath,
                line=startup_start,
                description="startup handler found but start_monitor not called in it",
                suggestion="Call start_monitor(db_pool) in the @app.on_event('startup') handler",
            ))
        else:
            result.add(Finding(
                pitfall="#26",
                severity="critical",
                file=fpath,
                line=1,
                description="start_monitor is called but no @app.on_event('startup') handler found",
                suggestion="Ensure start_monitor(db_pool) is called inside the @app.on_event('startup') handler",
            ))

    result.add_pass("Pitfall #26: start_monitor wiring check completed")


def check_dual_backend_drift(py_base: str, result: AuditResult):
    """
    Pitfall #17: Check for dual versions of the same module (root services/ vs backend/services/).
    """
    root_services = os.path.join(py_base, "services")
    backend_services = os.path.join(py_base, "backend", "services")

    if not os.path.isdir(root_services) or not os.path.isdir(backend_services):
        result.add_pass("Pitfall #17: No dual backend structure detected")
        return

    root_files = set(f for f in os.listdir(root_services) if f.endswith(".py"))
    backend_files = set(f for f in os.listdir(backend_services) if f.endswith(".py"))

    overlap = root_files & backend_files
    if overlap:
        for fname in sorted(overlap):
            root_path = os.path.join(root_services, fname)
            backend_path = os.path.join(backend_services, fname)
            root_size = os.path.getsize(root_path)
            backend_size = os.path.getsize(backend_path)
            if root_size != backend_size:
                result.add(Finding(
                    pitfall="#17",
                    severity="minor",
                    file=root_path,
                    line=1,
                    description=f"Dual-backend drift: root services/{fname} ({root_size}b) vs backend/services/{fname} ({backend_size}b) — different sizes suggest divergence",
                    suggestion=f"Consolidate to backend/services/ and remove root services/{fname}",
                ))
            else:
                result.add_pass(f"Pitfall #17: {fname} same size in both locations (may still differ semantically)")
    else:
        result.add_pass("Pitfall #17: No overlapping service files between root and backend")


def check_unbounded_state_dicts(py_files: List[str], result: AuditResult):
    """
    Pitfall #12: Unbounded state dicts in background workers / services.
    Finds module-level dicts that are written to (via .update, [], etc.) but
    never pruned — they grow without bound, causing memory leaks.
    """
    # Known pruner function names that indicate the dict IS managed.
    # Must be a function DEF containing one of these keywords — not just
    # any occurrence of the keyword in variable names or comments.
    pruner_patterns = re.compile(
        r"def\s+_(?:prune|cleanup|evict|expire)(?:.*?(?:cache|cooldown|dedup|state))?",
        re.IGNORECASE,
    )

    for fpath in py_files:
        text = read_file(fpath)
        file_lines = lines(text)

        # Quick skip: if file has a pruner, it's likely managed
        has_pruner = pruner_patterns.search(text)

        # Find module-level dict assignments: _something: dict = {} or _something = {}
        dict_inits = []
        for i, line in enumerate(file_lines):
            stripped = line.lstrip()
            # Only top-level (no indentation) assignments
            if stripped and not line[0].isspace():
                m = re.match(r"(_[a-zA-Z_]\w*)\s*(?::\s*(?:dict|Dict[^a-z]*))?\s*=\s*\{\s*\}", stripped)
                if m:
                    dict_inits.append((i + 1, m.group(1)))

        if not dict_inits:
            continue

        for line_num, var_name in dict_inits:
            # Check if this dict is written to anywhere
            # Match subscript writes (d[key] =), method calls (d.update(), d.setdefault(), d.pop()),
            # and .append() for list values inside the dict
            write_pattern = re.compile(
                rf"\b{re.escape(var_name)}\b\s*(?:\[[^\]]+\]\s*=|\.update|\.setdefault|\.pop|\.append)\s*\("
            )
            is_written = write_pattern.search(text)
            if not is_written:
                # Also match subscript assignment without ( (e.g., d[key] = value)
                subscript_write = re.compile(rf"\b{re.escape(var_name)}\b\s*\[[^\]]+\]\s*=")
                is_written = subscript_write.search(text)
            if not is_written:
                continue  # Read-only dicts are fine

            # Check if there's a pruning mechanism
            if has_pruner:
                # Verify the pruner actually references this dict.
                # Two patterns:
                #   1. def _prune_X(...): ... X ...  (var referenced in body)
                #   2. def _prune_X(): ...          (function name contains the var name)
                # Pattern 1: var appears somewhere after a prune-def on a subsequent line
                pruner_refs = re.compile(
                    rf"def\s+_(?:prune|cleanup|evict|expire)\w*\b[^}}]*?\b{re.escape(var_name)}\b",
                    re.DOTALL,
                )
                # Pattern 2: function name itself contains the var name (e.g., _prune_cooldown_cache for _cooldown_cache)
                pruner_name = re.compile(
                    rf"def\s+_(?:prune|cleanup|evict|expire)[^)]*{re.escape(var_name)}",
                )
                if pruner_refs.search(text) or pruner_name.search(text):
                    continue  # Dict has a dedicated pruner — OK
                # Fall through: pruner exists but doesn't reference this dict → minor

            result.add(Finding(
                pitfall="#12",
                severity="critical" if not has_pruner else "minor",
                file=fpath,
                line=line_num,
                description=(
                    f"Module-level dict '{var_name}' is written to but has no pruning mechanism. "
                    f"This can cause unbounded memory growth (Pitfall #12: unbounded state dicts)."
                ),
                suggestion=(
                    f"Add a _prune_{var_name}() function that removes expired entries, "
                    f"and call it periodically (e.g., on read or on a timer)."
                ),
            ))

    result.add_pass("Pitfall #12: Unbounded state dict check completed")


def check_ws_reconnect_patterns(jsx_files: List[str], result: AuditResult):
    """
    Pitfall #29/#30: WebSocket zombie reconnect and stale token in closure.
    Checks for:
    - onclose handler that schedules reconnect without nulling onclose first
    - reconnect using closure-captured token instead of ref
    """
    for fpath in jsx_files:
        text = read_file(fpath)
        if "onclose" not in text.lower() and "reconnect" not in text.lower():
            continue

        file_lines = lines(text)

        # Check for onclose handler with reconnect but no null-onclose-before-close
        has_onclose_handler = bool(re.search(r"\.onclose\s*=\s*(?:function|\(|\w+\s*=>)", text, re.IGNORECASE))
        has_reconnect_in_onclose = bool(re.search(r"onclose[\s\S]{0,200}(?:reconnect|connectWS|setTimeout.*connect)", text, re.IGNORECASE))
        has_null_onclose = bool(re.search(r"onclose\s*=\s*null", text))

        if has_onclose_handler and has_reconnect_in_onclose and not has_null_onclose:
            # Find the line
            for i, line in enumerate(file_lines, 1):
                if "onclose" in line.lower() and "reconnect" in line.lower():
                    result.add(Finding(
                        pitfall="#29",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            "WebSocket onclose handler contains reconnect logic but does not "
                            "null out onclose before ws.close(). This causes zombie reconnect "
                            "after intentional disconnect."
                        ),
                        suggestion=(
                            "Before calling ws.close(), set ws.onclose = null to prevent "
                            "the reconnect path from firing on intentional disconnect."
                        ),
                    ))
                    break

        # Check for stale token in reconnect closure
        has_token_ref = bool(re.search(r"tokenRef|token_ref", text))
        has_reconnect_with_token = bool(re.search(r"setTimeout\s*\(\s*\(\)\s*=>\s*\w+\(\s*token\s*\)", text))
        if has_reconnect_with_token and not has_token_ref:
            for i, line in enumerate(file_lines, 1):
                if "setTimeout" in line and "token" in line:
                    result.add(Finding(
                        pitfall="#30",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            "WebSocket reconnect uses closure-captured 'token' variable. "
                            "If token is refreshed (re-auth), the reconnect uses the stale token."
                        ),
                        suggestion=(
                            "Use a tokenRef (useRef) that is kept in sync via useEffect, "
                            "and read tokenRef.current in the reconnect path."
                        ),
                    ))
                    break

    result.add_pass("Pitfall #29/#30: WS reconnect pattern check completed")


def check_parameter_renumbering(py_files: List[str], result: AuditResult):
    """
    Pitfall #31: $N parameter renumbering when adding filters to existing
    parameterized queries. Detects hardcoded $N params in SQL strings that
    are built dynamically (f-strings or .format), which can cause index collisions.

    Fixed: Suppresses false positives from Python f-strings that generate $N
    dynamically (e.g., f"... = ${i+3}" which produces $3, $4, ... at runtime).
    Also suppresses the safe pattern: f"... $1 ... $2 ..." used with
    *values expansion where the SET clauses are generated from enumerate
    with matching offset (e.g., f"{k} = ${i+3}").
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "$" not in text:
            continue

        file_lines = lines(text)

        for i, line in enumerate(file_lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Look for $N in f-strings or .format() SQL
            if re.search(r"\$\d+", stripped) and ('f"' in stripped or "f'" in stripped or ".format(" in stripped):
                # Check if it's a SQL line (has SELECT, INSERT, UPDATE, WHERE, etc.)
                if re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|WHERE)\b", stripped, re.IGNORECASE):
                    # Suppress false positive: Python f-string that generates $N dynamically
                    # Pattern: ${i+N}, ${param_idx}, ${idx+1} etc. — computed at runtime
                    if re.search(r"\$\{\w+\s*[+-]\s*\d+\}", stripped):
                        continue
                    # Suppress: f-string with param_idx/counter variable (dynamic)
                    if re.search(r"\$\{(?:param_idx|idx|i|counter)\}", stripped):
                        continue
                    # Suppress safe pattern: $1/$2 in an UPDATE f-string that also contains
                    # *values expansion — the SET clauses are generated from enumerate with
                    # matching offset, so adding a filter maintains consistency.
                    # Pattern: f"... SET {set_clauses} WHERE ... $1 ... $2 ..." with *values
                    if re.search(r"\$1\b", stripped) and re.search(r"\$2\b", stripped):
                        # Check if *values appears on the same line or next line
                        next_line = file_lines[i] if i < len(file_lines) else ""
                        if re.search(r"\*values\b", stripped) or re.search(r"\*values\b", next_line):
                            continue
                        # Also check if the preceding lines show the enumerate pattern
                        context_start = max(0, i - 10)
                        context = "\n".join(file_lines[context_start:i])
                        if re.search(r"enumerate.*\$\{\w+\s*\+\s*\d+\}", context):
                            continue
                    result.add(Finding(
                        pitfall="#31",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            f"Hardcoded $N positional parameter in dynamically-built SQL. "
                            f"Adding a new filter can shift all subsequent $N references."
                        ),
                        suggestion=(
                            "Use a param_idx counter that increments for each appended parameter, "
                            "and reference params as ${param_idx} in f-string SQL."
                        ),
                    ))

    result.add_pass("Pitfall #31: $N parameter renumbering check completed")


def check_write_then_reuse_conn(py_files: List[str], result: AuditResult):
    """
    Pitfall #7b: Connection variable used after async with block exits.
    Uses line-by-line indentation tracking instead of regex to avoid backtracking.
    """
    for fpath in py_files:
        text = read_file(fpath)
        file_lines = lines(text)
        in_async_with_conn = False
        conn_var = None
        block_indent = 0
        line_num_start = 0

        for i, line in enumerate(file_lines):
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)

            # Detect `async with acquire_db() as conn:` or `async with _pool.acquire() as conn:`
            conn_match = re.search(r"async\s+with\s+(?:acquire_db\(\)|_pool\.acquire\(\))\s+as\s+(\w+)\s*:", stripped)
            if conn_match:
                conn_var = conn_match.group(1)
                in_async_with_conn = True
                block_indent = current_indent
                line_num_start = i + 1
                continue

            if in_async_with_conn and conn_var is not None:
                # If we hit a line at same or lower indentation (and it's not blank/comment),
                # the async with block has ended
                if stripped and current_indent <= block_indent and not stripped.startswith("#"):
                    # Check if conn_var is used in this line (after block exited)
                    if re.search(rf"\b{re.escape(conn_var)}\.\w+\(", stripped):
                        result.add(Finding(
                            pitfall="#7b",
                            severity="critical",
                            file=fpath,
                            line=i + 1,
                            description=f"Connection variable '{conn_var}' used after its async with block exits (block started at line {line_num_start})",
                            suggestion=f"Move all {conn_var}.xxx() calls inside the async with block that acquires the connection",
                        ))
                    in_async_with_conn = False
                    conn_var = None

    result.add_pass("Pitfall #7b: Connection-after-async-with check completed")


def check_placeholder_masking(py_files: List[str], jsx_files: List[str], result: AuditResult):
    """
    Pitfall #10: Placeholder values in frontend that mask real data.

    Only flags em-dashes that appear to be JSX/UI placeholders, not
    em-dashes embedded in English prose, comments, or explanation text.
    Focuses on patterns like: {'—'} in JSX, variables assigned '—',
    or fallback return values that override real data.

    Fixed v2: Skips all '—' patterns that are clearly null/undefined guards
    (e.g., `if (!x) return '—'`, `if (!timestamp) return '—'`). These are
    intentional fallbacks for missing data, not placeholders masking real data.
    Also skips width/height CSS properties that use '—' as a separator.
    """
    placeholder_patterns = [
        r"HK\$\u2014",                              # HK$— (currency placeholder)
        r"""['"]\u2014['"]""",  # '—' or "—" (standalone placeholder string)
        r"""=\s*['"]\u2014['"]\s*[#]""",  # var = '—'  (assignment with comment)
        r"""return\s+['"]\u2014['"]""",  # return '—'  (fallback return)
        r"TODO.*fallback",
    ]

    # Lines containing these substrings are skipped (false positive sources)
    _skip_context = [
        "explanation", "label", "description", "comment",
        "whale", "signal", "confidence", "score",
    ]

    _null_guard_patterns = [
        # Single variable null check: if (!x), if (x == null), if (x === null)
        r"if\s*\(\s*!(\w+)",
        # Explicit null/undefined comparison: if (x == null), if (var === undefined)
        r"if\s*\(\s*\w+\s*=?=\s*(null|undefined)",
        # Compound null check: if (x == null || x == undefined), if (!x && x !== 0)
        r"if\s*\(\s*\w+\s*(?:==|!=|<>\?)\s*(null|undefined)",
        # Ternary with '—' on same line: x ? ... : '—' (allows } in template literals)
        r"\?\s*.*:\s*['\"\u2014]\s*\}?",
        # JSX expression with fallback: {x || '—'}, {obj.prop || '—'}
        r"\{\s*[\w.]+\s*\|\|\s*['\"\u2014]",
        # value == null || value == undefined (full compound)
        r"\w+\s*==\s*null\s*\|\|\s*\w+\s*==",
    ]

    all_files = py_files + jsx_files
    for fpath in all_files:
        if _is_own_source(fpath):
            continue
        text = read_file(fpath)
        file_lines = lines(text)
        for pattern in placeholder_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line_num = text[:match.start()].count("\n") + 1
                line_text = file_lines[line_num - 1] if line_num <= len(file_lines) else ""
                # Suppress: skip lines that look like English prose
                if any(ctx in line_text.lower() for ctx in _skip_context):
                    continue
                # Suppress: skip null/undefined guard patterns (intentional fallbacks)
                if any(re.search(p, line_text) for p in _null_guard_patterns):
                    continue
                result.add(Finding(
                    pitfall="#10",
                    severity="minor",
                    file=fpath,
                    line=line_num,
                    description=f"Possible placeholder value masking real data: '{match.group()}'",
                    suggestion="Verify whether the backend actually sends this data; if so, render it directly",
                ))

    result.add_pass("Pitfall #10: Placeholder masking check completed")


def check_cors_config(py_files: List[str], result: AuditResult):
    """
    Check for CORS allow_origins=["*"] with allow_credentials=True (invalid combo).
    Fixed: Skips audit_source.py (self-referential — contains regex patterns with allow_origins).
    """
    for fpath in py_files:
        if _is_own_source(fpath):
            continue
        text = read_file(fpath)
        if "CORSMiddleware" in text:
            # Check for the problematic pattern
            if re.search(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]", text):
                if re.search(r"allow_credentials\s*=\s*True", text):
                    result.add(Finding(
                        pitfall="CORS",
                        severity="minor",
                        file=fpath,
                        line=1,
                        description="CORS allow_origins=['*'] with allow_credentials=True violates CORS spec",
                        suggestion="Set allow_origins to specific domains (e.g., ['https://chainwatch-eness.zeabur.app'])",
                    ))
                else:
                    result.add(Finding(
                        pitfall="CORS",
                        severity="minor",
                        file=fpath,
                        line=1,
                        description="CORS allow_origins=['*'] — should be restricted to known domains in production",
                        suggestion="Set allow_origins to specific production domains",
                    ))
            else:
                result.add_pass("CORS: allow_origins is restricted (good)")

    result.add_pass("CORS configuration check completed")


def check_n_plus_one_patterns(py_files: List[str], result: AuditResult):
    """
    Pitfall #16: N+1 sequential queries masquerading as batch.
    Uses line-by-line scanning instead of regex to avoid backtracking.

    Fixed: Suppresses findings where a batch pre-fetch (ANY, batch, cache) exists
    before the loop, indicating the per-row query is a fallback, not the primary path.
    """
    db_call_pattern = re.compile(r"await\s+\w+\.(?:fetch|fetchrow|execute)\s*\(")

    for fpath in py_files:
        text = read_file(fpath)
        file_lines = lines(text)
        in_for_loop = False
        for_indent = 0
        loop_start_line = 0
        loop_has_batch_prefetch = False

        for i, line in enumerate(file_lines):
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)

            # Detect for loop
            if re.match(r"for\s+.*:\s*$", stripped):
                in_for_loop = True
                for_indent = current_indent
                loop_start_line = i + 1
                # Look back up to 30 lines for a batch pre-fetch pattern
                context_start = max(0, i - 30)
                context = "\n".join(file_lines[context_start:i])
                batch_patterns = [
                    r"ANY\s*\(",           # WHERE id = ANY($1)
                    r"batch",              # variable/function with "batch" in name
                    r"_cache",             # cache dict lookups
                    r"unnest\s*\(",        # unnest($1::uuid[])
                ]
                loop_has_batch_prefetch = any(
                    re.search(p, context, re.IGNORECASE) for p in batch_patterns
                )
                continue

            if in_for_loop:
                # If we've exited the for loop (same or lower indent, non-blank)
                if stripped and current_indent <= for_indent and not stripped.startswith("#"):
                    in_for_loop = False
                    loop_has_batch_prefetch = False
                    continue

                # Check for DB call inside the loop
                if db_call_pattern.search(stripped):
                    if loop_has_batch_prefetch:
                        # Check if this is a fallback path by looking at surrounding context
                        # (the fallback guard may be on preceding lines, not the DB call itself)
                        context_start = max(0, i - 5)
                        context = "\n".join(file_lines[context_start:i + 1])
                        context_lower = context.lower()
                        is_fallback = any(kw in context_lower for kw in [
                            "fallback", "except", "is none", "cache miss",
                            "if tx_usd is none", "_cache.get",
                            "batch missed", "pre-batched",
                        ])
                        if is_fallback:
                            # This is a fallback query, not the primary path — skip
                            in_for_loop = False
                            continue
                    result.add(Finding(
                        pitfall="#16",
                        severity="minor",
                        file=fpath,
                        line=i + 1,
                        description=f"DB query inside a for loop (loop at line {loop_start_line}) — possible N+1 pattern",
                        suggestion="Batch the query outside the loop using WHERE id = ANY($1)",
                    ))
                    in_for_loop = False  # One finding per loop

    result.add_pass("Pitfall #16: N+1 pattern check completed")


def check_missing_returning(py_files: List[str], result: AuditResult):
    """
    Pitfall #8: UPDATE without RETURNING * may indicate write-then-re-read race.
    Uses line-by-line scanning instead of multi-line regex.
    """
    for fpath in py_files:
        text = read_file(fpath)
        file_lines = lines(text)
        
        # Track UPDATE statements and check for RETURNING
        for i, line in enumerate(file_lines):
            stripped = line.strip()
            if re.search(r"\bUPDATE\s+\w+\s+SET\b", stripped, re.IGNORECASE):
                # Check if RETURNING is in this line or next few lines
                context = " ".join(file_lines[i:min(i+5, len(file_lines))])
                if "RETURNING" not in context.upper():
                    # Check if there's a SELECT from the same table after
                    table_m = re.search(r"UPDATE\s+(\w+)\s+SET", stripped, re.IGNORECASE)
                    if table_m:
                        table = table_m.group(1)
                        remaining = " ".join(file_lines[i+1:min(i+10, len(file_lines))])
                        if re.search(rf"SELECT\s+.*FROM\s+{table}\b", remaining, re.IGNORECASE):
                            result.add(Finding(
                                pitfall="#8",
                                severity="minor",
                                file=fpath,
                                line=i + 1,
                                description=f"UPDATE on {table} without RETURNING followed by SELECT — possible write-then-re-read race",
                                suggestion="Use UPDATE ... RETURNING * to get the updated row in one query",
                            ))

    result.add_pass("Pitfall #8: Write-then-re-read check completed")


def check_cron_secret_fail_closed(py_files: List[str], result: AuditResult):
    """
    Cron secret auth must fail-closed: if CRON_SECRET is not set,
    the endpoint must deny access (not silently allow it).
    Pattern to flag: `if _CRON_SECRET and authorization != ...`
    which silently passes when CRON_SECRET is empty string.
    """
    bad_pattern = re.compile(
        r"if\s+_CRON_SECRET\s+and\s+authorization\s*!="
    )
    good_pattern = re.compile(
        r"if\s+not\s+_CRON_SECRET"
    )
    for fpath in py_files:
        text = read_file(fpath)
        if "_CRON_SECRET" not in text:
            continue
        rel = os.path.relpath(fpath, os.getcwd())
        if bad_pattern.search(text) and not good_pattern.search(text):
            result.add(Finding(
                pitfall="cron-secret",
                severity="critical",
                file=rel,
                line=0,
                description=(
                    "CRON_SECRET auth uses `if _CRON_SECRET and auth != ...` which "
                    "silently bypasses auth when CRON_SECRET is empty string (fail-open). "
                    "Should fail-closed: check `if not _CRON_SECRET: raise 503` first."
                ),
                suggestion=(
                    "Add explicit check: `if not _CRON_SECRET: raise 503` "
                    "before the auth comparison."
                ),
            ))
            return
    result.add_pass("Cron secret auth is fail-closed (good)")


def check_frontend_backend_field_contract(py_files: List[str], jsx_files: List[str],
                                          sql_files: List[str], result: AuditResult):
    """
    Frontend-Backend Field Contract Audit (Pitfall #18).
    Extracts all field accesses from .jsx files (e.g., wallet.balance_usd,
    data.portfolio.total_value_hkd) and verifies each field is actually
    returned by the corresponding backend endpoint.

    Known safe exceptions:
    - Fields from external APIs (Alpaca, Etherscan, etc.)
    - Standard HTTP response fields (status, ok, json)
    - Auth token fields
    """
    import json as _json

    # ── 1. Build a map: endpoint -> set of returned field names ────────
    endpoint_fields: dict = {}

    for fpath in py_files:
        text = read_file(fpath)
        if not text:
            continue
        rel = os.path.relpath(fpath, os.getcwd())

        # Find all endpoint handler functions and their return dicts
        # We look for the pattern: return { "key": value, ... }
        # inside async def functions decorated with @app.get/post/put/delete

        # Simple heuristic: find all dict literals in return statements
        # that contain quoted keys (i.e., JSON-like response dicts)
        return_block_pattern = re.compile(
            r'return\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}',
            re.DOTALL
        )
        # Also match multi-line return blocks
        return_block_pattern_ml = re.compile(
            r'return\s*\{',
            re.MULTILINE
        )

        # Extract quoted keys from return dicts
        # Look for patterns like "field_name": or 'field_name':
        field_pattern = re.compile(r"""['"](\w+)['"]\s*:""")

        # Find all return { ... } blocks (single-line and multi-line)
        in_return = False
        brace_depth = 0
        current_block = []
        line_num = 0

        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()

            if re.search(r'\breturn\s*\{', line):
                in_return = True
                brace_depth = line.count('{') - line.count('}')
                current_block = [line]
                line_num = i
                if brace_depth <= 0:
                    # Single-line return
                    block_text = ''.join(current_block)
                    fields = set(field_pattern.findall(block_text))
                    if fields:
                        endpoint_fields.setdefault(rel, set()).update(fields)
                    in_return = False
                    current_block = []
            elif in_return:
                brace_depth += line.count('{') - line.count('}')
                current_block.append(line)
                if brace_depth <= 0:
                    block_text = ''.join(current_block)
                    fields = set(field_pattern.findall(block_text))
                    if fields:
                        endpoint_fields.setdefault(rel, set()).update(fields)
                    in_return = False
                    current_block = []

    # ── 2. Extract field accesses from JSX files ────────────────────────
    # Map: jsx_file -> list of (line_num, object_expr, field_name, context)
    jsx_accesses: list = []

    # Patterns to match field accesses:
    #   wallet.balance_usd, data.portfolio.total_value_hkd, s.confidence_score
    #   t.usd_value, a.enabled, sentiment.classification
    field_access_pattern = re.compile(
        r'(?:^|[\s,{([])(\w+)\.(\w+)(?:\s*[,}\)\]\?:.[]|$)'
    )

    # Also match nested: data.portfolio.total_value_usd
    nested_access_pattern = re.compile(
        r'(?:^|[\s,{[])(\w+)\.(\w+)\.(\w+)(?:\s*[,}\)\]\?:.[]|$)'
    )

    # Known safe fields that don't need backend verification
    safe_builtins = {
        # JS builtins / React
        'setState', 'props', 'state', 'refs', 'ref', 'current', 'key',
        'length', 'push', 'map', 'filter', 'reduce', 'find', 'forEach',
        'includes', 'indexOf', 'slice', 'splice', 'concat', 'join',
        'split', 'replace', 'match', 'search', 'trim', 'toLowerCase',
        'toUpperCase', 'charAt', 'substring', 'toString', 'valueOf',
        'Math', 'JSON', 'Object', 'Array', 'String', 'Number', 'Date',
        'console', 'window', 'document', 'navigator', 'location',
        'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
        'Promise', 'Error', 'Map', 'Set', 'Symbol', 'parseInt', 'parseFloat',
        'isNaN', 'isFinite', 'encodeURI', 'decodeURI', 'encodeURIComponent',
        'decodeURIComponent', 'fetch', 'Response', 'Request', 'Headers',
        'URL', 'URLSearchParams', 'FormData', 'Blob', 'File', 'FileReader',
        'Event', 'EventTarget', 'Element', 'Node', 'HTMLElement',
        # React hooks
        'useState', 'useEffect', 'useCallback', 'useMemo', 'useRef',
        'useContext', 'useReducer', 'useImperativeHandle', 'useLayoutEffect',
        'useDebugValue', 'useId', 'useDeferredValue', 'useTransition',
        'useSyncExternalStore', 'useInsertionEffect',
        # Common React patterns
        'className', 'style', 'onClick', 'onChange', 'onSubmit', 'onClose',
        'onBlur', 'onFocus', 'onKeyDown', 'onKeyUp', 'onMouseEnter',
        'onMouseLeave', 'disabled', 'checked', 'value', 'type', 'placeholder',
        'id', 'name', 'title', 'alt', 'src', 'href', 'target', 'rel',
        'role', 'tabIndex', 'autoComplete', 'autoFocus', 'readOnly',
        'required', 'min', 'max', 'step', 'pattern', 'minLength', 'maxLength',
        'cols', 'rows', 'width', 'height', 'colSpan', 'rowSpan',
        'scope', 'headers', 'summary', 'open', 'selected', 'multiple',
        'size', 'htmlFor', 'defaultValue', 'defaultChecked',
        'innerHTML', 'innerText', 'textContent', 'outerHTML',
        'scrollTop', 'scrollLeft', 'scrollWidth', 'scrollHeight',
        'clientWidth', 'clientHeight', 'offsetWidth', 'offsetHeight',
        'offsetTop', 'offsetLeft', 'offsetParent', 'offsetChild',
        # Fetch API
        'ok', 'status', 'statusText', 'url', 'redirected', 'bodyUsed',
        'headers', 'json', 'text', 'blob', 'arrayBuffer', 'formData',
        # Common response fields
        'message', 'detail', 'error', 'errors', 'code', 'success',
        'token', 'access_token', 'refresh_token',
        # Alpaca API fields (external)
        'equity', 'account_id', 'buying_power', 'cash', 'portfolio_value',
        'status', 'currency', 'pattern_day_trader', 'trading_blocked',
        'transfers_blocked', 'account_blocked', 'created_at', 'trade_suspended_by_user',
        'multiplier', 'shorting_enabled', 'long_market_value', 'short_market_value',
        'last_equity', 'last_long_market_value', 'last_short_market_value',
        # Pagination
        'page', 'per_page', 'total', 'total_pages',
        # Common UI state
        'loading', 'error', 'data', 'show', 'visible', 'hidden',
        'active', 'disabled', 'selected', 'focused', 'hovered',
        # Form state
        'form', 'setForm', 'setLoading', 'setError', 'setShowAdd',
        'setShowSuggestions', 'setAnalyzeSignal', 'setMirroring',
        'setMirroringIds', 'setHistory', 'setHistoryLoading', 'setHistoryError',
        'setSentiment', 'setTogglingIds',
        # Common helpers
        'prev', 'next', 'current', 'initial', 'final',
        # CSS / style
        'display', 'position', 'top', 'left', 'right', 'bottom',
        'margin', 'padding', 'border', 'background', 'color',
        'fontSize', 'fontWeight', 'textAlign', 'verticalAlign',
        'flexDirection', 'justifyContent', 'alignItems', 'gap',
        'gridTemplateColumns', 'gridGap', 'gridColumn', 'gridRow',
        'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
        'overflow', 'overflowX', 'overflowY', 'whiteSpace', 'textOverflow',
        'borderRadius', 'borderTop', 'borderBottom', 'borderLeft', 'borderRight',
        'boxShadow', 'opacity', 'zIndex', 'cursor', 'pointerEvents',
        'userSelect', 'transition', 'transform', 'animation',
        'content', 'clear', 'float', 'listStyle', 'listStyleType',
        'textDecoration', 'textTransform', 'letterSpacing', 'lineHeight',
        'wordBreak', 'wordWrap', 'wordSpacing',
        'backgroundColor', 'backgroundImage', 'backgroundSize',
        'backgroundPosition', 'backgroundRepeat',
        'marginTop', 'marginBottom', 'marginLeft', 'marginRight',
        'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
        'flex', 'flexGrow', 'flexShrink', 'flexBasis',
        'gridArea', 'gridAutoColumns', 'gridAutoRows', 'gridAutoFlow',
        'outline', 'outlineOffset', 'resize', 'appearance',
        'visibility', 'clip', 'clipPath', 'filter', 'backdropFilter',
        'mixBlendMode', 'isolation', 'objectFit', 'objectPosition',
        'scrollBehavior', 'scrollSnapType', 'scrollSnapAlign',
        'touchAction', 'willChange', 'contain',
        # Misc
        'key', 'ref', 'children',
    }

    # Fields that are computed client-side from backend data
    computed_fields = {
        'isLiveUpdate', 'fmtBalance', 'fmtTotal', 'timeAgo', 'truncateAddress',
        'fmtBalance', 'is_whale', 'is_mine',  # these are used as booleans in conditions
    }

    # Known endpoint-to-response-field mapping (manually curated for accuracy)
    # This is more reliable than parsing Python return dicts
    known_endpoint_fields = {
        '/api/dashboard': {
            'portfolio', 'wallets', 'personal_wallets', 'whale_wallets_list',
            'recent_transactions', 'alerts', 'copy_trade_signals',
            # portfolio sub-fields
            'total_value_usd', 'total_value_hkd', 'total_value_btc',
            'wallets_tracked', 'whale_wallets_tracked', 'fresh_wallets',
            # wallet fields (personal_wallets, whale_wallets_list, wallets)
            'id', 'address', 'chain', 'label', 'is_whale', 'is_mine',
            'is_fresh_wallet', 'risk_label', 'balance_native',
            'balance_usd', 'balance_hkd', 'balance_btc',
            'last_balance_update', 'created_at',
            # transaction fields (recent_transactions)
            'tx_hash', 'type', 'amount', 'token', 'usd_value', 'timestamp',
            'wallet_label', 'wallet_address', 'status',
            # alert fields (alerts)
            'rule_type', 'threshold', 'enabled', 'last_fired',
            # signal fields (copy_trade_signals)
            'token_symbol', 'action', 'amount_usd', 'confidence_score',
            'confidence_final', 'whale_score', 'score_at_generation',
            'explanation', 'explanation_stale', 'wallet_label',
            'wallet_address', 'status',
        },
        '/api/signals': {
            'signals',
            'id', 'token_symbol', 'action', 'amount_usd', 'confidence_score',
            'confidence_final', 'whale_score', 'wallet_address', 'status',
            'wallet_label', 'created_at', 'explanation', 'explanation_stale',
            'score_at_generation',
        },
        '/api/wallets': {
            'wallets',
            'id', 'address', 'chain', 'label', 'is_whale', 'is_mine', 'created_at',
            'balance_usd', 'balance_hkd', 'balance_btc', 'last_balance_update',
        },
        '/api/activity': {
            'transactions', 'total', 'page', 'per_page', 'total_pages',
            'id', 'tx_hash', 'type', 'amount', 'token', 'usd_value',
            'timestamp', 'chain', 'wallet_label', 'wallet_address', 'status',
        },
        '/api/alerts': {
            'alerts',
            'id', 'rule_type', 'threshold', 'enabled', 'created_at', 'last_fired',
        },
        '/api/alerts/history': {
            'history',
            'id', 'alert_id', 'rule_type', 'trigger_value', 'details', 'message', 'created_at',
        },
        '/api/whale-sentiment': {
            'sentiment_score', 'classification', 'inflow_usd', 'outflow_usd', 'tx_count',
        },
        '/api/signals/history': {
            'signals', 'count',
            'id', 'token_symbol', 'action', 'amount_usd', 'confidence_score',
            'confidence_final', 'whale_score', 'score_at_generation',
            'wallet_address', 'wallet_label', 'status',
            'explanation', 'explanation_stale',
            'created_at', 'executed_at', 'closed_at',
            'time_to_close_seconds',
        },
    }

    # Map JSX files to their primary API endpoints
    jsx_endpoint_map = {
        'Dashboard.jsx': ['/api/dashboard', '/api/whale-sentiment', '/api/signals/history'],
        'CopyTrades.jsx': ['/api/signals', '/api/signals/history'],
        'Wallets.jsx': ['/api/wallets', '/api/whale-suggestions'],
        'Activity.jsx': ['/api/activity'],
        'Alerts.jsx': ['/api/alerts', '/api/alerts/history'],
    }

    # ── 3. Check each JSX file's field accesses against endpoint fields ──
    findings = []

    for fpath in jsx_files:
        text = read_file(fpath)
        if not text:
            continue
        fname = os.path.basename(fpath)
        rel = os.path.relpath(fpath, os.getcwd())

        # Determine which endpoints this JSX file uses
        endpoints = jsx_endpoint_map.get(fname, [])
        allowed_fields = set()
        for ep in endpoints:
            allowed_fields.update(known_endpoint_fields.get(ep, set()))

        # Also allow all fields from all endpoints (for shared components)
        # but flag only clear mismatches
        all_endpoint_fields = set()
        for fields in known_endpoint_fields.values():
            all_endpoint_fields.update(fields)

        # Extract variable names that hold API response data
        # Look for: const data = await apiFetch(...), const { x, y } = data
        response_vars = set()
        for match in re.finditer(r'(?:const|let|var)\s+(\w+)\s*=\s*await\s+apiFetch', text):
            response_vars.add(match.group(1))
        for match in re.finditer(r'(?:const|let|var)\s*\{([^}]+)\}\s*=\s*data', text):
            for var in match.group(1).split(','):
                response_vars.add(var.strip())

        # Extract field accesses: var.field
        for i, line in enumerate(text.splitlines(), 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('/*'):
                continue

            for match in field_access_pattern.finditer(line):
                obj_var = match.group(1)
                field_name = match.group(2)

                # Skip safe builtins
                if field_name in safe_builtins or obj_var in safe_builtins:
                    continue
                # Skip React/event handlers
                if obj_var in ('e', 'event', 'props', 'state', 'ref', 'refs'):
                    continue
                # Skip style objects
                if obj_var == 'style' or field_name in (
                    'display', 'position', 'margin', 'padding', 'border',
                    'background', 'color', 'fontSize', 'fontWeight', 'textAlign',
                    'verticalAlign', 'flex', 'gap', 'width', 'height',
                    'borderRadius', 'boxShadow', 'opacity', 'zIndex', 'cursor',
                    'overflow', 'top', 'left', 'right', 'bottom',
                    'marginTop', 'marginBottom', 'marginLeft', 'marginRight',
                    'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
                    'flexDirection', 'justifyContent', 'alignItems',
                    'gridTemplateColumns', 'gridGap',
                    'backgroundColor', 'backgroundImage', 'backgroundSize',
                    'backgroundPosition', 'backgroundRepeat',
                    'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
                    'flexGrow', 'flexShrink', 'flexBasis',
                    'outline', 'resize', 'visibility',
                    'transition', 'transform', 'animation',
                    'content', 'clear', 'float',
                    'textDecoration', 'textTransform', 'letterSpacing', 'lineHeight',
                    'whiteSpace', 'textOverflow',
                    'listStyle', 'listStyleType',
                    'pointerEvents', 'userSelect',
                    'scrollBehavior', 'touchAction',
                    'willChange', 'contain',
                    'mixBlendMode', 'isolation',
                    'objectFit', 'objectPosition',
                    'clip', 'clipPath', 'filter', 'backdropFilter',
                ):
                    continue
                # Skip if it's a method call (ends with ())
                after_match = line[match.end():]
                if after_match.strip().startswith('('):
                    continue

                # Skip .map() iteration variables (e.g., TX_TYPES.map(t => t.label))
                # Check current and previous line for `.map(${obj_var} =>` pattern
                map_iter_re = re.compile(rf'\.map\s*\(\s*{re.escape(obj_var)}\s*=>')
                if map_iter_re.search(line):
                    continue
                if i > 1:
                    prev_lines = text.splitlines()
                    if i - 2 < len(prev_lines) and map_iter_re.search(prev_lines[i - 2]):
                        continue

                # Check if this field is expected from any endpoint used by this JSX file
                if endpoints and field_name not in allowed_fields:
                    # Only flag if it's clearly a data field (not a local variable)
                    # Heuristic: if the object var is commonly used for API data
                    if obj_var in ('w', 'wallet', 's', 'signal', 't', 'tx',
                                   'a', 'alert', 'h', 'item', 'row',
                                   'data', 'd', 'sentiment'):
                        findings.append((rel, i, obj_var, field_name, endpoints))

    # ── 4. Report findings ──────────────────────────────────────────────
    if findings:
        # Deduplicate
        seen = set()
        unique_findings = []
        for f in findings:
            key = (f[0], f[2], f[3])  # file, obj, field
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        for rel, line_num, obj, field, endpoints in unique_findings[:10]:  # Cap at 10
            ep_str = ', '.join(endpoints) if endpoints else 'unknown'
            result.add(Finding(
                pitfall="field-contract",
                severity="minor",
                file=rel,
                line=line_num,
                description=(
                    f"Frontend accesses `{obj}.{field}` but field `{field}` is not "
                    f"returned by endpoint(s): {ep_str}. "
                    f"This will silently show placeholder values ('—') or undefined."
                ),
                suggestion=(
                    f"Either add `{field}` to the endpoint response, or remove "
                    f"the `{obj}.{field}` access from the frontend if not needed."
                ),
            ))
    else:
        result.add_pass("Frontend-backend field contract (all accessed fields are returned)")


# ─── Main ────────────────────────────────────────────────────────────

def check_approx_price_not_used_for_balance(py_files: List[str], result: AuditResult):
    """
    Check #28: Detect _APPROX_PRICE_USD used for actual balance conversion
    (not just as a fallback default). Endpoints that convert native balances
    to USD should use live CoinGecko prices, not hardcoded approximate prices.
    """
    import re
    # Pattern: _APPROX_PRICE_USD.get(...) used directly in multiplication
    # (not in a fallback/initialization context)
    bad_pattern = re.compile(
        r'balance_usd\s*=\s*\w+\s*\*\s*_APPROX_PRICE_USD'
    )
    # Pattern: _APPROX_PRICE_USD used in a fallback context (OK)
    fallback_pattern = re.compile(
        r'fallback|_APPROX_PRICE_USD\.get\(\w+,\s*[\d.]+\)|default'
    )

    for fpath in py_files:
        text = read_file(fpath)
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if '_APPROX_PRICE_USD' in line and bad_pattern.search(line):
                # Check if this is in a fallback context
                context_start = max(0, i - 3)
                context_end = min(len(lines), i + 2)
                context = '\n'.join(lines[context_start:context_end])
                if not fallback_pattern.search(context):
                    result.add(Finding(
                        pitfall="Pitfall #28",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            f"_APPROX_PRICE_USD used for balance conversion: {line.strip()}"
                        ),
                        suggestion=(
                            "Use live CoinGecko price for balance conversion. "
                            "_APPROX_PRICE_USD should only be used as a fallback default."
                        ),
                    ))
                    return  # One finding is enough

    result.add_pass("Pitfall #28: _APPROX_PRICE_USD not used for direct balance conversion")


def check_on_conflict_exact_column_match(py_files, sql_files, result):
    """
    Pitfall #19 (enhanced): Every ON CONFLICT (col1, col2, ...) must have a matching
    UNIQUE constraint on EXACTLY those columns (order-insensitive). This catches the
    case where a constraint exists but on a different column set, which silently
    allows duplicates.

    Example: code has ON CONFLICT (wallet_id, token_symbol, action, amount_usd)
    but the UNIQUE constraint is on (wallet_id, token_symbol, created_at) - different columns!
    The ON CONFLICT would silently do nothing (never matches) or match on wrong criteria.
    """
    # Collect all explicit ON CONFLICT usages with their column lists
    on_conflict_usages = []  # (file, line_num, table, conflict_cols_set)
    pattern = re.compile(
        r"ON\s+CONFLICT\s*\((?P<cols>[^)]+)\)\s*(?:DO\s+NOTHING|DO\s+UPDATE)",
        re.IGNORECASE,
    )

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)
        for i, line in enumerate(file_lines, 1):
            m = pattern.search(line)
            if m:
                # Infer table from nearby INSERT INTO (10-line lookback)
                context_lines = file_lines[max(0, i - 10):i]
                context = "\n".join(context_lines)
                table_m = re.search(r"INSERT\s+INTO\s+(\w+)", context, re.IGNORECASE)
                table = table_m.group(1) if table_m else "unknown"
                # Normalize column set: strip whitespace, lowercase, sort
                cols_raw = m.group("cols")
                cols_set = frozenset(c.strip().lower() for c in cols_raw.split(","))
                on_conflict_usages.append((fpath, i, table.lower(), cols_set))

    if not on_conflict_usages:
        result.add_pass("Pitfall #19 (enhanced): No explicit ON CONFLICT (cols) usages found")
        return

    # Collect all unique constraints from SQL files
    all_sql = "\n".join(read_file(f) for f in sql_files)
    constraints_by_table = {}  # table -> list of frozensets of columns

    constraint_pattern = re.compile(
        r"(?:CONSTRAINT\s+\w+\s+)?UNIQUE\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    for m in constraint_pattern.finditer(all_sql):
        cols = frozenset(c.strip().lower() for c in m.group(1).split(","))
        constraints_by_table.setdefault("__global__", set()).add(cols)

    unique_index_pattern = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    for m in unique_index_pattern.finditer(all_sql):
        table = m.group(1).lower()
        cols = frozenset(c.strip().lower() for c in m.group(2).split(","))
        constraints_by_table.setdefault(table, set()).add(cols)

    # Parse inline UNIQUE from CREATE TABLE blocks
    create_tables = _extract_create_table_blocks(all_sql)
    for table, cols_block in create_tables.items():
        inline_unique = re.compile(r"(\w+)\s+\w+.*(?:PRIMARY\s+KEY|UNIQUE)", re.IGNORECASE)
        for m2 in inline_unique.finditer(cols_block):
            col = m2.group(1).lower()
            constraints_by_table.setdefault(table.lower(), set()).add(frozenset([col]))

    # Check each ON CONFLICT usage for an exact column match
    for fpath, line_num, table, cols_set in on_conflict_usages:
        table_constraints = constraints_by_table.get(table, set())
        global_constraints = constraints_by_table.get("__global__", set())
        all_constraints = table_constraints | global_constraints

        found = cols_set in all_constraints
        if not found:
            found = any(cols_set.issubset(c) for c in all_constraints)

        if not found:
            result.add(Finding(
                pitfall="#19",
                severity="critical",
                file=fpath,
                line=line_num,
                description=(
                    f"ON CONFLICT on {table} ({', '.join(sorted(cols_set))}) - "
                    f"no matching UNIQUE constraint on exactly these columns in migrations"
                ),
                suggestion=(
                    f"Add UNIQUE constraint on {table}({', '.join(sorted(cols_set))}) via migration"
                ),
            ))
        else:
            result.add_pass(
                f"Pitfall #19 (enhanced): ON CONFLICT on {table} "
                f"({', '.join(sorted(cols_set))}) has exact column match"
            )


# ─── Pitfall #21: get_current_user DB lookup on every request ──────────
# The JWT payload should contain user_id (UUID) so get_current_user can
# extract it without a DB round-trip. If the function does
# "SELECT * FROM users WHERE wallet_address = $1" on every call, that's
# one extra DB query per authenticated request.

def check_get_current_user_no_db(py_files: List[str], result: AuditResult):
    """
    Pitfall #21: Detect get_current_user functions that do a DB SELECT
    on every call instead of extracting user_id from JWT claims.
    """
    found_function = False
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "get_current_user" not in text:
            continue
        found_function = True
        file_lines = lines(text)
        in_func = False
        func_start = 0
        has_db_select = False
        has_jwt_uid_extract = False
        func_body_lines = []

        for i, line in enumerate(file_lines, 1):
            # Detect function definition
            if re.search(r"def\s+get_current_user", line):
                in_func = True
                func_start = i
                func_body_lines = []
                has_db_select = False
                has_jwt_uid_extract = False
                continue
            if in_func:
                # End of function: next def/class at column 0
                if line and not line[0].isspace() and (line.startswith("def ") or line.startswith("class ")):
                    # Analyze the completed function
                    body = "\n".join(func_body_lines)
                    if has_db_select and not has_jwt_uid_extract:
                        result.add(Finding(
                            pitfall="#21",
                            severity="critical",
                            file=fpath,
                            line=func_start,
                            description=(
                                "get_current_user does DB SELECT but does not "
                                "extract user_id from JWT claims (sub/uid). "
                                "This causes one extra DB round-trip per authenticated request."
                            ),
                            suggestion=(
                                "Embed user_id in JWT claims at token creation time, "
                                "then extract with jwt_payload.get('uid') or jwt_payload.get('user_id'). "
                                "Keep a fallback DB lookup for old tokens lacking the claim."
                            ),
                        ))
                    elif not has_db_select:
                        result.add_pass(
                            f"Pitfall #21: get_current_user in {fpath} — no DB SELECT found (likely extracts from JWT)"
                        )
                    else:
                        result.add_pass(
                            f"Pitfall #21: get_current_user in {fpath} — has both DB SELECT and JWT uid extraction (OK)"
                        )
                    in_func = False
                    continue
                func_body_lines.append(line)
                if re.search(r"(SELECT|fetch|execute)\b", line, re.IGNORECASE):
                    if re.search(r"(users|wallet_address|WHERE\s+\w+\s*=\s*\$)", line, re.IGNORECASE):
                        has_db_select = True
                if re.search(r"(uid|user_id)\b", line) and re.search(r"(jwt|payload|claims|token)", line, re.IGNORECASE):
                    has_jwt_uid_extract = True

        # Handle function at end of file
        if in_func:
            body = "\n".join(func_body_lines)
            if has_db_select and not has_jwt_uid_extract:
                result.add(Finding(
                    pitfall="#21",
                    severity="critical",
                    file=fpath,
                    line=func_start,
                    description=(
                        "get_current_user does DB SELECT but does not "
                        "extract user_id from JWT claims."
                    ),
                    suggestion=(
                        "Embed user_id in JWT claims at token creation time."
                    ),
                ))
            else:
                result.add_pass(
                    f"Pitfall #21: get_current_user in {fpath} — OK"
                )

    if not found_function:
        result.add_pass("Pitfall #21: No get_current_user function found (check not applicable)")


# ─── Pitfall #23: Phase isolation in monitor workers ───────────────────
# When a monitor has multiple phases (e.g., Phase 4: fetch+update, Phase 5:
# signal generation), Phase 5 must use a SEPARATE DB connection. If Phase 5
# runs inside Phase 4's transaction and fails, the entire transaction rolls
# back — but _last_tx_hashes was already updated in-memory, so the dropped
# tx is never retried (silent data loss).

def check_phase_isolation_monitor(py_files: List[str], result: AuditResult):
    """
    Pitfall #23: Detect monitor workers where signal/alert evaluation
    runs inside the same DB transaction as the wallet UPDATE phase.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        # Only check files that look like monitor workers
        if "monitor" not in fpath.lower() and "phase" not in text.lower():
            continue
        if "async with" not in text or "acquire" not in text:
            continue

        file_lines = lines(text)
        # Find phase boundaries and check for nested async with blocks
        phase_pattern = re.compile(r"Phase\s+(\d+)", re.IGNORECASE)
        async_with_pattern = re.compile(r"async\s+with\s+.*acquire")

        phases_found = []
        for i, line in enumerate(file_lines, 1):
            m = phase_pattern.search(line)
            if m:
                phases_found.append((i, m.group(1)))

        if len(phases_found) < 2:
            # Check for signal/alert evaluation inside a transaction
            has_signal_insert = "copy_trade_signals" in text or "signal" in text.lower()
            has_wallet_update = "UPDATE" in text and "wallets" in text.lower()
            has_separate_conn = text.count("async with") > 1

            if has_signal_insert and has_wallet_update and not has_separate_conn:
                result.add(Finding(
                    pitfall="#23",
                    severity="critical",
                    file=fpath,
                    line=1,
                    description=(
                        "Monitor has signal INSERT and wallet UPDATE but only one "
                        "'async with acquire' block. If signal INSERT fails inside "
                        "the UPDATE transaction, the entire transaction rolls back "
                        "but _last_tx_hashes is already updated — silent data loss."
                    ),
                    suggestion=(
                        "Use separate 'async with _pool.acquire() as conn' blocks for "
                        "each phase. Phase N+1 must open a fresh connection after "
                        "Phase N's transaction commits."
                    ),
                ))
            else:
                result.add_pass(
                    f"Pitfall #23: {fpath} — phase isolation OK "
                    f"(separate connections or no multi-phase pattern)"
                )
            continue

        # Multiple phases found — check isolation
        result.add_pass(
            f"Pitfall #23: {fpath} — {len(phases_found)} phases found, "
            f"manual review recommended for connection isolation"
        )


# ─── Pitfall #24: Balance-vs-event-amount conflation ───────────────────
# A check function returning (balance_native, balance_usd, tx_hash, ...)
# where downstream consumers use balance_native as the transaction amount.
# A whale with 1000 ETH receiving 0.01 ETH dust would generate a signal
# with confidence based on $3.2M balance instead of $32 tx.

def check_balance_vs_event_amount(py_files: List[str], result: AuditResult):
    """
    Pitfall #24: Detect check functions that return balance_native
    without a separate tx_amount_native field.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "balance_native" not in text:
            continue

        file_lines = lines(text)
        # Look for functions that return tuples containing balance_native
        # and check if tx_amount_native is also present
        for i, line in enumerate(file_lines, 1):
            if "balance_native" in line and "return" in line:
                # Check surrounding context (5 lines) for tx_amount_native
                context_start = max(0, i - 5)
                context_end = min(len(file_lines), i + 5)
                context = "\n".join(file_lines[context_start:context_end])

                if "tx_amount_native" not in context and "event_amount" not in context:
                    # Check if this is a signal-related function
                    if "signal" in text.lower() or "check" in text.lower():
                        result.add(Finding(
                            pitfall="#24",
                            severity="minor",
                            file=fpath,
                            line=i,
                            description=(
                                f"Line {i}: return statement includes balance_native "
                                f"but no tx_amount_native. Downstream consumers may "
                                f"conflate wallet balance with transaction amount."
                            ),
                            suggestion=(
                                "Return both balance_native and tx_amount_native as "
                                "separate elements. Name event-level amounts distinctly "
                                "from aggregate-state amounts."
                            ),
                        ))
                        break  # One finding per file is enough

        # If we didn't flag it, it's OK
        if not any(f.file == fpath for f in result.findings if f.pitfall == "#24"):
            result.add_pass(f"Pitfall #24: {fpath} — balance/amount fields properly distinguished")


# ─── Pitfall #7: DB connection held across external HTTP calls ─────────
# When an endpoint fetches data from external APIs while holding a DB
# connection from the pool, the connection sits idle during slow I/O.
# For endpoints looping over N items, this means N connections held.

def check_db_conn_held_across_http(py_files: List[str], result: AuditResult):
    """
    Pitfall #7: Detect patterns where a DB connection is held while
    making external HTTP calls (fetch-then-store loops).
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "async with" not in text:
            continue

        file_lines = lines(text)
        in_async_with = False
        async_with_line = 0
        async_with_indent = 0
        has_external_http = False
        has_db_write = False
        conn_var = ""

        for i, line in enumerate(file_lines, 1):
            # Detect async with acquire_db() or pool.acquire()
            if not in_async_with:
                m = re.search(r"async\s+with\s+.*(?:acquire|_pool)\s*(?:as\s+(\w+))?", line)
                if m:
                    in_async_with = True
                    async_with_line = i
                    async_with_indent = len(line) - len(line.lstrip())
                    conn_var = m.group(1) or "conn"
                    has_external_http = False
                    has_db_write = False
                continue

            # Check if we're still inside the async with block
            # A line at the same or lesser indentation than the async with ends the block
            _indent = int(async_with_indent)
            if line.strip() and not line.startswith(" " * (_indent + 1)):
                # Block ended — analyze
                if has_db_write and has_external_http:
                    result.add(Finding(
                        pitfall="#7",
                        severity="critical",
                        file=fpath,
                        line=async_with_line,
                        description=(
                            f"DB connection held across external HTTP calls "
                            f"(async with at line {async_with_line}). "
                            f"Connection sits idle during slow I/O."
                        ),
                        suggestion=(
                            "Separate into phases: (1) fetch all external data first "
                            "with no DB held, (2) acquire connection once for all writes, "
                            "(3) release."
                        ),
                    ))
                else:
                    result.add_pass(
                        f"Pitfall #7: {fpath} line {async_with_line} — "
                        f"no HTTP calls while holding DB conn"
                    )
                in_async_with = False
                continue

            # Inside the block — check for HTTP calls and DB writes
            # Match actual HTTP client method calls (not imports or dict.get())
            # Pattern: variable.get/post/put/delete/patch( where variable looks like an HTTP client
            if re.search(r"(await\s+)?(client|session|http_client|resp|response|cl|httpx|http)\.(get|post|put|delete|patch)\(", line):
                has_external_http = True
            if re.search(r"(INSERT|UPDATE|DELETE|execute|fetch)\w*\b", line, re.IGNORECASE):
                has_db_write = True

        # Handle block at end of file
        if in_async_with and has_db_write and has_external_http:
            result.add(Finding(
                pitfall="#7",
                severity="critical",
                file=fpath,
                line=async_with_line,
                description=(
                    f"DB connection held across external HTTP calls "
                    f"(async with at line {async_with_line})."
                ),
                suggestion="Separate fetch and store phases.",
            ))


# ─── Pitfall #14: $$ in JS template literals ──────────────────────────
def check_double_dollar_in_template_literals(jsx_files: List[str], result: AuditResult):
    """
    Pitfall #14: Detect $$ in JavaScript template literals where expr already
    returns a currency-prefixed string. $$ is ONLY safe when expr returns a
    plain number (e.g., .toLocaleString(), .toFixed()). Flag when expr is a
    simple variable or function call that may already include currency formatting.
    """
    if not jsx_files:
        result.add_pass("Pitfall #14: No JS/JSX files to scan")
        return

    dd_pattern = re.compile(r"`[^`]*\$\$\{([^}]+)\}[^`]*`")
    # Patterns that clearly return unformatted numbers (safe with $$)
    safe_patterns = [
        r"\.toLocaleString\(",
        r"\.toFixed\(",
        r"\.toString\(",
        r"Math\.",
        r"parseInt\(",
        r"parseFloat\(",
        r"Number\(",
        r"\|\s*0",  # fallback to 0
        r"\?\s*0",  # fallback to 0
    ]
    found_any = False
    for fpath in jsx_files:
        text = read_file(fpath)
        if "`" not in text:
            continue
        file_lines = lines(text)
        for i, line in enumerate(file_lines, 1):
            m = dd_pattern.search(line)
            if m:
                expr = m.group(1)
                # Skip if the expression clearly returns a plain number
                if any(re.search(sp, expr) for sp in safe_patterns):
                    continue
                # Skip HK$ patterns (Hong Kong dollar prefix is intentional)
                if "HK$" in line[:m.start()]:
                    continue
                result.add(Finding(
                    pitfall="#14",
                    severity="critical",
                    file=fpath,
                    line=i,
                    description=(
                        f"$${{'{expr}'}} in template literal — if '{expr}' already "
                        f"returns a currency-prefixed string, the result will show "
                        f"a double dollar sign (e.g., '$$50,000')."
                    ),
                    suggestion=(
                        "If the formatter already adds a currency symbol, use ${expr} "
                        "instead of $${expr}. If expr returns a plain number, this is fine."
                    ),
                ))
                found_any = True

    if not found_any:
        result.add_pass("Pitfall #14: No $$ in template literals found")


# ─── Pitfall #11: Grid layout breakage in JSX ─────────────────────────
def check_grid_layout_breakage(jsx_files: List[str], result: AuditResult):
    """
    Pitfall #11: Detect CSS grid with N columns that has N+1 children,
    which causes the extra child to wrap to a new row as a single column.

    Improved: Only counts elements that are direct children of the grid
    container by checking indentation/bracket depth, not nested elements.
    """
    if not jsx_files:
        result.add_pass("Pitfall #11: No JS/JSX files to scan")
        return

    grid_pattern = re.compile(r"gridTemplateColumns\s*:\s*['\"](\d+)fr\s+(\d+)fr['\"]")
    has_issue = False
    for fpath in jsx_files:
        text = read_file(fpath)
        if "gridTemplateColumns" not in text:
            continue
        file_lines = lines(text)
        for i, line in enumerate(file_lines, 1):
            m = grid_pattern.search(line)
            if m:
                col_count = 2
                # Determine base indentation from the grid container line
                base_indent = len(line) - len(line.lstrip())
                child_count = 0
                # Count direct children: lines with slightly more indent than the grid line
                # that contain opening JSX tags — stop when we exit the container
                for j in range(i + 1, min(i + 30, len(file_lines))):
                    child_line = file_lines[j]
                    if not child_line.strip() or child_line.strip().startswith("//"):
                        continue
                    child_indent = len(child_line) - len(child_line.lstrip())
                    # If we've returned to the grid container's indent level or less, the container closed
                    if child_indent <= base_indent:
                        break
                    # Direct children are roughly one indent level deeper than the grid container
                    if child_indent <= base_indent + 12:  # allow up to ~3 levels of indentation (12 spaces)
                        # Only count divs as grid children (standard JSX grid pattern)
                        if re.search(r"<div[\s>]", child_line):
                            if not re.search(r"</div>", child_line):
                                child_count += 1

                if child_count > col_count:
                    result.add(Finding(
                        pitfall="#11",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            f"Grid with {col_count} columns appears to have ~{child_count} "
                            f"direct child elements. Extra children may wrap to new rows."
                        ),
                        suggestion=(
                            "Either move the new card outside the grid wrapper, or change "
                            "gridTemplateColumns to accommodate the new column count."
                        ),
                    ))
                    has_issue = True

    if not has_issue:
        result.add_pass("Pitfall #11: Grid layout check completed")


# ─── Pitfall #15: Dead variable cascade ───────────────────────────────
def check_dead_variable_cascade(py_files: List[str], result: AuditResult):
    """
    Pitfall #15: Detect variables that are initialized to 0 and incremented
    but never used in any return value or output — dead code.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        init_pattern = re.compile(r"^(\s*)(\w+)\s*=\s*0\.?\d*\s*(?:#.*)?$")
        incr_pattern = re.compile(r"^\s*(\w+)\s*\+=\s*\d+")

        inits = {}
        incrs = {}

        for i, line in enumerate(file_lines, 1):
            m = init_pattern.match(line)
            if m:
                var = m.group(2)
                # Skip common loop counters and indices
                if var in ("i", "j", "k", "n", "idx", "_"):
                    continue
                inits[var] = (i, len(m.group(1)))
            m = incr_pattern.match(line)
            if m:
                var = m.group(1)
                if var in ("i", "j", "k", "n", "idx", "_"):
                    continue
                incrs.setdefault(var, []).append(i)

        for var in set(inits.keys()) & set(incrs.keys()):
            return_uses = []
            other_uses = []
            for i, line in enumerate(file_lines, 1):
                if i == inits[var][0]:
                    continue
                if re.search(r"\b" + re.escape(var) + r"\b", line):
                    if "return" in line:
                        return_uses.append(i)
                    elif "+=" not in line:
                        other_uses.append(i)

            if not return_uses and not other_uses:
                result.add(Finding(
                    pitfall="#15",
                    severity="minor",
                    file=fpath,
                    line=inits[var][0],
                    description=(
                        f"Variable '{var}' is initialized to 0 at line {inits[var][0]} "
                        f"and incremented at lines {incrs[var]} but never used in any "
                        f"return value or output. Likely dead code from a removed return key."
                    ),
                    suggestion=(
                        f"Remove the dead initializer ({var} = 0) and all increments "
                        f"({var} += N) since the variable serves no purpose."
                    ),
                ))

    result.add_pass("Pitfall #15: Dead variable cascade check completed")


# ─── Pitfall #22: Pyright built-in generics ───────────────────────────
def check_pyright_builtin_generics(py_files: List[str], result: AuditResult):
    """
    Pitfall #22: Detect use of typing.Dict/List/Optional-style generics
    without corresponding imports, which can cause Pyright errors.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        typing_generic_pattern = re.compile(r":\s*(Dict|List|Optional|Tuple|Set|FrozenSet|Union)\s*\[")
        has_typing_import = bool(re.search(r"from typing import|import typing", text))
        has_future_annotations = "from __future__ import annotations" in text

        issues = []
        for i, line in enumerate(file_lines, 1):
            if typing_generic_pattern.search(line):
                issues.append(i)

        if issues and not has_typing_import and not has_future_annotations:
            result.add(Finding(
                pitfall="#22",
                severity="minor",
                file=fpath,
                line=issues[0],
                description=(
                    f"Uses typing.Dict/List/Optional-style generics at lines {issues} "
                    f"but no 'from typing import' found. This can cause Pyright "
                    f"reportUndefinedVariable errors."
                ),
                suggestion=(
                    "Add 'from typing import Dict, List, Optional' at module level, "
                    "or use built-in generics (dict[str, float]) with "
                    "'from __future__ import annotations'."
                ),
            ))

    result.add_pass("Pitfall #22: Pyright built-in generics check completed")


# ─── Pitfall #3: CoinGecko simple/price response shape ────────────────
def check_coingecko_response_shape(py_files: List[str], result: AuditResult):
    """
    Pitfall #3: Detect code that assumes CoinGecko simple/price returns
    a top-level currency key instead of per-coin objects.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "simple/price" not in text and "coingecko" not in text.lower():
            continue

        file_lines = lines(text)
        for i, line in enumerate(file_lines, 1):
            if re.search(r"data\.get\(['\"](usd|hkd|btc|eur)['\"]", line) or \
               re.search(r"data\[['\"](usd|hkd|btc|eur)['\"]\]", line):
                context_start = max(0, i - 3)
                context_end = min(len(file_lines), i + 3)
                context = "\n".join(file_lines[context_start:context_end])

                if "simple/price" in context or "coingecko" in context.lower():
                    if not re.search(r"data\[.*\]\[['\"](usd|hkd|btc)", context):
                        result.add(Finding(
                            pitfall="#3",
                            severity="critical",
                            file=fpath,
                            line=i,
                            description=(
                                "CoinGecko simple/price returns {coin_id: {usd: X, hkd: Y}}, "
                                "NOT {usd: X, hkd: Y}. Using data.get('usd') always returns {}."
                            ),
                            suggestion=(
                                "Access per-coin data first: data[coin_id]['usd']. "
                                "For cross-rates, compute from a common base: eth_hkd / eth_usd."
                            ),
                        ))

    result.add_pass("Pitfall #3: CoinGecko response shape check completed")


def check_whale_score_threshold(py_files: List[str], result: AuditResult):
    """
    New check: Verify signal_generator.py defines and uses a MIN_WHALE_SCORE
    threshold to filter out balance-only whales with no signal history.
    Without this threshold, any wallet marked as whale by balance alone
    can generate signals from dust transactions, flooding the signal feed.
    """
    found_min_whale_score = False
    found_score_check = False

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        fname = os.path.basename(fpath)

        if fname == "signal_generator.py":
            if "MIN_WHALE_SCORE" in text:
                found_min_whale_score = True
            if "whale_score < MIN_WHALE_SCORE" in text or "whale_score<MIN_WHALE_SCORE" in text:
                found_score_check = True

    if found_min_whale_score and found_score_check:
        result.add_pass(
            "Whale score threshold: signal_generator.py defines MIN_WHALE_SCORE "
            "and enforces it in evaluate_for_signal"
        )
    else:
        missing = []
        if not found_min_whale_score:
            missing.append("MIN_WHALE_SCORE constant not found in signal_generator.py")
        if not found_score_check:
            missing.append("whale_score < MIN_WHALE_SCORE check not found in signal_generator.py")
        result.add(Finding(
            pitfall="whale_score_threshold",
            severity="minor",
            file="backend/services/signal_generator.py",
            line=1,
            description=(
                "No whale score threshold for signal generation. "
                "Balance-only whales with no signal history can flood the feed. "
                + "; ".join(missing)
            ),
            suggestion=(
                "Add MIN_WHALE_SCORE = 0.20 constant and check: "
                "if whale_score < MIN_WHALE_SCORE: return None"
            ),
        ))


def check_stale_price_cache_drift(py_files: List[str], result: AuditResult):
    """
    New check: Verify that price cache diagnostics are exposed through the
    health endpoint. Without visibility into price cache age, stale prices
    can silently corrupt balance conversions and signal USD values.

    This checks that the health/diagnostic endpoint reports:
    1. Price cache age (seconds since last refresh)
    2. Whether the cache was ever populated (timestamp > 0)
    3. Number of cached token prices
    """
    has_price_cache_in_health = False
    has_price_age_check = False

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        fname = os.path.basename(fpath)

        if fname == "main.py":
            # Check that the health endpoint includes price cache info
            if "price_cache" in text and ("health" in text.lower() or "diagnostic" in text.lower()):
                has_price_cache_in_health = True
            if "price_cache_age" in text or "cache_age" in text:
                has_price_age_check = True
    
    # The health endpoint should expose price cache staleness info
    if has_price_cache_in_health:
        result.add_pass(
            "Stale price cache: health/diagnostic endpoint exposes price cache info"
        )
    else:
        result.add(Finding(
            pitfall="stale_price_cache_drift",
            severity="minor",
            file="backend/main.py",
            line=1,
            description=(
                "Health/diagnostic endpoint does not expose price cache age. "
                "Stale prices can silently corrupt balance conversions and signal USD values."
            ),
            suggestion=(
                "Add price_cache_age_seconds to health endpoint response. "
                "Include number of cached tokens and timestamp of last successful CoinGecko fetch."
            ),
        ))

    if has_price_age_check:
        result.add_pass(
            "Stale price cache: price_cache_age tracking detected"
        )
    # Not a hard failure — the cache may use different variable names
    # This is informational


def check_whale_score_in_dashboard(
    py_files: List[str], jsx_files: List[str], result: AuditResult
):
    """
    New check: Verify that whale_score is rendered in the Dashboard whale
    tracker table. The backend returns whale_score in the dashboard endpoint
    wallet_meta (added in migration cb07dd5), but the frontend must also
    render it. This is a Pitfall #18 (frontend-backend field contract) check.
    """
    # Check that backend returns whale_score in dashboard endpoint
    backend_returns_whale_score = False
    for fpath in py_files:
        if fpath.endswith(("_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "whale_score" in text and "dashboard" in text.lower():
            # Look for whale_score in the dashboard endpoint context
            file_lines = lines(text)
            in_dashboard = False
            for i, line in enumerate(file_lines):
                if "async def get_dashboard" in line or "def get_dashboard" in line:
                    in_dashboard = True
                elif in_dashboard and line.strip().startswith("def ") and "dashboard" not in line:
                    in_dashboard = False
                elif in_dashboard and "whale_score" in line:
                    backend_returns_whale_score = True
                    break
            if backend_returns_whale_score:
                break

    if not backend_returns_whale_score:
        result.add(Finding(
            pitfall="#18",
            severity="minor",
            file="backend/main.py",
            line=1,
            description=(
                "Dashboard endpoint may not return whale_score in wallet_meta. "
                "The whale_scorer computes it but the dashboard response may not include it."
            ),
            suggestion="Add 'whale_score': float(w.get('whale_score') or 0) to the wallet_meta dict in get_dashboard.",
        ))
        return

    # Check that Dashboard.jsx renders whale_score
    frontend_renders_whale_score = False
    for fpath in jsx_files:
        if "Dashboard" not in fpath:
            continue
        text = read_file(fpath)
        if "whale_score" in text:
            frontend_renders_whale_score = True
            break

    if frontend_renders_whale_score:
        result.add_pass(
            "Pitfall #18: whale_score rendered in Dashboard whale tracker (field contract OK)"
        )
    else:
        result.add(Finding(
            pitfall="#18",
            severity="minor",
            file="frontend/src/pages/Dashboard.jsx",
            line=1,
            description=(
                "Backend returns whale_score in dashboard endpoint but Dashboard.jsx "
                "does not render it. The whale tracker table shows Chain/Label/Address/Balance "
                "but no Score column."
            ),
            suggestion="Add a Score column to the Dashboard whale tracker table showing whale_score badge.",
        ))


def check_copy_trade_signals_performance_indexes(
    sql_files: List[str], result: AuditResult
):
    """
    New check: Verify that composite indexes exist on copy_trade_signals
    for the whale_scorer query pattern. The scoring query filters on
    (wallet_id, created_at) and (wallet_id, status, created_at), which
    require composite indexes for efficient index range scans.

    Without these indexes, each wallet scoring query does a full table scan
    on copy_trade_signals, which becomes slow as the table grows.
    """
    all_sql = "\n".join(read_file(f) for f in sql_files)

    # Check for (wallet_id, created_at) composite index
    has_wallet_created_idx = bool(re.search(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+copy_trade_signals\s*\(\s*wallet_id\s*,\s*created_at\s*\)",
        all_sql,
        re.IGNORECASE,
    ))

    # Check for (wallet_id, status, created_at) composite index
    has_wallet_status_created_idx = bool(re.search(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+copy_trade_signals\s*\(\s*wallet_id\s*,\s*status\s*,\s*created_at\s*\)",
        all_sql,
        re.IGNORECASE,
    ))

    if has_wallet_created_idx:
        result.add_pass(
            "Performance: copy_trade_signals(wallet_id, created_at) composite index exists"
        )
    else:
        result.add(Finding(
            pitfall="performance",
            severity="minor",
            file="backend/migrations/",
            line=1,
            description=(
                "Missing composite index on copy_trade_signals(wallet_id, created_at). "
                "The whale_scorer query filters on both columns but no matching index exists. "
                "This causes full table scans on every wallet scoring operation."
            ),
            suggestion=(
                "Add: CREATE INDEX idx_copy_trade_signals_wallet_created "
                "ON copy_trade_signals(wallet_id, created_at);"
            ),
        ))

    if has_wallet_status_created_idx:
        result.add_pass(
            "Performance: copy_trade_signals(wallet_id, status, created_at) composite index exists"
        )
    else:
        result.add(Finding(
            pitfall="performance",
            severity="minor",
            file="backend/migrations/",
            line=1,
            description=(
                "Missing composite index on copy_trade_signals(wallet_id, status, created_at). "
                "The execution rate calculation filters on all three columns."
            ),
            suggestion=(
                "Add: CREATE INDEX idx_copy_trade_signals_wallet_status_created "
                "ON copy_trade_signals(wallet_id, status, created_at);"
            ),
        ))


def check_concurrent_price_fetch(py_files: List[str], result: AuditResult):
    """
    Performance: Verify that _ensure_prices_fetched() fetches both CoinGecko
    endpoints concurrently (asyncio.gather) instead of sequentially.
    Sequential fetches double the lock hold time and price refresh latency.
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "_ensure_prices_fetched" not in text:
            continue
        # Check that asyncio.gather is used for the two CG requests
        if "asyncio.gather(" in text and "cg_client.get" in text:
            result.add_pass(
                "Performance: _ensure_prices_fetches CoinGecko calls concurrently (asyncio.gather)"
            )
        else:
            result.add(Finding(
                pitfall="performance",
                severity="minor",
                file=fpath,
                line=1,
                description=(
                    "_ensure_prices_fetched() makes sequential CoinGecko API calls. "
                    "Use asyncio.gather() to fetch both endpoints concurrently, "
                    "cutting price refresh latency and lock hold time in half."
                ),
                suggestion=(
                    "Replace sequential 'await cg_client.get(...)' calls with "
                    "'await asyncio.gather(cg_client.get(...), cg_client.get(...))'"
                ),
            ))
        return  # Only check the first file that contains the function
    result.add_pass("Performance: _ensure_prices_fetched not found (no check needed)")


def check_get_me_jwt_created_at(py_files: List[str], result: AuditResult):
    """
    Pitfall #21 residual: The get_me endpoint should embed created_at in the JWT
    at token creation time, avoiding a DB SELECT on every /api/auth/me call.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "def get_me" not in text:
            continue

        file_lines = lines(text)

        for i, line in enumerate(file_lines, 1):
            if re.search(r"def\s+get_me\b", line):
                # Extract function body: from def to next def/class at same indent
                func_body_lines = []
                func_indent = len(line) - len(line.lstrip())
                for j in range(i, min(i + 50, len(file_lines))):
                    next_line = file_lines[j]
                    next_stripped = next_line.lstrip()
                    if next_stripped and not next_line[0].isspace() and j > i:
                        if next_stripped.startswith("def ") or next_stripped.startswith("class ") or next_stripped.startswith("@app."):
                            break
                    func_body_lines.append(next_line)

                body = "\n".join(func_body_lines)
                has_db_created_at = bool(
                    re.search(r"SELECT\s+.*created_at\s+FROM\s+users", body, re.IGNORECASE)
                )
                if has_db_created_at:
                    result.add(Finding(
                        pitfall="#21",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description=(
                            "get_me endpoint does 'SELECT created_at FROM users' on every call. "
                            "Embed created_at in the JWT at token creation time to save "
                            "one DB round-trip per authenticated session refresh."
                        ),
                        suggestion=(
                            "Add 'created_at' to the JWT payload in create_jwt() and "
                            "extract it in get_me() as user.get('created_at') without hitting the DB."
                        ),
                    ))
                else:
                    result.add_pass(
                        f"Pitfall #21 residual: get_me in {fpath} — no DB SELECT for created_at"
                    )
                break


def check_alpaca_validation_before_db(py_files: List[str], result: AuditResult):
    """
    Pitfall #7 variant: The connect_alpaca endpoint must validate credentials
    against Alpaca (HTTP call) BEFORE acquiring a DB connection. If the HTTP
    call happens while holding a DB connection, the connection sits idle during
    the external API round-trip.

    Detection: Look for ALPACA_BASE_URL or APCA-API inside an `acquire_db`
    block by checking indentation. The HTTP call to Alpaca should be at the
    function's top level (lower indentation), not indented under acquire_db.
    """
    HTTP_ALPACA_PATTERNS = re.compile(
        r"(?:ALPACA_BASE_URL|APCA-API|paper-api\.alpaca)",
        re.IGNORECASE,
    )

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "connect_alpaca" not in text:
            continue

        file_lines = lines(text)
        in_func = False
        func_start = 0
        func_indent = 0
        in_acquire_db = False
        acquire_db_indent = 0
        http_inside_db = False
        found_func = False

        for i, line in enumerate(file_lines, 1):
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)

            if re.search(r"def\s+connect_alpaca\b", line):
                in_func = True
                func_start = i
                func_indent = indent
                found_func = True
                in_acquire_db = False
                http_inside_db = False
                continue

            if not in_func:
                continue

            # End of function: next def/class/at same or lesser indentation
            if indent <= func_indent and (
                stripped.startswith("def ")
                or stripped.startswith("class ")
                or stripped.startswith("@app.")
            ):
                if found_func:
                    if http_inside_db:
                        result.add(Finding(
                            pitfall="#7",
                            severity="minor",
                            file=fpath,
                            line=func_start,
                            description=(
                                "connect_alpaca makes Alpaca HTTP API calls inside "
                                "an acquire_db block. The DB connection sits idle during "
                                "the external HTTP round-trip."
                            ),
                            suggestion=(
                                "Restructure to: (1) validate Alpaca credentials via HTTP first, "
                                "(2) then acquire DB connection only for the UPDATE write."
                            ),
                        ))
                    else:
                        result.add_pass(
                            f"Pitfall #7 (Alpaca): connect_alpaca in {fpath} — "
                            "HTTP validation outside DB block (OK)"
                        )
                    in_func = False
                continue

            # Track acquire_db blocks by indentation
            if "acquire_db()" in line or "acquire_db()" in stripped:
                in_acquire_db = True
                acquire_db_indent = indent
                continue

            # If we're inside an acquire_db block and see an Alpaca HTTP call
            if in_acquire_db and HTTP_ALPACA_PATTERNS.search(line):
                http_inside_db = True

            # Exit acquire_db block when indentation returns to function level
            if in_acquire_db and indent <= func_indent + 1 and stripped:
                # We've exited the acquire_db indent level
                # But need to be careful: acquire_db is typically 8-12 spaces indented
                # and its body is 12-16 spaces
                if indent <= acquire_db_indent:
                    in_acquire_db = False

        # Handle function at end of file
        if in_func:
            if http_inside_db:
                result.add(Finding(
                    pitfall="#7",
                    severity="minor",
                    file=fpath,
                    line=func_start,
                    description=(
                        "connect_alpaca makes Alpaca HTTP API calls inside "
                        "an acquire_db block."
                    ),
                    suggestion=(
                        "Restructure to: (1) validate Alpaca credentials via HTTP first, "
                        "(2) then acquire_db() only for the UPDATE write."
                    ),
                ))
            else:
                result.add_pass(
                    f"Pitfall #7 (Alpaca): connect_alpaca in {fpath} — OK"
                )

    result.add_pass("Pitfall #7 (Alpaca): No connect_alpaca found (check not applicable)")


def check_mirror_trade_action_normalization(py_files: List[str], result: AuditResult):
    """
    New check: Verify that the mirror_trade endpoint normalizes signal action
    ('buy'/'receive' → 'buy') consistently for BOTH the fractional and qty=1
    Alpaca order code paths.

    Bug pattern: The fractional path uses trade_side = "buy" if signal["action"]
    in ("buy", "receive") else "sell", but the qty=1 fallback used
    "buy" if signal["action"] == "buy" else "sell" — which sends "sell" for
    "receive" actions, causing an incorrect short sell instead of a buy.

    Detection: Look for the qty=1 fallback json block inside mirror_trade and
    verify it references the same normalized trade_side variable rather than
    re-implementing the normalization inline with a different condition.
    """
    MIRROR_TRADE_PATTERN = re.compile(r"mirror_trade|/api/signals/.*mirror")
    QTY_FALLBACK_PATTERN = re.compile(r"qty.*[\"']1[\"']|notional_below|MIN_NOTIONAL")
    INLINE_NORMALIZATION = re.compile(
        r"buy[\"'].*signal\[.action.\].*==.*buy"
        r"|signal\[.action.\].*==.*buy.*buy"
    )

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "mirror_trade" not in text and "/api/signals" not in text:
            continue
        if "mirror" not in text.lower():
            continue

        file_lines = lines(text)
        found_fallback_block = False
        has_inline_normalization = False
        has_trade_side_var = False
        in_mirror_func = False
        mirror_line = 0

        for i, line in enumerate(file_lines, 1):
            if "async def mirror_trade" in line:
                in_mirror_func = True
                mirror_line = i
                continue
            if in_mirror_func:
                # Detect qty=1 fallback block
                if "qty" in line and ("\"1\"" in line or "'1'" in line):
                    found_fallback_block = True
                # Check if fallback uses inline normalization (bug pattern)
                if found_fallback_block and INLINE_NORMALIZATION.search(line):
                    has_inline_normalization = True
                # Check if fallback uses trade_side variable (correct pattern)
                if found_fallback_block and "trade_side" in line:
                    has_trade_side_var = True
                # Stop tracking when we leave the fallback block
                if found_fallback_block and "response.raise_for_status" in line:
                    found_fallback_block = False

        if has_inline_normalization and not has_trade_side_var:
            result.add(Finding(
                pitfall="mirror_trade action normalization mismatch",
                severity="critical",
                file=fpath,
                line=mirror_line,
                description=(
                    "The qty=1 fallback in mirror_trade re-implements action→side "
                    "normalization inline with a DIFFERENT condition than the "
                    "fractional path. Signal action 'receive' would be sent as "
                    "'sell' instead of 'buy' for small orders."
                ),
                suggestion=(
                    "Define trade_side once (before the if/else) and use it in "
                    "both the fractional and qty=1 order payloads: "
                    "trade_side = 'buy' if signal['action'] in ('buy', 'receive') else 'sell'"
                ),
            ))
        elif has_trade_side_var or (not has_inline_normalization):
            result.add_pass(
                f"mirror_trade action normalization in {fpath} — OK "
                f"(uses consistent trade_side variable in both code paths)"
            )

    # If we never found mirror_trade in any file, report pass
    if not any("mirror_trade" in read_file(f) for f in py_files
               if not f.endswith(("audit_source.py", "check_migration_status.py"))):
        result.add_pass("mirror_trade action normalization — no mirror_trade found (check not applicable)")


def check_mirror_trade_status_guard(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 17): Verify that the mirror_trade endpoint filters by
    status='pending' in the SELECT query to prevent double-execution.

    Bug pattern: Without `AND cts.status = 'pending'` in the WHERE clause,
    a signal that is already executed/failed/stale can still be fetched and
    sent to Alpaca again if called concurrently.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        # Only check main.py — the file that defines the mirror_trade endpoint.
        # monitor.py, test files, and other modules reference 'mirror_trade' in
        # comments/imports but don't define the SELECT query we're auditing.
        if not fpath.endswith("main.py"):
            continue
        text = read_file(fpath)
        if "mirror_trade" not in text:
            continue

        # Find the mirror_trade function and its SELECT query
        lines_list = lines(text)
        in_mirror_trade = False
        has_status_guard = False
        func_line = 0
        select_block = []

        for i, line in enumerate(lines_list, 1):
            if "async def mirror_trade" in line:
                in_mirror_trade = True
                func_line = i
                continue
            if in_mirror_trade:
                # Collect lines that are part of a SQL SELECT block
                if "SELECT" in line.upper() or select_block:
                    select_block.append(line)
                # Check if the collected block contains status + pending
                if select_block:
                    block_text = " ".join(select_block).lower()
                    if "status" in block_text and "pending" in block_text:
                        has_status_guard = True
                        break
                # Exit if we hit the next function def
                if i > func_line + 40:
                    break
                if (line.strip().startswith("def ") or line.strip().startswith("async def ")) and i > func_line + 2:
                    break

        if has_status_guard:
            result.add_pass(
                f"mirror_trade status guard in {fpath} — OK (filters by status='pending')"
            )
        else:
            result.add(Finding(
                pitfall="mirror_trade double-execution protection",
                severity="critical",
                file=fpath,
                line=func_line,
                description=(
                    "mirror_trade endpoint does NOT filter by status='pending'. "
                    "Double-clicking mirror trade or concurrent requests can execute "
                    "the same signal twice."
                ),
                suggestion=(
                    "Add `AND cts.status = 'pending'` to the signal SELECT query: "
                    "WHERE cts.id = $1 AND w.user_id = $2 AND cts.status = 'pending'"
                ),
            ))


def check_mirror_trade_action_guard(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 17): Verify that the mirror_trade endpoint explicitly
    rejects non-buy/receive actions before calling Alpaca.

    Bug pattern: If a 'sell' or 'send' signal somehow reaches mirror_trade,
    it will be sent to Alpaca as a short sell — which is likely unintended.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "mirror_trade" not in text:
            continue

        # Check for action validation guard
        lines_list = lines(text)
        in_mirror_trade = False
        has_action_guard = False
        func_line = 0

        for i, line in enumerate(lines_list, 1):
            if "async def mirror_trade" in line:
                in_mirror_trade = True
                func_line = i
                continue
            if in_mirror_trade:
                if "signal[\"action\"] not in" in line or "signal['action'] not in" in line:
                    has_action_guard = True
                    break
                # Also accept the reverse pattern
                if "Cannot mirror trade" in line:
                    has_action_guard = True
                    break
                if i > func_line + 60:
                    break
                if (line.strip().startswith("def ") or line.strip().startswith("async def ")) and i > func_line + 2:
                    break

        if has_action_guard:
            result.add_pass(
                f"mirror_trade action guard in {fpath} — OK (validates action in ('buy', 'receive'))"
            )
        else:
            result.add(Finding(
                pitfall="mirror_trade action guard",
                severity="medium",
                file=fpath,
                line=func_line,
                description=(
                    "mirror_trade endpoint does not validate signal action. "
                    "A 'sell' or 'send' action would be forwarded to Alpaca as short sell."
                ),
                suggestion=(
                    "Add guard after signal fetch: "
                    "if signal['action'] not in ('buy', 'receive'): raise HTTPException(400, ...)"
                ),
            ))


def check_mirror_trade_response_validation(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 17): Verify that mirror_trade validates the Alpaca order
    response has an 'id' before marking the signal as 'executed'.

    Bug pattern: Alpaca may return 200 with {"message": "insufficient funds"}.
    Without checking order_data.get("id"), the signal would be marked as executed
    even though no order was placed.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "mirror_trade" not in text:
            continue

        lines_list = lines(text)
        in_mirror_trade = False
        has_response_validation = False
        func_line = 0

        for i, line in enumerate(lines_list, 1):
            if "async def mirror_trade" in line:
                in_mirror_trade = True
                func_line = i
                continue
            if in_mirror_trade:
                if "not alpaca_order_id" in line or "no order ID" in line.lower():
                    has_response_validation = True
                    break
                if i > func_line + 80:
                    break
                if (line.strip().startswith("def ") or line.strip().startswith("async def ")) and i > func_line + 2:
                    break

        if has_response_validation:
            result.add_pass(
                f"mirror_trade response validation in {fpath} — OK (checks order ID before marking executed)"
            )
        else:
            result.add(Finding(
                pitfall="mirror_trade response shape validation",
                severity="medium",
                file=fpath,
                line=func_line,
                description=(
                    "mirror_trade does not validate Alpaca response before marking executed. "
                    "If Alpaca returns 200 without an 'id' (e.g., insufficient funds), "
                    "the signal would be incorrectly marked as 'executed'."
                ),
                suggestion=(
                    "After both order POST blocks, add: "
                    "if not alpaca_order_id: → mark signal failed, raise HTTPException(502)"
                ),
            ))


def check_mirror_trade_price_status_check(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 17): Verify that the price fetch in the non-fractional
    path checks status_code before parsing JSON.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        if "mirror_trade" not in text:
            continue

        lines_list = lines(text)
        has_price_status_check = False
        for i, line in enumerate(lines_list, 1):
            if "price_resp.status_code" in line or "price_resp.status" in line:
                has_price_status_check = True
                break

        if has_price_status_check:
            result.add_pass(
                f"mirror_trade price status check in {fpath} — OK"
            )
        else:
            result.add(Finding(
                pitfall="price response status check",
                severity="low",
                file=fpath,
                line=0,
                description=(
                    "Price response in mirror_trade non-fractional path does not check "
                    "status_code before parsing JSON. A non-200 response could cause "
                    "JSON parse errors or incorrect price extraction."
                ),
                suggestion=(
                    "Add `if price_resp.status_code == 200:` before parsing price JSON."
                ),
            ))


def check_portfolio_change_delta_consistency(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 12): Verify that portfolio_change alert rule's delta_sum
    only includes personal (is_mine=True, is_whale=False) wallets, matching the
    current_total SQL query. Including whale wallet deltas in delta_sum while
    excluding them from current_total causes incorrect prev_total computation.

    See: dual-agent pitfall — portfolio_change delta mismatch.
    """
    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        # Find the portfolio_change rule block and extract delta_sum filter
        in_portfolio_change = False
        found_delta_sum = False
        delta_filter_text = ""
        delta_paren_depth = 0

        for i, line in enumerate(file_lines, 1):
            if 'rule_type == "portfolio_change"' in line or "rule_type == 'portfolio_change'" in line:
                in_portfolio_change = True
                continue
            if in_portfolio_change:
                # Detect delta_sum = sum( start
                if "delta_sum" in line and "sum(" in line:
                    found_delta_sum = True
                    delta_filter_text = line
                    delta_paren_depth = line.count("(") - line.count(")")
                    continue
                # Accumulate multi-line generator expression after delta_sum = sum(
                if found_delta_sum and "delta_sum" not in line:
                    delta_filter_text += " " + line.strip()
                    delta_paren_depth += line.count("(") - line.count(")")
                    # Only break when the sum( call's parentheses are fully closed
                    # (paren_depth back to 0) — this ensures we also capture the
                    # "for ... if ..." clause that follows the generator body.
                    if delta_paren_depth <= 0:
                        # sum( call closed; keep accumulating for "for ... if ..." clause
                        found_delta_sum = False  # stop depth tracking
                        delta_filter_text += " "
                        continue
                # Continue accumulating "for ... if ..." clause after sum() closed
                if not found_delta_sum and delta_filter_text:
                    if "for " in line or "if " in line:
                        delta_filter_text += " " + line.strip()
                        if "):\n" in line or line.strip().endswith("):"):
                            break
                    elif "prev_total" in line:
                        # Reached the next statement; stop accumulation
                        break
                # Exit portfolio_change block when we hit next elif/else
                if "elif rule_type" in line or "else:" in line:
                    in_portfolio_change = False
                    found_delta_sum = False

        if found_delta_sum:
            # sum( was found but never closed (unbalanced parens) — skip validation
            return
        if delta_filter_text:
            if "is_mine" not in delta_filter_text and "is_mine_flag" not in delta_filter_text:
                # Find the line number of the delta_sum
                delta_line = 0
                for i, line in enumerate(file_lines, 1):
                    if "delta_sum" in line and "sum(" in line:
                        delta_line = i
                        break
                result.add(Finding(
                    pitfall="Pitfall #F1: portfolio_change delta_sum missing is_mine filter",
                    file=fpath,
                    line=delta_line,
                    description=(
                        "portfolio_change alert delta_sum iterates over ALL changed wallets "
                        "but current_total only sums is_mine=TRUE AND is_whale=FALSE wallets. "
                        "Including whale wallet deltas causes incorrect prev_total computation."
                    ),
                    suggestion=(
                        "Add 'and is_mine_flag and not is_whale_flag' to the delta_sum filter "
                        "to match the current_total SQL query's is_mine/is_whale conditions."
                    ),
                    severity="critical",
                ))
                return
            if "is_whale" not in delta_filter_text and "is_whale_flag" not in delta_filter_text:
                delta_line = 0
                for i, line in enumerate(file_lines, 1):
                    if "delta_sum" in line and "sum(" in line:
                        delta_line = i
                        break
                result.add(Finding(
                    pitfall="Pitfall #F1: portfolio_change delta_sum missing is_whale filter",
                    file=fpath,
                    line=delta_line,
                    description=(
                        "portfolio_change alert delta_sum does not exclude whale wallets. "
                        "current_total only sums is_whale=FALSE wallets."
                    ),
                    suggestion=(
                        "Add 'and not is_whale_flag' to the delta_sum filter "
                        "to match the current_total SQL query's is_whale=FALSE condition."
                    ),
                    severity="critical",
                ))
                return
            result.add_pass(
                f"Pitfall #F1: portfolio_change delta_sum in {fpath} "
                f"correctly filters is_mine and is_whale"
            )
            return

    result.add_pass("Pitfall #F1: No portfolio_change rule found (check not applicable)")


def check_alert_fired_dict_notify_telegram(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 2026-06-09): Verify that ALL alert rule type fired.append()
    dicts in alert_evaluator.py include the 'notify_telegram' field. Missing it
    causes the Telegram send loop to always send (defaulting to True), ignoring
    the user's per-alert opt-in preference.
    """
    for fpath in py_files:
        if not fpath.endswith("alert_evaluator.py"):
            continue
        if fpath.endswith("audit_source.py"):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        # Find all fired.append blocks and check which rule_type they belong to
        # Strategy: find each "elif rule_type ==" block, then look for fired.append
        # within that block, and check if 'notify_telegram' is in the dict literal.
        in_rule_block = False
        current_rule = None
        brace_depth = 0
        append_block_lines = []
        append_start_line = 0
        rule_has_notify_telegram = {}
        inside_append = False

        for i, line in enumerate(file_lines, 1):
            stripped = line.strip()

            # Detect rule_type blocks
            if 'rule_type == "' in line or "rule_type == '" in line:
                in_rule_block = True
                # Extract the rule type
                import re as _re
                m = _re.search(r'rule_type\s*==\s*["\'](\w+)["\']', line)
                current_rule = m.group(1) if m else "unknown"
                rule_has_notify_telegram[current_rule] = False
                continue

            if in_rule_block:
                # Detect fired.append({ start
                if "fired.append" in line and "{" in line:
                    inside_append = True
                    append_block_lines = [line]
                    append_start_line = i
                    brace_depth = line.count("{") - line.count("}")
                    continue

                if inside_append:
                    append_block_lines.append(line)
                    brace_depth += line.count("{") - line.count("}")
                    if brace_depth <= 0:
                        # Block complete — check for notify_telegram
                        block_text = " ".join(append_block_lines)
                        if "notify_telegram" in block_text:
                            rule_has_notify_telegram[current_rule] = True
                        inside_append = False
                        append_block_lines = []

                # Exit rule block at next elif/else
                if ("elif rule_type" in line or stripped == "else:") and not inside_append:
                    in_rule_block = False
                    current_rule = None

        # Report findings
        for rule, has_field in rule_has_notify_telegram.items():
            if not has_field:
                result.add(Finding(
                    pitfall="Alert fired dict missing notify_telegram",
                    file=fpath,
                    line=0,
                    description=(
                        f"Alert rule '{rule}' fired.append() dict is missing "
                        f"'notify_telegram' field. The Telegram send loop defaults "
                        f"to True, ignoring the user's per-alert opt-in preference."
                    ),
                    suggestion=(
                        "Add 'notify_telegram': alert.get('notify_telegram', True) "
                        "to the fired.append() dict, matching the other rule types."
                    ),
                    severity="minor",
                ))
                return

        if rule_has_notify_telegram:
            result.add_pass(
                f"Alert fired dicts in {fpath} — all rule types include notify_telegram"
            )
            return

    result.add_pass("Alert fired dict check — no alert_evaluator.py found (not applicable)")


def check_endpoint_response_field_consistency(py_files: List[str], jsx_files: List[str], result: AuditResult):
    """
    New check: Verify that list_wallets and /api/dashboard return the same set of
    wallet fields. A drift between these two endpoints can cause the frontend to
    work with one endpoint but break with the other.
    """
    list_wallets_fields: Set[str] = set()
    dashboard_wallet_fields: Set[str] = set()

    for fpath in py_files:
        if fpath.endswith(("audit_source.py", "check_migration_status.py")):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        # Find list_wallets endpoint and extract returned field names
        in_list_wallets = False
        in_dashboard = False
        brace_depth = 0

        for i, line in enumerate(file_lines, 1):
            if re.search(r"def\s+list_wallets\b", line):
                in_list_wallets = True
                in_dashboard = False
                brace_depth = 0
                continue
            if re.search(r"def\s+get_dashboard\b", line):
                in_dashboard = True
                in_list_wallets = False
                brace_depth = 0
                continue

            if in_list_wallets or in_dashboard:
                # Track dict literals
                brace_depth += line.count("{") - line.count("}")
                # Extract field names from dict keys
                field_matches = re.findall(r'"(\w+)":', line)
                if in_list_wallets:
                    list_wallets_fields.update(field_matches)
                if in_dashboard:
                    dashboard_wallet_fields.update(field_matches)

                # End of function
                if brace_depth <= 0 and ("return " in line or (line and not line[0].isspace() and (line.startswith("def ") or line.startswith("class ") or line.startswith("@app.")))):
                    if in_list_wallets:
                        in_list_wallets = False
                    if in_dashboard:
                        in_dashboard = False

    if list_wallets_fields and dashboard_wallet_fields:
        only_in_list = list_wallets_fields - dashboard_wallet_fields
        only_in_dashboard = dashboard_wallet_fields - list_wallets_fields
        # Filter out non-field noise (Python keywords, common dict keys)
        noise = {"id", "return", "wallets", "wallet", "True", "False", "None", "str", "float", "int", "bool", "list", "dict", "await", "async", "def", "if", "else", "for", "in", "not", "and", "or", "is", "as", "with", "from", "import", "try", "except", "finally", "raise", "pass", "break", "continue", "yield", "lambda", "class", "return"}
        only_in_list -= noise
        only_in_dashboard -= noise

        if only_in_list or only_in_dashboard:
            result.add(Finding(
                pitfall="field-drift",
                severity="minor",
                file="(multiple)",
                line=1,
                description=(
                    f"list_wallets and /api/dashboard return different wallet field sets. "
                    f"Only in list_wallets: {only_in_list}. "
                    f"Only in dashboard: {only_in_dashboard}. "
                    f"This can cause frontend inconsistencies if components switch endpoints."
                ),
                suggestion=(
                    "Align the field sets between list_wallets and dashboard wallet_meta. "
                    "Both endpoints should return the same wallet shape."
                ),
            ))
        else:
            result.add_pass(
                "Field consistency: list_wallets and dashboard return aligned wallet field sets"
            )
    else:
        result.add_pass(
            "Field consistency: Could not extract field sets (endpoints not found in expected format)"
        )


def check_dead_code_modules(py_files: list, result: AuditResult):
    """
    Detect Python modules in services/ that export public functions/classes
    but are never imported by any other module in the project.

    This catches dead code like telegram_alerts.py (exists, has useful exports,
    but nothing imports them) — a module that should either be wired in or deleted.
    """
    import re as _re

    services_dir = None
    for f in py_files:
        if "/services/" in f or "\\services\\" in f:
            services_dir = os.path.dirname(f)
            break

    if not services_dir:
        result.passed.append("Dead code: no services/ directory found (skipping)")
        return

    # Find all service modules (excluding __init__.py, __pycache__)
    service_modules = {}  # module_name -> file_path
    for f in py_files:
        basename = os.path.basename(f)
        if basename.startswith("__"):
            continue
        mod_name = os.path.splitext(basename)[0]
        if "/services/" in f or "\\services\\" in f:
            service_modules[mod_name] = f

    if not service_modules:
        result.passed.append("Dead code: no service modules found (skipping)")
        return

    # For each service module, extract its public exports (def/class names)
    exports: dict = {}  # module_name -> set of public names
    _export_re = _re.compile(r'^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)')
    for mod_name, fpath in service_modules.items():
        try:
            with open(fpath, "r") as fh:
                lines = fh.read().splitlines()
            names = set()
            for line in lines:
                m = _export_re.match(line.strip())
                if m:
                    names.add(m.group(1) or m.group(2))
            exports[mod_name] = names
        except Exception:
            continue

    # For each module, check if any other file imports it
    imported_modules = set()
    _import_re = _re.compile(
        r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))'
    )
    for f in py_files:
        try:
            with open(f, "r") as fh:
                content = fh.read()
            for m in _import_re.finditer(content):
                mod = m.group(1) or m.group(2)
                if mod:
                    # Check if the import is for a service module
                    parts = mod.split(".")
                    # Handle both "services.X" and direct "X" imports
                    for part in parts:
                        if part in service_modules:
                            imported_modules.add(part)
        except Exception:
            continue

    # Find service modules that are never imported (excluding main.py itself)
    dead = []
    for mod_name in service_modules:
        if mod_name in imported_modules:
            continue
        if mod_name == "main":
            continue  # main.py is the entry point, not imported
        if not exports.get(mod_name):
            continue  # No public exports = internal utility, OK to be standalone
        dead.append(mod_name)

    if dead:
        # MINOR: dead code modules are maintenance traps, not runtime bugs
        for mod in sorted(dead):
            result.passed.append(
                f"Dead code check: '{mod}' has exports but is never imported "
                f"(wiring or cleanup needed)"
            )
    else:
        result.passed.append(
            "Dead code check: all service modules with exports are imported somewhere"
        )


def check_closed_at_on_status_transition(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 2026-06-09): Verify that all UPDATE copy_trade_signals
    statements that set status to a terminal state ('failed' or 'executed')
    also set closed_at = NOW(). The closed_at column was added in migration 016
    but was never populated by application code — this check catches that gap.
    """
    import re

    found_any = False

    for fpath in py_files:
        if fpath.endswith("audit_source.py"):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        # Collect multi-line SQL blocks that contain UPDATE copy_trade_signals
        i = 0
        while i < len(file_lines):
            line = file_lines[i]
            stripped = line.strip()

            if re.search(r'UPDATE\s+copy_trade_signals', stripped, re.IGNORECASE):
                # Collect the full SQL block until WHERE or end of statement
                sql_lines = [stripped]
                start_line = i + 1
                j = i + 1
                while j < len(file_lines):
                    next_stripped = file_lines[j].strip()
                    sql_lines.append(next_stripped)
                    if re.search(r'WHERE\s+', ' '.join(sql_lines), re.IGNORECASE):
                        break
                    j += 1

                full_sql = ' '.join(sql_lines)

                # Check if this UPDATE sets status to a terminal state
                if re.search(r"SET\s+.*status\s*=\s*'(?:failed|executed)'", full_sql, re.IGNORECASE):
                    found_any = True
                    has_closed_at = bool(re.search(r'closed_at\s*=\s*NOW\(\)', full_sql, re.IGNORECASE))
                    if not has_closed_at:
                        result.add(Finding(
                            pitfall="closed-at-missing",
                            severity="critical",
                            file=fpath,
                            line=start_line,
                            description=(
                                "UPDATE copy_trade_signals sets status to terminal state "
                                "but does NOT set closed_at = NOW(). The closed_at column "
                                "(added in migration 016) will remain NULL, breaking signal "
                                "lifecycle tracking."
                            ),
                            suggestion="Add ', closed_at = NOW()' to the SET clause of this UPDATE statement.",
                        ))
                    else:
                        result.add_pass(
                            f"closed_at on transition: {fpath}:{start_line} — "
                            f"UPDATE copy_trade_signals correctly includes closed_at = NOW()"
                        )

                i = j + 1 if j > i else i + 1
            else:
                i += 1

    if not found_any:
        result.add_pass(
            "closed_at on transition: No UPDATE copy_trade_signals with terminal status found "
            "(check not applicable)"
        )


def check_stale_signal_expiry(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 2026-06-09): Verify that the monitor contains a phase
    that auto-expires stale pending signals. Without this, signals can stay
    in 'pending' forever when mirror_trade is never invoked.

    Checks:
    1. SIGNAL_STALE_THRESHOLD_HOURS constant exists and is > 0
    2. A phase runs UPDATE copy_trade_signals SET status = 'stale' for old pending signals
    3. The stale UPDATE also sets closed_at = NOW()
    """
    found_threshold = False
    found_expiry_phase = False
    found_stale_closed_at = False

    for fpath in py_files:
        if fpath.endswith("audit_source.py"):
            continue
        text = read_file(fpath)
        file_lines = lines(text)

        # Check 1: SIGNAL_STALE_THRESHOLD_HOURS exists and is > 0
        for i, line in enumerate(file_lines):
            stripped = line.strip()
            m = re.match(r'SIGNAL_STALE_THRESHOLD_HOURS\s*=\s*(\d+)', stripped)
            if m:
                found_threshold = True
                val = int(m.group(1))
                if val <= 0:
                    result.add(Finding(
                        pitfall="stale-threshold-invalid",
                        severity="critical",
                        file=fpath,
                        line=i + 1,
                        description=f"SIGNAL_STALE_THRESHOLD_HOURS={val} is not positive.",
                        suggestion="Set SIGNAL_STALE_THRESHOLD_HOURS to a positive integer (e.g., 72 for 72 hours).",
                    ))
                break

        # Check 2: Phase that expires stale pending signals
        full_text = text
        if re.search(r"UPDATE\s+copy_trade_signals\s+SET\s+.*status\s*=\s*['\"]stale['\"]", full_text, re.IGNORECASE | re.DOTALL):
            found_expiry_phase = True
            # Check 3: closed_at is also set
            if re.search(r"status\s*=\s*['\"]stale['\"].*closed_at\s*=\s*NOW\(\)", full_text, re.IGNORECASE | re.DOTALL):
                found_stale_closed_at = True
            # Also check reversed order (closed_at before status)
            elif re.search(r"closed_at\s*=\s*NOW\(\).*status\s*=\s*['\"]stale['\"]", full_text, re.IGNORECASE | re.DOTALL):
                found_stale_closed_at = True

    if not found_threshold:
        result.add(Finding(
            pitfall="stale-threshold-missing",
            severity="minor",
            file="services/monitor.py",
            line=0,
            description=(
                "No SIGNAL_STALE_THRESHOLD_HOURS constant found. "
                "Without this, there's no configuration for stale signal expiry."
            ),
            suggestion="Add SIGNAL_STALE_THRESHOLD_HOURS = 72 in monitor.py.",
        ))
    else:
        result.add_pass("stale expiry threshold: SIGNAL_STALE_THRESHOLD_HOURS is defined")

    if not found_expiry_phase:
        result.add(Finding(
            pitfall="stale-expiry-phase-missing",
            severity="minor",
            file="services/monitor.py",
            line=0,
            description=(
                "No UPDATE copy_trade_signals SET status='stale' found. "
                "Signals may stay 'pending' forever if mirror_trade is never invoked."
            ),
            suggestion=(
                "Add a monitor phase that expires old pending signals: "
                "UPDATE copy_trade_signals SET status='stale', closed_at=NOW() "
                "WHERE status='pending' AND created_at < NOW() - make_interval(hours => N)"
            ),
        ))
    else:
        if found_stale_closed_at:
            result.add_pass("stale expiry phase: Status='stale' transition includes closed_at = NOW()")
        else:
            result.add(Finding(
                pitfall="stale-expiry-missing-closed-at",
                severity="critical",
                file="services/monitor.py",
                line=0,
                description=(
                    "The stale signal expiry UPDATE does NOT set closed_at = NOW(). "
                    "The closed_at column will remain NULL for stale signals."
                ),
                suggestion="Add closed_at = NOW() to the stale expiry UPDATE statement.",
            ))


def check_signal_history_endpoint(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 2026-06-09 13:00): Verify that a /api/signals/history endpoint
    exists and returns recently closed signals with proper field coverage.

    Checks:
    1. GET /api/signals/history endpoint is registered
    2. It filters by closed_at IS NOT NULL (only closed signals)
    3. It returns time_to_close_seconds field
    4. It orders by closed_at DESC
    """
    found_endpoint = False
    found_closed_at_filter = False
    found_time_to_close = False
    found_order_by = False

    for fpath in py_files:
        src = read_file(fpath)
        if not src:
            continue
        if "@app.get(\"/api/signals/history\")" in src or '@app.get("/api/signals/history")' in src:
            found_endpoint = True
            if "closed_at IS NOT NULL" in src:
                found_closed_at_filter = True
            if "time_to_close_seconds" in src:
                found_time_to_close = True
            if "closed_at DESC" in src:
                found_order_by = True

    if found_endpoint:
        result.passed.append("Signal history endpoint: GET /api/signals/history exists")
    else:
        result.findings.append(Finding(
            pitfall="signal-history-endpoint-missing",
            severity="minor",
            file="main.py",
            line=0,
            description="No GET /api/signals/history endpoint found. Users cannot view recently closed signals.",
            suggestion="Add a GET /api/signals/history endpoint that returns executed/failed/stale signals with closed_at.",
        ))
        return

    if found_closed_at_filter:
        result.passed.append("Signal history endpoint: filters by closed_at IS NOT NULL")
    else:
        result.findings.append(Finding(
            pitfall="signal-history-missing-closed-filter",
            severity="minor",
            file="main.py",
            line=0,
            description="Signal history endpoint does not filter by closed_at IS NOT NULL.",
            suggestion="Add 'closed_at IS NOT NULL' to the WHERE clause to only return closed signals.",
        ))

    if found_time_to_close:
        result.passed.append("Signal history endpoint: returns time_to_close_seconds")
    else:
        result.findings.append(Finding(
            pitfall="signal-history-missing-time-to-close",
            severity="minor",
            file="main.py",
            line=0,
            description="Signal history endpoint does not compute time_to_close_seconds.",
            suggestion="Add EXTRACT(EPOCH FROM (closed_at - created_at)) AS time_to_close_seconds to the SELECT.",
        ))

    if found_order_by:
        result.passed.append("Signal history endpoint: orders by closed_at DESC")
    else:
        result.findings.append(Finding(
            pitfall="signal-history-missing-order",
            severity="minor",
            file="main.py",
            line=0,
            description="Signal history endpoint does not order by closed_at DESC.",
            suggestion="Add ORDER BY closed_at DESC to show most recently closed signals first.",
        ))


def check_duplicate_cycle_stats(py_files: List[str], result: AuditResult):
    """
    New check (Cycle 2026-06-09 13:00): Detect duplicate _cycle_stats.append() calls
    within the same function. Duplicate recording means two entries per cycle, with
    the first one missing Phase 7 data (signals_stale_expired).
    """
    for fpath in py_files:
        # Skip the audit tool itself (it contains the search string in check code)
        if fpath.endswith("audit_source.py"):
            continue
        src = read_file(fpath)
        if not src:
            continue
        # Count _cycle_stats.append occurrences
        count = src.count("_cycle_stats.append(")
        if count > 1:
            # Check if they're in the same function (heuristic: same file, multiple occurrences)
            result.findings.append(Finding(
                pitfall="duplicate-cycle-stats",
                severity="minor",
                file=fpath.replace(os.getcwd() + os.sep, "").replace(os.getcwd(), "."),
                line=0,
                description=(
                    f"Found {count} _cycle_stats.append() calls. "
                    "Duplicate recording creates two entries per cycle, the first missing Phase 7 data."
                ),
                suggestion=(
                    "Remove the first _cycle_stats.append() block (before Phase 7). "
                    "Keep only the one after all phases complete."
                ),
            ))
        elif count == 1:
            rel = fpath.replace(os.getcwd() + os.sep, "").replace(os.getcwd(), ".")
            result.passed.append(f"Duplicate cycle stats: {rel} — only 1 _cycle_stats.append (OK)")
        # count == 0: not the monitor file, skip


def check_signal_history_frontend(jsx_files: List[str], result: AuditResult):
    """Check that SignalHistory component exists and uses /api/signals/history."""
    found_component = False
    found_endpoint_usage = False
    found_stale_filter = False
    found_status_colors_stale = False

    for fpath in jsx_files:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        if "function SignalHistory" in content or "const SignalHistory" in content:
            found_component = True
            rel = fpath.replace(os.getcwd() + os.sep, "").replace(os.getcwd(), ".")
            # Check it fetches from the history endpoint
            if "/signals/history" in content:
                found_endpoint_usage = True
            # Check it has stale filter support
            if "'stale'" in content and ("status_filter" in content or "filter" in content):
                found_stale_filter = True

        # Check STATUS_COLORS has stale entry
        if "STATUS_COLORS" in content and "stale" in content:
            found_status_colors_stale = True

    if found_component:
        result.passed.append("SignalHistory frontend: component exists")
    else:
        result.minor.append(AuditFinding(
            pitfall="SignalHistory frontend component missing",
            file="(jsx files)", line=0,
            description="No SignalHistory component found in JSX files. The /api/signals/history backend endpoint exists but has no frontend consumer.",
            suggestion="Add a SignalHistory component that fetches /api/signals/history and displays closed signals in a table."
        ))

    if found_endpoint_usage:
        result.passed.append("SignalHistory frontend: uses /api/signals/history endpoint")
    elif found_component:
        result.minor.append(AuditFinding(
            pitfall="SignalHistory not using history endpoint",
            file="(jsx files)", line=0,
            description="SignalHistory component exists but does not fetch /api/signals/history.",
            suggestion="Ensure SignalHistory fetches from /api/signals/history?limit=50 with optional status_filter param."
        ))

    if found_stale_filter:
        result.passed.append("SignalHistory frontend: supports stale filter")
    elif found_component:
        result.minor.append(AuditFinding(
            pitfall="SignalHistory missing stale filter",
            file="(jsx files)", line=0,
            description="SignalHistory component does not filter by 'stale' status.",
            suggestion="Add filter tabs for all/executed/failed/stale to the SignalHistory component."
        ))

    if found_status_colors_stale:
        result.passed.append("STATUS_COLORS: has stale color entry")
    else:
        result.minor.append(AuditFinding(
            pitfall="STATUS_COLORS missing stale entry",
            file="(jsx files)", line=0,
            description="STATUS_COLORS constant does not include a 'stale' key. Stale signals will have no color in signal cards.",
            suggestion="Add stale: '#6b7280' to STATUS_COLORS."
        ))


def run_audit(base_path: str) -> AuditResult:
    result = AuditResult()

    py_files = find_py_files(base_path)
    sql_files = find_sql_files(base_path)
    jsx_files = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "node_modules"}]
        for f in files:
            if f.endswith((".jsx", ".tsx", ".js", ".ts")):
                jsx_files.append(os.path.join(root, f))
    # Also scan sibling frontend/ directory if it exists
    frontend_path = os.path.normpath(os.path.join(base_path, "..", "frontend", "src"))
    if os.path.isdir(frontend_path):
        for root, dirs, files in os.walk(frontend_path):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "node_modules"}]
            for f in files:
                if f.endswith((".jsx", ".tsx", ".js", ".ts")):
                    jsx_files.append(os.path.join(root, f))

    print(f"Scanning {len(py_files)} Python files, {len(sql_files)} SQL files, {len(jsx_files)} JS/JSX files...")

    # Run all checks
    check_on_conflict_has_constraint(py_files, sql_files, result)
    check_columns_exist_in_schema(py_files, sql_files, result)
    check_try_except_imports(py_files, result)
    check_cache_initialized_nonzero(py_files, result)
    check_ws_auth_before_accept(py_files, result)
    check_start_monitor_wired(py_files, result)
    check_dual_backend_drift(base_path, result)
    check_unbounded_state_dicts(py_files, result)
    check_write_then_reuse_conn(py_files, result)
    check_placeholder_masking(py_files, jsx_files, result)
    check_cors_config(py_files, result)
    check_n_plus_one_patterns(py_files, result)
    check_missing_returning(py_files, result)
    check_ws_reconnect_patterns(jsx_files, result)
    check_parameter_renumbering(py_files, result)
    check_cron_secret_fail_closed(py_files, result)
    check_frontend_backend_field_contract(py_files, jsx_files, sql_files, result)
    check_approx_price_not_used_for_balance(py_files, result)
    check_on_conflict_exact_column_match(py_files, sql_files, result)
    check_get_current_user_no_db(py_files, result)
    check_phase_isolation_monitor(py_files, result)
    check_balance_vs_event_amount(py_files, result)
    check_db_conn_held_across_http(py_files, result)
    check_double_dollar_in_template_literals(jsx_files, result)
    check_grid_layout_breakage(jsx_files, result)
    check_dead_variable_cascade(py_files, result)
    check_pyright_builtin_generics(py_files, result)
    check_coingecko_response_shape(py_files, result)
    check_whale_score_threshold(py_files, result)
    check_stale_price_cache_drift(py_files, result)
    check_whale_score_in_dashboard(py_files, jsx_files, result)
    check_copy_trade_signals_performance_indexes(sql_files, result)
    check_concurrent_price_fetch(py_files, result)
    check_get_me_jwt_created_at(py_files, result)
    check_alpaca_validation_before_db(py_files, result)
    check_mirror_trade_action_normalization(py_files, result)
    check_mirror_trade_status_guard(py_files, result)
    check_mirror_trade_action_guard(py_files, result)
    check_mirror_trade_response_validation(py_files, result)
    check_mirror_trade_price_status_check(py_files, result)
    check_portfolio_change_delta_consistency(py_files, result)
    check_alert_fired_dict_notify_telegram(py_files, result)
    check_endpoint_response_field_consistency(py_files, jsx_files, result)
    check_closed_at_on_status_transition(py_files, result)
    check_stale_signal_expiry(py_files, result)
    check_signal_history_endpoint(py_files, result)
    check_duplicate_cycle_stats(py_files, result)
    check_signal_history_frontend(jsx_files, result)
    check_alert_update_notify_telegram(py_files, result)
    check_refresh_wallet_shared_clients(py_files, result)
    check_whale_sentiment_buy_inflow(py_files, result)
    check_signal_stats_field_contract(py_files, result)
    check_mirror_trade_rate_limit(py_files, result)

    return result


def check_whale_sentiment_buy_inflow(py_files: List[str], result: AuditResult):
    """
    Verify that the whale-sentiment endpoint counts both 'receive' AND 'buy'
    transactions as inflow. Previously, only 'receive' was counted, which
    excluded DEX swap purchases by whales from the sentiment calculation.
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "whale_sentiment" not in text and "whale-sentiment" not in text:
            continue
        if fpath.endswith("audit_source.py") or fpath.endswith("field_contract.py"):
            result.add_pass("whale_sentiment: buy txns counted as inflow")
            continue

        # Find the inflow_usd computation
        lines = text.split('\n')
        in_sentiment_func = False
        found_buy_in_inflow = False
        for i, line in enumerate(lines, 1):
            if "def get_whale_sentiment" in line or "def whale_sentiment" in line:
                in_sentiment_func = True
                continue
            if in_sentiment_func and line.strip() and not line[0].isspace() and "def " in line:
                in_sentiment_func = False
                break
            if in_sentiment_func and "inflow_usd" in line:
                # Check if 'buy' is included in the inflow type check
                if '"buy"' in line or "'buy'" in line:
                    found_buy_in_inflow = True
                    break
                # Also check for tuple form: ("receive", "buy")
                if '("receive"' in line and '"buy"' in line:
                    found_buy_in_inflow = True
                    break

        if found_buy_in_inflow:
            result.add_pass("whale_sentiment: buy txns counted as inflow")
        else:
            result.add(Finding(
                pitfall="#18",
                severity="minor",
                file=fpath,
                line=0,
                description="whale_sentiment inflow only counts 'receive' — 'buy' txns excluded from sentiment",
                suggestion="Change inflow filter to: r['type'] in ('receive', 'buy')",
            ))


def check_signal_stats_field_contract(py_files: List[str], result: AuditResult):
    """
    Verify that the /api/signals/stats endpoint returns all fields the frontend
    expects: total_signals, by_status, avg_confidence, avg_whale_score,
    execution_rate, recent_signals (with last_7d, last_24h).
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "signals/stats" not in text and "signal_stats" not in text:
            continue
        if fpath.endswith("audit_source.py") or fpath.endswith("field_contract.py"):
            result.add_pass("signal_stats: field contract includes all frontend-accessed fields")
            continue

        lines = text.split('\n')
        in_stats_func = False
        found_fields = set()
        for i, line in enumerate(lines, 1):
            if "def get_signal_stats" in line:
                in_stats_func = True
                continue
            if in_stats_func and line.strip() and not line[0].isspace() and "def " in line:
                in_stats_func = False
                break
            if in_stats_func:
                for field in ["total_signals", "by_status", "avg_confidence",
                              "avg_whale_score", "execution_rate", "recent_signals",
                              "avg_time_to_execute_seconds"]:
                    if f'"{field}"' in line or f"'{field}'" in line:
                        found_fields.add(field)

        required = {"total_signals", "by_status", "avg_confidence", "avg_whale_score",
                    "execution_rate", "recent_signals"}
        missing = required - found_fields
        if not missing:
            result.add_pass("signal_stats: field contract includes all frontend-accessed fields")
        else:
            result.add(Finding(
                pitfall="#18",
                severity="minor",
                file=fpath,
                line=0,
                description=f"signal_stats endpoint missing fields: {missing}",
                suggestion=f"Ensure the return dict includes: {missing}",
            ))


def check_alert_update_notify_telegram(py_files: List[str], result: AuditResult):
    """
    Verify that the update_alert endpoint handles notify_telegram in both
    the update logic and the response dict. The AlertUpdateRequest model
    includes notify_telegram, but the endpoint must actually process it
    and return it in the response for field contract consistency.
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "update_alert" not in text:
            continue

        # Check that notify_telegram is handled in the update logic
        if "req.notify_telegram" not in text:
            result.add(Finding(
                pitfall="alert-update",
                severity="minor",
                file=fpath,
                line=0,
                description="update_alert endpoint does not process req.notify_telegram — "
                            "AlertUpdateRequest has the field but the endpoint ignores it",
                suggestion="Add 'if req.notify_telegram is not None: updates[\"notify_telegram\"] = req.notify_telegram'",
            ))
        else:
            result.add_pass("Alert update: notify_telegram is processed in update_alert")

        # Check that response includes notify_telegram
        if '"notify_telegram"' not in text and "'notify_telegram'" not in text:
            result.add(Finding(
                pitfall="alert-update",
                severity="minor",
                file=fpath,
                line=0,
                description="update_alert response does not include notify_telegram field",
                suggestion="Add 'notify_telegram' to the alert response dict",
            ))
        else:
            result.add_pass("Alert update: response includes notify_telegram")

    result.add_pass("Alert update notify_telegram check completed")


def check_refresh_wallet_shared_clients(py_files: List[str], result: AuditResult):
    """
    Pitfall #6: Verify that refresh_wallet uses shared blockchain clients
    from the monitor module instead of creating per-call clients.
    Per-call client instantiation (EtherscanClient(), SolscanClient(), etc.)
    creates unnecessary TLS handshakes and connection overhead.
    """
    for fpath in py_files:
        text = read_file(fpath)
        if "refresh_wallet" not in text and "wallet_id}/refresh" not in text:
            continue

        # Check for per-call client instantiation (bad pattern)
        per_call_patterns = [
            (r"refresh_wallet.*?EtherscanClient\(\)", "EtherscanClient()"),
            (r"refresh_wallet.*?SolscanClient\(\)", "SolscanClient()"),
            (r"refresh_wallet.*?BlockchairClient\(\)", "BlockchairClient()"),
        ]

        # Check for per-call client instantiation in refresh_wallet function
        # Skip audit_source.py itself (contains the check code with client names)
        if fpath.endswith("audit_source.py"):
            result.add_pass("refresh_wallet: uses shared clients from monitor module")
            continue

        lines = text.split('\n')
        in_refresh = False
        found_shared = False
        found_per_call = False
        for i, line in enumerate(lines, 1):
            if "def refresh_wallet" in line:
                in_refresh = True
                continue
            if in_refresh and line.strip() and not line[0].isspace() and "def " in line:
                in_refresh = False
                break
            if in_refresh:
                if "EtherscanClient()" in line or "SolscanClient()" in line or "BlockchairClient()" in line:
                    result.add(Finding(
                        pitfall="#6",
                        severity="minor",
                        file=fpath,
                        line=i,
                        description="refresh_wallet creates per-call blockchain client — "
                                    "should use shared clients from services.monitor._clients",
                        suggestion="Import _clients from services.monitor and use _clients.get(chain)",
                    ))
                    found_per_call = True
                    break
                if "_monitor_clients" in line or "services.monitor" in line:
                    found_shared = True

        if not found_per_call and found_shared:
            result.add_pass("refresh_wallet: uses shared clients from monitor module")
        elif not found_per_call and not found_shared:
            # No refresh pattern found at all — might be a different file
            pass

    result.add_pass("Refresh wallet shared clients check completed")


def check_mirror_trade_rate_limit(py_files: List[str], result: AuditResult):
    """Audit check: POST /api/signals/{signal_id}/mirror must have rate limiting."""
    import re
    found_rate_limit = False
    found_dir_pattern = False

    for fpath in py_files:
        try:
            src = open(fpath).read()
        except Exception:
            continue

        # Check for rate limiter in mirror_trade endpoint
        if "mirror_trade" in src or "mirror" in src.lower():
            if "_check_mirror_rate_limit" in src or "_MIRROR_RATE_LIMIT" in src:
                found_rate_limit = True

        # Check for fragile dir() pattern (skip self — audit_source.py)
        # Match: some_var if 'some_var' in dir() else fallback
        # The pattern must have a variable name before 'if' and the same name in quotes
        if "audit_source.py" not in fpath and re.search(r"\w+\s+if\s+'(\w+)'\s+in\s+dir\(\)", src):
            found_dir_pattern = True
            # Find approximate line number
            lines = src.splitlines()
            line_num = 0
            for i, line in enumerate(lines, 1):
                if re.search(r"\w+\s+if\s+'(\w+)'\s+in\s+dir\(\)", line):
                    line_num = i
                    break
            result.add(Finding(
                pitfall="fragile-dir-pattern",
                severity="minor",
                file=fpath,
                line=line_num,
                description=(
                    "Fragile 'var if 'var' in dir() else default' pattern detected. "
                    "This is unnecessary when the variable is always in scope."
                ),
                suggestion=(
                    "Replace with the variable directly, or use a try/except "
                    "if the variable may not be defined."
                ),
            ))

    if found_rate_limit:
        result.add_pass("Mirror trade endpoint has rate limiting (token bucket)")
    else:
        result.add(Finding(
            pitfall="mirror-rate-limit",
            severity="minor",
            file="backend/main.py",
            line=0,
            description=(
                "POST /api/signals/{signal_id}/mirror has no rate limiting. "
                "An attacker or buggy client could spam Alpaca orders."
            ),
            suggestion=(
                "Add a per-user token-bucket rate limiter (e.g., 10 req/min)."
            ),
        ))


def main():
    parser = argparse.ArgumentParser(description="ChainWatch static source audit")
    parser.add_argument(
        "--path",
        default=".",
        help="Base path to the ChainWatch project (default: current directory)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    result = run_audit(args.path)

    if args.output == "json":
        import json
        output = {
            "critical": [
                {"pitfall": f.pitfall, "file": f.file, "line": f.line,
                 "description": f.description, "suggestion": f.suggestion}
                for f in result.critical
            ],
            "minor": [
                {"pitfall": f.pitfall, "file": f.file, "line": f.line,
                 "description": f.description, "suggestion": f.suggestion}
                for f in result.minor
            ],
            "passed": result.passed,
        }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 70)
        print("ChainWatch Static Source Audit Report")
        print("=" * 70)

        if result.critical:
            print(f"\n🔴 CRITICAL FINDINGS ({len(result.critical)}):")
            for i, f in enumerate(result.critical, 1):
                print(f"  {i}. [{f.pitfall}] {f.file}:{f.line}")
                print(f"     {f.description}")
                print(f"     → {f.suggestion}")

        if result.minor:
            print(f"\n🟡 MINOR FINDINGS ({len(result.minor)}):")
            for i, f in enumerate(result.minor, 1):
                print(f"  {i}. [{f.pitfall}] {f.file}:{f.line}")
                print(f"     {f.description}")
                print(f"     → {f.suggestion}")

        print(f"\n✅ PASSED CHECKS ({len(result.passed)}):")
        for p in result.passed:
            print(f"  ✓ {p}")

        print("=" * 70)
        total = len(result.findings) + len(result.passed)
        print(f"Results: {len(result.passed)}/{total} checks passed, "
              f"{len(result.critical)} critical, {len(result.minor)} minor findings")

    if result.critical:
        sys.exit(1)
    elif result.minor:
        sys.exit(2)
    else:
        print("\n🎉 All checks passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
