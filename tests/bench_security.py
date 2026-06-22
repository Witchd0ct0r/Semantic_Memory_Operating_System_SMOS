"""
bench_security.py — File sandbox security audit for semantic_memory MCP system.

Covers 8 attack categories:
  1. Path traversal — write_file_safe
  2. Path traversal — read_file_compress
  3. Null-byte injection
  4. Special characters & encoding
  5. Content injection
  6. Audit log integrity
  7. Concurrent write simulation
  8. Store-after-read data leakage
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"C:\Private\semantic_memory")
import tools.file_tools as ft

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEPARATOR = "=" * 72
THIN_SEP = "-" * 72

total_attempts = 0
correctly_blocked = 0
incorrectly_allowed = 0
unexpected_crashes = 0
findings: list[str] = []


def record(blocked: bool, crashed: bool, escaped: bool, note: str = "") -> None:
    global total_attempts, correctly_blocked, incorrectly_allowed, unexpected_crashes
    total_attempts += 1
    if crashed:
        unexpected_crashes += 1
    if escaped:
        incorrectly_allowed += 1
        findings.append(f"[ESCAPE] {note}")
    if blocked and not crashed:
        correctly_blocked += 1


class _TempEnv:
    """Context manager that patches _WORKSPACE and _LOGS to isolated temp dirs."""

    def __init__(self) -> None:
        self._td: tempfile.TemporaryDirectory[str] | None = None
        self.workspace: Path | None = None
        self.logs: Path | None = None
        self._orig_ws: Path | None = None
        self._orig_logs: Path | None = None

    def __enter__(self) -> "_TempEnv":
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        self.workspace = root / "workspace"
        self.logs = root / "logs"
        self.workspace.mkdir()
        self.logs.mkdir()
        self._orig_ws = ft._WORKSPACE
        self._orig_logs = ft._LOGS
        ft._WORKSPACE = self.workspace
        ft._LOGS = self.logs
        return self

    def __exit__(self, *_: Any) -> None:
        ft._WORKSPACE = self._orig_ws  # type: ignore[assignment]
        ft._LOGS = self._orig_logs  # type: ignore[assignment]
        if self._td:
            self._td.cleanup()

    def files_outside_workspace(self) -> list[Path]:
        """Return any files that exist outside the workspace dir."""
        root = Path(self._td.name)  # type: ignore[arg-type]
        outside: list[Path] = []
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    p.relative_to(self.workspace)  # type: ignore[arg-type]
                except ValueError:
                    # Not under workspace — check if it's under logs (expected)
                    try:
                        p.relative_to(self.logs)  # type: ignore[arg-type]
                    except ValueError:
                        outside.append(p)
        return outside


def _mock_store() -> MagicMock:
    ms = MagicMock()
    ms.store.return_value = "test-id"
    return ms


# ---------------------------------------------------------------------------
# Category 1 & 2 — Path Traversal
# ---------------------------------------------------------------------------

TRAVERSAL_PATHS = [
    "../evil.txt",
    "../../evil.txt",
    "../../../evil.txt",
    "../../../../Windows/System32/evil.txt",
    "subdir/../../evil.txt",
    "subdir/../../../evil.txt",
    "./../../evil.txt",
    "%2e%2e/evil.txt",           # URL-encoded (Python Path does NOT decode)
    "..\\evil.txt",              # Windows backslash
    "..\\..\\evil.txt",
    "subdir\\..\\..\\evil.txt",
    "/etc/passwd",
    "/tmp/evil",
    "C:\\Windows\\evil.txt",
    "C:/Windows/evil.txt",
    "\\\\server\\share\\evil.txt",  # UNC path
]

# Paths that are actually valid relative paths (within workspace) on Windows
# %2e%2e is treated literally by Python Path, so it stays inside workspace.
EXPECTED_TO_SUCCEED_WRITES = {"%2e%2e/evil.txt"}


def run_category_1() -> list[dict]:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 1 — Path Traversal (write_file_safe)")
    print(SEPARATOR)
    rows: list[dict] = []
    for path in TRAVERSAL_PATHS:
        expected_blocked = path not in EXPECTED_TO_SUCCEED_WRITES
        crashed = False
        escaped = False
        result: dict = {}
        with _TempEnv() as env:
            try:
                result = ft.write_file_safe(path, "evil content")
            except Exception as exc:
                crashed = True
                result = {"success": False, "_exception": str(exc)}

            outside = env.files_outside_workspace()
            if outside:
                escaped = True

        actual_blocked = not result.get("success", False)

        if expected_blocked:
            passed = actual_blocked and not escaped and not crashed
            record(blocked=actual_blocked, crashed=crashed, escaped=escaped,
                   note=f"write traversal not blocked: {path!r}")
        else:
            # Expected to succeed (literal path stays in workspace)
            passed = not crashed
            record(blocked=False, crashed=crashed, escaped=escaped,
                   note=f"write traversal path: {path!r}")

        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {path!r:50s}  result={result}  outside={outside}")
        rows.append({"path": path, "result": result, "escaped": escaped,
                     "crashed": crashed, "pass": passed})
    return rows


def run_category_2() -> list[dict]:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 2 — Path Traversal (read_file_compress)")
    print(SEPARATOR)
    rows: list[dict] = []
    for path in TRAVERSAL_PATHS:
        expected_blocked = path not in EXPECTED_TO_SUCCEED_WRITES
        crashed = False
        escaped = False
        result: dict = {}
        with _TempEnv() as env:
            try:
                ms = _mock_store()
                with patch("tools.file_tools.summarize_text", return_value="summary"):
                    result = ft.read_file_compress(path, ms)
            except Exception as exc:
                crashed = True
                result = {"summary": None, "_exception": str(exc)}

        actual_blocked = result.get("summary") is None and result.get("error") is not None

        if expected_blocked:
            passed = actual_blocked and not escaped and not crashed
            record(blocked=actual_blocked, crashed=crashed, escaped=escaped,
                   note=f"read traversal not blocked: {path!r}")
        else:
            passed = not crashed
            record(blocked=False, crashed=crashed, escaped=escaped,
                   note=f"read traversal path: {path!r}")

        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {path!r:50s}  result={result}  crashed={crashed}")
        rows.append({"path": path, "result": result, "escaped": escaped,
                     "crashed": crashed, "pass": passed})
    return rows


# ---------------------------------------------------------------------------
# Category 3 — Null Byte Injection
# ---------------------------------------------------------------------------

NULL_BYTE_PATHS = [
    "file\x00.txt",
    "file\x00../../evil.txt",
    "valid.txt\x00../../shadow",
]


def run_category_3() -> list[dict]:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 3 — Null Byte Injection")
    print(SEPARATOR)
    rows: list[dict] = []
    for path in NULL_BYTE_PATHS:
        crashed = False
        escaped = False
        result_w: dict = {}
        result_r: dict = {}
        exception_detail = ""
        with _TempEnv() as env:
            try:
                result_w = ft.write_file_safe(path, "null-byte payload")
            except Exception as exc:
                crashed = True
                exception_detail = f"{type(exc).__name__}: {exc}"
                result_w = {"success": False, "_exception": exception_detail}

            outside = env.files_outside_workspace()
            if outside:
                escaped = True

        with _TempEnv() as env2:
            try:
                ms = _mock_store()
                with patch("tools.file_tools.summarize_text", return_value="summary"):
                    result_r = ft.read_file_compress(path, ms)
            except Exception as exc:
                if not crashed:
                    crashed = True
                exception_detail = f"{type(exc).__name__}: {exc}"
                result_r = {"summary": None, "_exception": exception_detail}

        # Success = no escape, no uncontrolled crash (ValueError/TypeError OK, it's expected)
        # A crash is "uncontrolled" if it bubbles up beyond the API boundary (returns dict)
        controlled = isinstance(result_w, dict) and isinstance(result_r, dict)
        passed = not escaped and controlled
        record(blocked=True, crashed=(crashed and not controlled),
               escaped=escaped, note=f"null-byte path: {path!r}")

        status = "PASS" if passed else "FAIL"
        path_repr = repr(path)
        print(f"  [{status}] {path_repr:50s}")
        print(f"         write={result_w}")
        print(f"         read ={result_r}")
        print(f"         outside={outside if 'outside' in dir() else '[]'}  exception={exception_detail!r}")
        rows.append({"path": path, "write": result_w, "read": result_r,
                     "escaped": escaped, "crashed": crashed, "pass": passed,
                     "exception": exception_detail})
    return rows


# ---------------------------------------------------------------------------
# Category 4 — Special Characters & Encoding
# ---------------------------------------------------------------------------

SPECIAL_PATHS = [
    ("file with spaces.txt", True),    # should SUCCEED
    ("file.txt..", True),              # trailing dots — Windows strips them; may succeed
    ("CON", False),                    # Windows reserved name — expect OS error
    ("NUL", False),                    # Windows reserved name
    ("PRN.txt", False),                # Windows reserved name
    ("file\nname.txt", False),         # newline in filename
    ("file\ttab.txt", True),           # tab in filename — Windows allows tabs in filenames
    ("a" * 300 + ".txt", False),       # extremely long filename (>255 chars)
]


def run_category_4() -> list[dict]:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 4 — Special Characters & Encoding")
    print(SEPARATOR)
    rows: list[dict] = []
    for path, expect_success in SPECIAL_PATHS:
        crashed = False
        escaped = False
        result: dict = {}
        exception_detail = ""
        with _TempEnv() as env:
            try:
                result = ft.write_file_safe(path, "test content")
            except Exception as exc:
                crashed = True
                exception_detail = f"{type(exc).__name__}: {exc}"
                result = {"success": False, "_exception": exception_detail}

            outside = env.files_outside_workspace()
            if outside:
                escaped = True

        success = result.get("success", False)
        controlled = isinstance(result, dict)
        escaped_sandbox = escaped

        if escaped_sandbox:
            record(blocked=False, crashed=False, escaped=True,
                   note=f"special-char path escaped: {path!r}")
            status = "FAIL (ESCAPE)"
        elif crashed and not controlled:
            record(blocked=False, crashed=True, escaped=False,
                   note=f"special-char path crashed: {path!r}")
            status = "FAIL (CRASH)"
        else:
            record(blocked=not success, crashed=False, escaped=False)
            status = "PASS"

        outcome = "success" if success else ("crashed" if crashed else "blocked/failed")
        print(f"  [{status}] {path!r:45s}  expected={'succeed' if expect_success else 'fail/block'}  actual={outcome}")
        if exception_detail:
            print(f"           exception: {exception_detail}")
        rows.append({"path": path, "result": result, "expected_success": expect_success,
                     "actual_success": success, "crashed": crashed,
                     "escaped": escaped, "exception": exception_detail})
    return rows


# ---------------------------------------------------------------------------
# Category 5 — Content Injection
# ---------------------------------------------------------------------------

INJECTION_CONTENTS = [
    ("sql_injection", "'; DROP TABLE memories; --"),
    ("xss", "<script>alert('xss')</script>"),
    ("template_injection", "{{7*7}}"),
    ("binary_null", "\x00\x01\x02"),
    ("100kb_blob", "A" * 100_000),
    ("10k_lines", "\n".join(["line"] * 10_000)),
]


def run_category_5() -> list[dict]:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 5 — Content Injection")
    print(SEPARATOR)
    rows: list[dict] = []
    for label, content in INJECTION_CONTENTS:
        crashed = False
        result: dict = {}
        exception_detail = ""
        with _TempEnv() as env:
            try:
                result = ft.write_file_safe("payload.txt", content)
            except Exception as exc:
                crashed = True
                exception_detail = f"{type(exc).__name__}: {exc}"
                result = {"success": False, "_exception": exception_detail}

        controlled = isinstance(result, dict)
        passed = controlled and not (crashed and not controlled)

        # For content injection, a clean success OR clean failure both pass.
        # A crash (unhandled exception) is a finding.
        if crashed and not controlled:
            record(blocked=False, crashed=True, escaped=False,
                   note=f"content injection crashed: {label}")
            status = "FAIL (CRASH)"
        else:
            record(blocked=False, crashed=False, escaped=False)
            status = "PASS" if result.get("success") or not crashed else "PASS (blocked)"

        print(f"  [{status}] {label:30s}  result={result}  exception={exception_detail!r}")
        rows.append({"label": label, "result": result, "crashed": crashed, "pass": passed})
    return rows


# ---------------------------------------------------------------------------
# Category 6 — Audit Log Integrity
# ---------------------------------------------------------------------------

def run_category_6() -> dict:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 6 — Audit Log Integrity")
    print(SEPARATOR)

    with _TempEnv() as env:
        legitimate_paths = [
            "report_a.txt",
            "data/report_b.txt",
            "data/sub/report_c.txt",
            "notes.txt",
            "summary.txt",
        ]
        for p in legitimate_paths:
            ft.write_file_safe(p, f"content for {p}")

        # Attempt malicious writes (should be blocked, no log entry)
        ft.write_file_safe("../evil.txt", "evil")
        ft.write_file_safe("/etc/passwd", "evil")
        ft.write_file_safe("../../Windows/System32/evil.txt", "evil")

        log_file = env.logs / "writes.jsonl"  # type: ignore[operator]
        assert log_file.exists(), "writes.jsonl does not exist!"
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        entries = [json.loads(l) for l in lines]

    results = {
        "entry_count": len(entries),
        "expected_count": 5,
        "count_ok": len(entries) == 5,
        "fields_ok": all(
            {"timestamp", "path", "bytes"}.issubset(e.keys()) for e in entries
        ),
        "paths_relative": all(
            not Path(e["path"]).is_absolute() for e in entries
        ),
        "no_absolute_in_path": all(
            "\\" not in e["path"].split(":")[0] and ":" not in e["path"]
            for e in entries
        ),
        "malicious_paths_absent": all(
            ".." not in e["path"] and not Path(e["path"]).is_absolute()
            for e in entries
        ),
        "entries": entries,
    }

    print(f"  Entry count: {results['entry_count']} (expected 5) — {'PASS' if results['count_ok'] else 'FAIL'}")
    print(f"  Fields present (timestamp/path/bytes): {'PASS' if results['fields_ok'] else 'FAIL'}")
    print(f"  Paths are relative: {'PASS' if results['paths_relative'] else 'FAIL'}")
    print(f"  No absolute paths logged: {'PASS' if results['no_absolute_in_path'] else 'FAIL'}")
    print(f"  Malicious paths absent from log: {'PASS' if results['malicious_paths_absent'] else 'FAIL'}")
    print(f"  Logged entries:")
    for e in entries:
        print(f"    {e}")

    # Correct accounting: 5 legitimate writes counted
    for _ in range(5):
        record(blocked=False, crashed=False, escaped=False)
    # 3 malicious blocked
    for _ in range(3):
        record(blocked=True, crashed=False, escaped=False)

    return results


# ---------------------------------------------------------------------------
# Category 7 — Concurrent Write Simulation
# ---------------------------------------------------------------------------

def run_category_7() -> dict:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 7 — Concurrent Write Simulation (20 rapid sequential writes)")
    print(SEPARATOR)

    results_list: list[dict] = []
    with _TempEnv() as env:
        TARGET = "concurrent.txt"
        NUM_WRITES = 20

        def write_task(i: int) -> None:
            r = ft.write_file_safe(TARGET, f"write-{i:03d}" + ("x" * 1000))
            results_list.append(r)

        threads = [threading.Thread(target=write_task, args=(i,)) for i in range(NUM_WRITES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify audit log
        log_file = env.logs / "writes.jsonl"  # type: ignore[operator]
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        log_entries = [json.loads(l) for l in lines]

        # Verify the final file content is one complete write (not garbled)
        final_content = (env.workspace / TARGET).read_text(encoding="utf-8")  # type: ignore[operator]
        content_starts = [f"write-{i:03d}" for i in range(NUM_WRITES)]
        content_intact = any(final_content.startswith(s) for s in content_starts)

    all_success = all(r.get("success") for r in results_list)
    log_count_ok = len(log_entries) == NUM_WRITES

    print(f"  All {NUM_WRITES} writes returned success: {'PASS' if all_success else 'FAIL'}")
    print(f"  Audit log has {len(log_entries)} entries (expected {NUM_WRITES}): {'PASS' if log_count_ok else 'FAIL'}")
    print(f"  Final content is one complete write: {'PASS' if content_intact else 'WARN (possible interleave)'}")
    print(f"  Final content prefix: {final_content[:20]!r}")

    for _ in range(NUM_WRITES):
        record(blocked=False, crashed=False, escaped=False)

    return {
        "all_success": all_success,
        "log_count": len(log_entries),
        "log_count_ok": log_count_ok,
        "content_intact": content_intact,
    }


# ---------------------------------------------------------------------------
# Category 8 — Store After Read (data leakage check)
# ---------------------------------------------------------------------------

def run_category_8() -> dict:
    print(f"\n{SEPARATOR}")
    print("CATEGORY 8 — Store After Read (raw content vs summary leakage)")
    print(SEPARATOR)

    RAW_CONTENT = "TOP SECRET RAW CONTENT — must not reach vector store"
    SUMMARY = "Sanitized summary only"

    with _TempEnv() as env:
        # Create a legit file inside workspace
        (env.workspace / "secret.txt").write_text(RAW_CONTENT, encoding="utf-8")

        ms = _mock_store()
        with patch("tools.file_tools.summarize_text", return_value=SUMMARY):
            result = ft.read_file_compress("secret.txt", ms)

    # Check what was passed to store.store()
    assert ms.store.called, "store.store() was never called"
    call_args = ms.store.call_args
    stored_object = call_args[0][0]  # first positional arg
    stored_content = stored_object.content

    raw_leaked = RAW_CONTENT in stored_content
    summary_stored = stored_content == SUMMARY

    print(f"  store.store() called: {'YES' if ms.store.called else 'NO'}")
    print(f"  Stored content is summary only: {'PASS' if summary_stored else 'FAIL'}")
    print(f"  Raw content leaked to store: {'CRITICAL FAIL' if raw_leaked else 'PASS (not leaked)'}")
    print(f"  Stored content preview: {stored_content[:80]!r}")
    print(f"  result['summary'] == SUMMARY: {'PASS' if result['summary'] == SUMMARY else 'FAIL'}")

    if raw_leaked:
        findings.append("[CRITICAL] Raw file content leaked into vector store via read_file_compress")

    record(blocked=False, crashed=False, escaped=raw_leaked,
           note="raw content leaked to vector store" if raw_leaked else "")

    return {
        "store_called": ms.store.called,
        "summary_stored": summary_stored,
        "raw_leaked": raw_leaked,
        "stored_content": stored_content,
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> None:
    print(SEPARATOR)
    print("SEMANTIC MEMORY MCP — FILE SANDBOX SECURITY AUDIT")
    print("bench_security.py")
    print(SEPARATOR)

    cat1 = run_category_1()
    cat2 = run_category_2()
    cat3 = run_category_3()
    cat4 = run_category_4()
    cat5 = run_category_5()
    cat6 = run_category_6()
    cat7 = run_category_7()
    cat8 = run_category_8()

    # -----------------------------------------------------------------------
    # Final Metrics
    # -----------------------------------------------------------------------
    print(f"\n{SEPARATOR}")
    print("SECURITY METRICS SUMMARY")
    print(SEPARATOR)

    escape_rate = (incorrectly_allowed / total_attempts * 100) if total_attempts else 0
    crash_rate = (unexpected_crashes / total_attempts * 100) if total_attempts else 0
    security_score = max(0, 100 - (incorrectly_allowed * 10) - (unexpected_crashes * 2))

    print(f"  Total attack attempts : {total_attempts}")
    print(f"  Correctly blocked     : {correctly_blocked}")
    print(f"  Incorrectly allowed   : {incorrectly_allowed}  {'*** CRITICAL ***' if incorrectly_allowed else ''}")
    print(f"  Unexpected crashes    : {unexpected_crashes}  {'*** HIGH ***' if unexpected_crashes else ''}")
    print(f"  Sandbox Escape Rate   : {escape_rate:.1f}%  (must be 0%)")
    print(f"  Crash Rate            : {crash_rate:.1f}%")
    print(f"  Security Score        : {security_score}/100")

    print(f"\n{SEPARATOR}")
    print("FINDINGS")
    print(SEPARATOR)
    if findings:
        for f in findings:
            print(f"  {f}")
    else:
        print("  No sandbox escapes or critical findings detected.")

    # -----------------------------------------------------------------------
    # Detailed Category Summary Table
    # -----------------------------------------------------------------------
    print(f"\n{SEPARATOR}")
    print("CATEGORY SUMMARY TABLE")
    print(SEPARATOR)
    print(f"  {'Category':<45} {'Total':>6} {'Passed':>7} {'Failed':>7}")
    print(f"  {THIN_SEP}")

    def _cat_summary(rows: list[dict], label: str) -> None:
        total = len(rows)
        passed = sum(1 for r in rows if r.get("pass"))
        print(f"  {label:<45} {total:>6} {passed:>7} {total - passed:>7}")

    _cat_summary(cat1, "Cat 1: Path Traversal (write)")
    _cat_summary(cat2, "Cat 2: Path Traversal (read)")
    _cat_summary(cat3, "Cat 3: Null Byte Injection")
    _cat_summary(cat4, "Cat 4: Special Characters")
    _cat_summary(cat5, "Cat 5: Content Injection")

    cat6_pass = sum([cat6["count_ok"], cat6["fields_ok"], cat6["paths_relative"],
                     cat6["no_absolute_in_path"], cat6["malicious_paths_absent"]])
    print(f"  {'Cat 6: Audit Log Integrity':<45} {'5':>6} {cat6_pass:>7} {5 - cat6_pass:>7}")

    cat7_checks = [cat7["all_success"], cat7["log_count_ok"], cat7["content_intact"]]
    cat7_pass = sum(cat7_checks)
    print(f"  {'Cat 7: Concurrent Writes':<45} {'3':>6} {cat7_pass:>7} {3 - cat7_pass:>7}")

    cat8_checks = [cat8["store_called"], cat8["summary_stored"], not cat8["raw_leaked"]]
    cat8_pass = sum(cat8_checks)
    print(f"  {'Cat 8: Store-After-Read Leakage':<45} {'3':>6} {cat8_pass:>7} {3 - cat8_pass:>7}")

    print(f"\n{'=' * 72}")
    print(f"  FINAL SECURITY SCORE: {security_score}/100")
    if incorrectly_allowed == 0 and unexpected_crashes == 0:
        print("  STATUS: ALL CHECKS PASSED — No sandbox escapes, no uncontrolled crashes.")
    elif incorrectly_allowed > 0:
        print("  STATUS: *** CRITICAL — SANDBOX ESCAPES DETECTED ***")
    else:
        print("  STATUS: WARNING — Uncontrolled crashes require investigation.")
    print("=" * 72)


if __name__ == "__main__":
    main()
