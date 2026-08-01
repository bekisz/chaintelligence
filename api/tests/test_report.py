#!/usr/bin/env python3
"""
Run the API test suite and produce formatted terminal and Markdown reports.

Usage:
    python api/tests/test_report.py                         # all tests
    python api/tests/test_report.py -t test_20              # specific test
    python api/tests/test_report.py --env POOL_MAX_CHECK=200
    python api/tests/test_report.py --report /tmp/api-test-report.md

Environment variables (passed to tests):
    POOL_MAX_CHECK         default 100
    POOL_UNISWAP_WORKERS   default 5
    PCS_MAX_CHECK          default 100
"""

import sys
import os
import io
import re
import time
import argparse
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path


KNOWN_FAILURE_PATTERNS = {
    "test_01_health": (
        "DB table freshness >3h — DAGs haven't run recently",
        "Known issue — not a code regression",
    ),
}


def _collect_tests(test_pattern=None):
    """Return list of (method_name, docstring) for matching tests."""
    test_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, test_dir)
    module = __import__("test_api", fromlist=["TestChaintelligenceAPI"])
    cls = getattr(module, "TestChaintelligenceAPI")
    tests = []
    for name in dir(cls):
        if not name.startswith("test_"):
            continue
        if test_pattern and not name.startswith(test_pattern):
            continue
        method = getattr(cls, name)
        if callable(method):
            doc = (method.__doc__ or "").strip()
            tests.append((name, doc))
    return sorted(tests)


def run_tests(verbosity=2, test_pattern=None, env_overrides=None):
    env = os.environ.copy()
    if env_overrides:
        for k, v in env_overrides.items():
            env[k] = v

    test_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, test_dir)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_file = os.path.join(test_dir, "test_api.py")
    test_meta = _collect_tests(test_pattern)
    if test_meta:
        module = __import__("test_api", fromlist=["TestChaintelligenceAPI"])
        cls = getattr(module, "TestChaintelligenceAPI")
        for method_name, _ in test_meta:
            suite.addTest(cls(method_name))
    else:
        loaded = loader.discover(os.path.dirname(test_file), pattern="test_api.py")
        suite.addTest(loaded)

    runner = unittest.TextTestRunner(
        stream=io.StringIO(),
        verbosity=verbosity,
        descriptions=True,
    )

    start = time.monotonic()
    snapshot = {k: os.environ[k] for k in env if k in os.environ}
    try:
        os.environ.update(env)
        result = runner.run(suite)
    finally:
        for k in env:
            if k in snapshot:
                os.environ[k] = snapshot[k]
            else:
                os.environ.pop(k, None)
    duration = time.monotonic() - start

    return result, runner.stream.getvalue(), duration, test_meta


def _test_status(result, method_name):
    """Return PASS/FAIL/SKIP for a test method."""
    for tc, _ in result.failures + result.errors:
        if tc._testMethodName == method_name:
            return "FAIL"
    for tc, _ in result.skipped:
        if tc._testMethodName == method_name:
            return "SKIP"
    return "PASS"


def format_report(result, duration, config, test_meta, test_pattern):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    sep = "=" * 74
    thin = "-" * 74

    title = "Chaintelligence API Test Report"
    pad = (74 - len(title)) // 2
    lines.append("")
    lines.append(f"  {' ' * pad}{title}")
    lines.append(sep)

    # Metadata
    lines.append(f"  Run:       {now}")
    lines.append(f"  Duration:  {duration:.2f}s")
    lines.append(f"  Server:    {os.getenv('API_URL', 'http://localhost:8000')}")
    if test_pattern:
        lines.append(f"  Filter:    {test_pattern}")
    lines.append("")

    # Configuration
    defaults = {"POOL_MAX_CHECK": "100", "POOL_UNISWAP_WORKERS": "5", "PCS_MAX_CHECK": "100"}
    lines.append("  Configuration")
    lines.append(thin)
    for key, desc in [
        ("POOL_MAX_CHECK", "Uniswap pools to check"),
        ("POOL_UNISWAP_WORKERS", "Gateway API workers"),
        ("PCS_MAX_CHECK", "PancakeSwap pools to check"),
    ]:
        val = config.get(key) or defaults.get(key, "")
        lines.append(f"    {key:<25} {val}  ({desc})")
    lines.append("")

    # Summary
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    lines.append("  Results Summary")
    lines.append(sep)
    lines.append(f"    Passed:   {passed}")
    lines.append(f"    Failed:   {len(result.failures) + len(result.errors)}")
    lines.append(f"    Skipped:  {len(result.skipped)}")
    lines.append(f"    Total:    {result.testsRun}")
    status_text = "All tests passed" if not (result.failures or result.errors) else f"{len(result.failures) + len(result.errors)} failure(s)"
    lines.append(f"    Status:   {status_text}")
    lines.append("")

    # Detailed results
    lines.append("  Detailed Results")
    lines.append(sep)
    for method_name, doc in test_meta:
        status = _test_status(result, method_name)
        if status == "PASS":
            icon = "  PASS"
        elif status == "SKIP":
            icon = "  SKIP"
        else:
            icon = "  FAIL"
        label = f"{method_name:<65} {icon}"
        doc_flat = doc.replace("\n", " ").replace("  ", " ").strip()
        lines.append(f"    {label}  {doc_flat}")
    lines.append("")

    # Failure details
    if result.failures or result.errors:
        lines.append("  Failure Details")
        lines.append(sep)
        for test_case, trace in result.failures + result.errors:
            test_name = test_case._testMethodName
            known = KNOWN_FAILURE_PATTERNS.get(test_name)
            lines.append(f"    {test_name}")
            # Extract assertion message
            msg = trace.split("\n")[-2] if trace.strip() else ""
            lines.append(f"      {msg}")
            if known:
                root_cause, note = known
                lines.append(f"      Root cause: {root_cause}")
                lines.append(f"      ({note})")
            lines.append("")
    lines.append("")

    return "\n".join(lines)


