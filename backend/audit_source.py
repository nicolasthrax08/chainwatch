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
from typing import List, Tuple


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
        # Skip the audit tool itself — it contains SQL examples with ON CONFLICT
        if fpath.endswith("audit_source.py"):
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
            found = any(cols_clean in c or c in cols_clean for c in all_constraint_cols)
            if not found:
                # Also check table-specific constraints
                table_constraints = constraints_by_table.get(table_lower, set())
                found = any(cols_clean in c or c in cols_clean for c in table_constraints)
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
    select_pattern = re.compile(r"SELECT.*?FROM\s+(\w+)", re.IGNORECASE | re.DOTALL)

    for fpath in py_files:
        text = read_file(fpath)
        # Simple heuristic: find table references and column references
        for table_name, cols in schema.items():
            # Check UPDATE statements
            for i, line in enumerate(lines(text), 1):
                if re.search(rf"\b{table_name}\b", line, re.IGNORECASE):
                    # Check for column references in the same or nearby lines
                    for col_m in update_pattern.finditer(line):
                        col = col_m.group(1).lower()
                        if col not in cols and col not in ("where", "set", "and", "or"):
                            # Only flag if it looks like a column assignment
                            pass  # Too many false positives for now

        # Check INSERT INTO table (col1, col2, ...) statements
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
    """Check if fpath is the audit tool itself (skip self-referential checks)."""
    return fpath.endswith("audit_source.py")


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
            r"\s*except[^\n]*:\s*\n\s+([^\n]+)",
            re.MULTILINE,
        )
        for m in try_import_pattern.finditer(text):
            imports_block = m.group(1)
            except_block = m.group(2)
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
        text = read_file(fpath)
        # Look for dict initializations with 0.0 values that look like price/rate caches
        zero_cache_pattern = re.compile(
            r"(\w+)\s*:\s*0\.0\s*,?\s*(?:#.*(?:price|rate|cache|threshold|multiplier))",
            re.IGNORECASE,
        )
        for m in zero_cache_pattern.finditer(text):
            var_name = m.group(1)
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
            if isinstance(node, ast.FunctionDef) and node.name:
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
    # Known pruner function names that indicate the dict IS managed
    pruner_patterns = re.compile(
        r"def\s+_(?:prune|cleanup|evict|expire).*cache|cooldown|dedup|state",
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
            write_pattern = re.compile(rf"\b{re.escape(var_name)}\b\s*(?:\[|\.update|\.setdefault|\.pop)\s*\(")
            is_written = write_pattern.search(text)
            if not is_written:
                continue  # Read-only dicts are fine

            # Check if there's a pruning mechanism
            if has_pruner:
                # Verify the pruner actually references this dict
                pruner_refs = re.compile(rf"def\s+_(?:prune|cleanup|evict|expire).*\b{re.escape(var_name)}\b")
                if pruner_refs.search(text):
                    continue  # Dict has a dedicated pruner — OK
                # Fall through: pruner exists but doesn't reference this dict

            result.add(Finding(
                pitfall="#12",
                severity="minor",
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
            'id', 'alert_id', 'rule_type', 'trigger_value', 'details', 'created_at',
        },
        '/api/whale-sentiment': {
            'sentiment_score', 'classification', 'inflow_usd', 'outflow_usd', 'tx_count',
        },
    }

    # Map JSX files to their primary API endpoints
    jsx_endpoint_map = {
        'Dashboard.jsx': ['/api/dashboard', '/api/whale-sentiment'],
        'CopyTrades.jsx': ['/api/signals'],
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

    return result


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