def format_markdown_report(result, duration, config, test_meta, test_pattern):
    """Build a Markdown report with a description and result for every test."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    failed = len(result.failures) + len(result.errors)
    status = "PASS" if not (result.failures or result.errors) else "FAIL"

    failure_traces = {
        test_case._testMethodName: trace
        for test_case, trace in result.failures + result.errors
    }
    skip_reasons = {
        test_case._testMethodName: reason
        for test_case, reason in result.skipped
    }

    lines = [
        "# Chaintelligence API Test Report",
        "",
        f"- **Status:** `{status}`",
        f"- **Run:** {now}",
        f"- **Duration:** {duration:.2f}s",
        f"- **Server:** `{os.getenv('API_URL', 'http://localhost:8000')}`",
    ]
    if test_pattern:
        lines.append(f"- **Filter:** `{test_pattern}`")

    lines.extend([
        "",
        "## Summary",
        "",
        "| Result | Count |",
        "|---|---:|",
        f"| Passed | {passed} |",
        f"| Failed | {failed} |",
        f"| Skipped | {len(result.skipped)} |",
        f"| Total | {result.testsRun} |",
        "",
        "## Configuration",
        "",
        "| Variable | Value | Purpose |",
        "|---|---|---|",
    ])

    defaults = {
        "POOL_MAX_CHECK": "100",
        "POOL_UNISWAP_WORKERS": "5",
        "PCS_MAX_CHECK": "100",
    }
    descriptions = {
        "POOL_MAX_CHECK": "Uniswap pools to check",
        "POOL_UNISWAP_WORKERS": "Gateway API workers",
        "PCS_MAX_CHECK": "PancakeSwap pools to check",
    }
    for key in descriptions:
        value = config.get(key) or defaults[key]
        lines.append(f"| `{key}` | `{value}` | {descriptions[key]} |")

    lines.extend([
        "",
        "## Test Details",
        "",
        "| Test | What it tests | Result |",
        "|---|---|---|",
    ])
    for method_name, doc in test_meta:
        test_status = _test_status(result, method_name)
        description = " ".join(doc.split()) or "No description provided."
        lines.append(f"| `{method_name}` | {description} | **{test_status}** |")

    if failure_traces or skip_reasons:
        lines.extend(["", "## Failure and Skip Details", ""])
        for method_name, trace in failure_traces.items():
            lines.extend([
                f"### `{method_name}`",
                "",
                "```text",
                trace.rstrip(),
                "```",
                "",
            ])
        for method_name, reason in skip_reasons.items():
            lines.extend([f"### `{method_name}`", "", f"Skipped: {reason}", ""])

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Run API tests with formatted report")
    parser.add_argument("-t", "--test", help="Test pattern (e.g. test_20)")
    parser.add_argument("--env", action="append", default=[], help="ENV=value overrides")
    parser.add_argument(
        "--report",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-report.md"),
        help="Markdown report path (default: api/tests/test-report.md)",
    )
    args = parser.parse_args()

    env_overrides = {}
    for e in args.env:
        if "=" in e:
            k, v = e.split("=", 1)
            env_overrides[k] = v

    merged = os.environ.copy()
    merged.update(env_overrides)
    config = dict(env_overrides)

    result, _raw_output, duration, test_meta = run_tests(
        verbosity=2, test_pattern=args.test, env_overrides=env_overrides
    )

    report = format_report(result, duration, merged, test_meta, args.test)
    print(report)

    markdown_report = format_markdown_report(
        result, duration, merged, test_meta, args.test
    )
    report_path = Path(args.report).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown_report, encoding="utf-8")
    print(f"Markdown report written to: {report_path}")

    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
