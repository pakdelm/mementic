"""OpenCode-specific A/B benchmark runner for mementic agent session efficiency.

Manages OpenCode config (opencode.json, AGENTS.md) and session log parsing
to compare agent behavior with and without mementic MCP context.

Usage:
    python scripts/opencode_benchmark.py
    python scripts/opencode_benchmark.py --runs 3
    python scripts/opencode_benchmark.py --tasks add-stale-flag

Process:
    1. Loads task definitions from scripts/benchmark_config.json
    2. For each task, runs 2 conditions (A=without mementic, B=with mementic)
    3. Parses OpenCode session logs to extract files-read, tool-call, and LLM metrics
    4. Appends results to ab_benchmark_results.json (never overwrites)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# -- Paths -------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = PROJECT_ROOT / "scripts" / "benchmark_config.json"
OPENCODE_JSON = PROJECT_ROOT / "opencode.json"
AGENTS_MD = PROJECT_ROOT / "AGENTS.md"
BACKUP_DIR = PROJECT_ROOT / ".benchmark_backups"

_OP_LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "opencode" / "log"
if not _OP_LOG.exists():
    _OP_LOG = Path.home() / ".local" / "share" / "opencode" / "log"
OP_LOG_DIR = _OP_LOG


# -- ANSI stripping -----------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# -- Helpers -----------------------------------------------------------------

_CONTEXT_LINE_RE = re.compile(r"([\d,.]+)(K)?\b.*?(\d+)%?.*?\$?([\d.]+)")


def _parse_context_line(raw: str) -> tuple[int | None, int | None, float | None]:
    m = _CONTEXT_LINE_RE.search(raw)
    if not m:
        return None, None, None
    tokens = float(m.group(1).replace(",", ""))
    if m.group(2) == "K":
        tokens *= 1000
    return int(tokens), int(m.group(3)), float(m.group(4))


def _parse_token_number(raw: str) -> int | None:
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        if raw.upper().endswith("K"):
            return int(float(raw[:-1]) * 1000)
        return int(float(raw))
    except ValueError:
        return None


_TIME_RE = re.compile(r"(\d+)\s*m\s*(\d+)\s*s", re.IGNORECASE)


def _parse_time_spent(raw: str) -> int | None:
    m = _TIME_RE.search(raw)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _run(cmd: str | list[str], check: bool = True) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        return subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=check, shell=True
        )
    return subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=check
    )


# -- Session log parsing -----------------------------------------------------

_PERMISSION_RE = re.compile(
    r"message=evaluated\s+permission=(\w+)\s+pattern=(\"\"|\".*?\"|\S+)\s+action\."
)

_START_RE = re.compile(r"message=created\s+.*?\bparentID=undefined\b")

_VERIFY_PATTERNS = {
    "pytest", "coverage", "ruff", "mypy",
    "git commit", "git diff", "git log", "git show", "git add",
    "git checkout", "git branch", "git status",
    "new-item", "remove-item", "join-path", "test-path",
    "get-content", "set-content", "convertfrom-json", "convertto-json",
    "write-host", "select-object", "pop-location", "get-location",
    "out-null", "echo",
}


def _unquote(pattern: str) -> str:
    if len(pattern) >= 2 and pattern.startswith('"') and pattern.endswith('"'):
        return pattern[1:-1]
    return pattern


def _parse_permission_lines(log_text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    clean = _strip_ansi(log_text)
    for match in _PERMISSION_RE.finditer(clean):
        tool = match.group(1)
        pattern = _unquote(match.group(2))
        results.append((tool, pattern))
    return results


def _find_last_session_start(log_text: str) -> int:
    clean = _strip_ansi(log_text)
    last_start = 0
    for match in _START_RE.finditer(clean):
        last_start = match.start()
    return last_start


def _classify_tool(tool: str, pattern: str) -> str:
    if tool.startswith("mementic_"):
        return "mcp"
    if tool in ("read", "glob", "grep"):
        return "context"
    if tool in ("edit", "write", "todowrite", "task"):
        return "impl"
    if tool == "bash":
        p_lower = pattern.lower()
        for keyword in _VERIFY_PATTERNS:
            if keyword in p_lower:
                return "verify"
        if any(cmd in p_lower for cmd in ("uv run", "git ", "python ")):
            return "verify"
        return "verify"
    if tool == "external_directory":
        return "context"
    return "other"


def _measure_session(log_path: Path, from_line: int = 0) -> dict:
    raw_text = log_path.read_text(encoding="utf-8", errors="replace")
    log_size = len(raw_text.encode("utf-8"))

    if from_line <= 0:
        scope_start = _find_last_session_start(_strip_ansi(raw_text))
        scoped_text = raw_text[scope_start:]
    else:
        lines = raw_text.splitlines(keepends=True)
        scoped_text = "".join(lines[from_line - 1:]) if from_line <= len(lines) else raw_text

    clean = _strip_ansi(scoped_text)
    permissions = _parse_permission_lines(scoped_text)

    files_read = [p for tool, p in permissions if tool == "read"]
    unique_files = sorted(set(files_read))

    tool_counts: dict[str, int] = {}
    for tool, _pattern in permissions:
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    category_counts: dict[str, int] = {}
    for tool, pattern in permissions:
        cat = _classify_tool(tool, pattern)
        category_counts[cat] = category_counts.get(cat, 0) + 1

    try:
        from mementic.format import count_tokens

        tokens = count_tokens(clean)
    except ImportError:
        tokens = len(clean) // 4

    return {
        "log_path": str(log_path),
        "log_size_bytes": log_size,
        "scoped": from_line > 0,
        "from_line": from_line if from_line > 0 else None,
        "files_read": files_read,
        "unique_files_read_count": len(unique_files),
        "unique_files_read": unique_files,
        "total_file_reads": len(files_read),
        "session_log_tokens": tokens,
        "tool_calls": tool_counts,
        "total_tool_calls": len(permissions),
        "category_breakdown": category_counts,
        "context_cost": category_counts.get("context", 0) + category_counts.get("mcp", 0),
        "mcp_cost": category_counts.get("mcp", 0),
        "verify_cost": category_counts.get("verify", 0),
        "impl_cost": category_counts.get("impl", 0),
    }


# -- Config management -------------------------------------------------------


def _latest_session_log() -> Path | None:
    logs = sorted(OP_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _backup_config() -> dict:
    BACKUP_DIR.mkdir(exist_ok=True)
    config = {}
    if OPENCODE_JSON.exists():
        config["opencode.json"] = OPENCODE_JSON.read_text(encoding="utf-8")
    if AGENTS_MD.exists():
        config["AGENTS.md"] = AGENTS_MD.read_text(encoding="utf-8")
    (BACKUP_DIR / "original_state.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    return config


def _restore_config(original: dict) -> None:
    if "opencode.json" in original:
        OPENCODE_JSON.write_text(original["opencode.json"], encoding="utf-8")
    if "AGENTS.md" in original:
        AGENTS_MD.write_text(original["AGENTS.md"], encoding="utf-8")


def _configure_condition(condition: str) -> None:
    oc = json.loads(OPENCODE_JSON.read_text(encoding="utf-8"))
    if "mcp" not in oc:
        oc["mcp"] = {}

    if condition == "A":
        if "mementic" in oc["mcp"]:
            oc["mcp"]["mementic"]["enabled"] = False
        agents = AGENTS_MD.read_text(encoding="utf-8")
        agents = re.sub(
            r"## Mementic Memory \(HARD CONSTRAINT.*?(?=\n## |\n# |\Z)",
            "",
            agents,
            flags=re.DOTALL,
        )
        AGENTS_MD.write_text(agents.strip() + "\n", encoding="utf-8")
    elif condition == "B":
        if "mementic" in oc["mcp"]:
            oc["mcp"]["mementic"]["enabled"] = True
        else:
            oc["mcp"]["mementic"] = {
                "type": "local",
                "command": ["uv", "run", "python", "-m", "mementic"],
                "enabled": True,
            }
        _run("git checkout -- AGENTS.md")
        agents = AGENTS_MD.read_text(encoding="utf-8")
        agents = re.sub(
            r"\*\*Proposing decisions/constraints.*",
            "**BENCHMARK MODE** -- do NOT call mementic_record_decision,\n"
            "mementic_record_constraint, or mementic_review_session.\n"
            "These are disabled for benchmark measurement.\n",
            agents,
            flags=re.DOTALL,
        )
        AGENTS_MD.write_text(agents, encoding="utf-8")

    OPENCODE_JSON.write_text(json.dumps(oc, indent=2) + "\n", encoding="utf-8")


def _git_reset(ref: str) -> None:
    _run("git stash", check=False)
    _run(f"git checkout {ref}")


def _count_log_lines(log_path: Path) -> int:
    count = 0
    try:
        with open(log_path, "rb") as f:
            bufsize = 1024 * 1024
            buf = f.read(bufsize)
            while buf:
                count += buf.count(b"\n")
                buf = f.read(bufsize)
        return count
    except OSError:
        return 0


# -- Main -------------------------------------------------------------------


def _write_results(results: list[dict], output_path: Path) -> None:
    """Append new results to the output file, skipping duplicates by (task, run, condition)."""
    existing: list[dict] = []
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []

    existing_keys = {(r["task"], r["run"], r["condition"]) for r in existing}
    for r in results:
        if (r["task"], r["run"], r["condition"]) not in existing_keys:
            existing.append(r)

    output_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")


def run_benchmark(tasks: list[dict], runs: int) -> list[dict]:
    results: list[dict] = []

    print("=" * 60)
    print("  Mementic A/B Benchmark (OpenCode)")
    print(f"  {len(tasks)} tasks, {runs} runs per condition")
    print("=" * 60)
    print()

    original_config = _backup_config()
    print(f"[setup] Backed up config to {BACKUP_DIR}")
    print()

    try:
        for task in tasks:
            tid = task["id"]
            desc = task["description"]
            starting_ref = task.get("starting_ref", "main")
            success_cmd = task.get("success_command", "uv run pytest")

            for run_num in range(1, runs + 1):
                for condition in ["A", "B"]:
                    label = f"{tid} run {run_num} condition {condition}"
                    print(f"\n{'=' * 60}")
                    print(f"  {label}")
                    print(f"{'=' * 60}")
                    print()

                    print(f"[git] Resetting to {starting_ref}...")
                    _git_reset(starting_ref)
                    print(f"[git] Clean state at {starting_ref}")

                    print(f"[config] Setting up condition {condition}...")
                    _configure_condition(condition)

                    if condition == "A":
                        print("  Mementic MCP: DISABLED")
                        print("  AGENTS.md: enforcement REMOVED")
                    else:
                        print("  Mementic MCP: ENABLED")
                        print("  AGENTS.md: enforcement ACTIVE")

                    print()
                    print(f"  TASK: {desc}")
                    print()
                    latest = _latest_session_log()
                    if not latest:
                        print("[warn] No session log found. Skipping.")
                        results.append({
                            "task": tid, "run": run_num, "condition": condition,
                            "success": False, "error": "no session log",
                        })
                        continue

                    log_lines_before = _count_log_lines(latest)
                    print(f"[log] {latest.name} has {log_lines_before:,} lines before session")

                    input("  [ACTION] Run OpenCode with the task above, then press Enter...")

                    raw_ctx = input(
                        "  [input] PARENT agent: paste the 'Context' line from OpenCode terminal:\n"
                        "          e.g. Context 67,901 tokens  7% used  $0.03 spent\n"
                        "          Enter 3 numbers: <tokens> <%used> <cost> (or Enter to skip)\n"
                        "  > "
                    ).strip()
                    llm_ctx_tokens, llm_ctx_pct, llm_ctx_cost = _parse_context_line(raw_ctx)
                    if llm_ctx_tokens:
                        print(f"[input]   parent -> {llm_ctx_tokens:,} tokens")

                    llm_subagent_tokens = 0
                    subagent_count = 0
                    subagent_re = re.compile(r"message=created\s+.*?\bparentID=ses_")
                    session_lines = "".join(
                        Path(str(latest))
                        .read_text(encoding="utf-8", errors="replace")
                        .splitlines(keepends=True)[log_lines_before:]
                    )
                    subagent_count = len(subagent_re.findall(session_lines))
                    for i in range(1, subagent_count + 1):
                        raw_sub = input(
                            f"  [input] Subagent {i}/{subagent_count} context tokens "
                            f"(e.g. 70.4K or 70400, Enter to skip):\n  > "
                        ).strip()
                        tokens = _parse_token_number(raw_sub)
                        if tokens:
                            llm_subagent_tokens += tokens
                            print(f"[input]   subagent {i} -> {tokens:,} tokens")
                        else:
                            print(f"[input]   subagent {i} -> skipped")
                    if subagent_count > 0:
                        print(f"[input]   subagent total: {llm_subagent_tokens:,} tokens")

                    raw_time = input(
                        "  [input] Time spent (from OpenCode terminal, e.g. '13m 57s', "
                        "Enter to skip):\n  > "
                    ).strip()
                    time_spent = _parse_time_spent(raw_time) if raw_time else None
                    if time_spent:
                        print(
                            f"[input]   time -> {time_spent // 60}m "
                            f"{time_spent % 60}s ({time_spent}s)"
                        )

                    print(f"\n[test] Running: {success_cmd}")
                    sr = _run(success_cmd, check=False)
                    success = sr.returncode == 0
                    print(f"[test] {'PASS' if success else 'FAIL'} (exit {sr.returncode})")

                    print(
                        f"[measure] Parsing: {latest.name} "
                        f"(from line {log_lines_before + 1})..."
                    )
                    metrics = _measure_session(latest, from_line=log_lines_before + 1)

                    run_result = {
                        "task": tid,
                        "run": run_num,
                        "condition": condition,
                        "condition_label": (
                            "without mementic" if condition == "A" else "with mementic"
                        ),
                        "success": success,
                        "session_log": str(latest),
                        "metrics": metrics,
                        "llm_context_tokens": llm_ctx_tokens,
                        "llm_subagent_tokens": llm_subagent_tokens,
                        "llm_total_tokens": (
                            llm_ctx_tokens + llm_subagent_tokens
                            if llm_ctx_tokens
                            else None
                        ),
                        "llm_context_percent": llm_ctx_pct,
                        "llm_cost_spent": llm_ctx_cost,
                        "subagent_count": subagent_count,
                        "time_spent_seconds": time_spent,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    results.append(run_result)

                    if "error" not in metrics:
                        print(
                            f"[result] Files read: {metrics.get('total_file_reads', '?')}"
                        )
                        if llm_ctx_tokens:
                            if llm_subagent_tokens:
                                print(
                                    f"[result] LLM context: {llm_ctx_tokens:,} + "
                                    f"{llm_subagent_tokens:,} sub = "
                                    f"{llm_ctx_tokens + llm_subagent_tokens:,} total"
                                )
                            else:
                                print(f"[result] LLM context: {llm_ctx_tokens:,}")
                        else:
                            print("[result] LLM context: ?")
                        if time_spent:
                            print(
                                f"[result] Time: {time_spent // 60}m "
                                f"{time_spent % 60}s"
                            )
                    print()

        # Print summary
        print("\n" + "=" * 60)
        print("  Final Summary")
        print("=" * 60)

        for task in tasks:
            tid = task["id"]
            tr = [r for r in results if r["task"] == tid]

            def _avg(rs, key):
                vals = [
                    r["metrics"].get(key, 0)
                    for r in rs
                    if "metrics" in r and "error" not in r.get("metrics", {})
                ]
                return f"{sum(vals)/len(vals):,.0f}" if vals else "-"

            def _avg_result(rs, key):
                vals = [r.get(key) for r in rs if r.get(key) is not None]
                return f"{sum(vals)/len(vals):,.0f}" if vals else "-"

            def _avg_time(rs):
                vals = [
                    r.get("time_spent_seconds")
                    for r in rs
                    if r.get("time_spent_seconds") is not None
                ]
                if not vals:
                    return "-"
                avg = sum(vals) / len(vals)
                return f"{int(avg // 60)}m {int(avg % 60)}s"

            def _rate(rs):
                if not rs:
                    return "-"
                return f"{sum(1 for r in rs if r.get('success'))}/{len(rs)}"

            a = [r for r in tr if r["condition"] == "A"]
            b = [r for r in tr if r["condition"] == "B"]

            print(f"\n  Task: {tid}")
            print(f"  {'Metric':<25} {'Without mementic':>18} {'With mementic':>18}")
            print(f"  {'-'*25} {'-'*18} {'-'*18}")
            print(
                f"  {'Files read':<25} {_avg(a, 'total_file_reads'):>18} "
                f"{_avg(b, 'total_file_reads'):>18}"
            )
            print(
                f"  {'Context calls':<25} {_avg(a, 'context_cost'):>18} "
                f"{_avg(b, 'context_cost'):>18}"
            )
            print(f"  {'MCP calls':<25} {'-':>18} {_avg(b, 'mcp_cost'):>18}")
            print(f"  {'--- What matters ---':<25}")
            print(
                f"  {'LLM context tokens':<25} {_avg_result(a, 'llm_total_tokens'):>18} "
                f"{_avg_result(b, 'llm_total_tokens'):>18}"
            )
            print(f"  {'Time spent':<25} {_avg_time(a):>18} {_avg_time(b):>18}")
            print(f"  {'Success rate':<25} {_rate(a):>18} {_rate(b):>18}")

        results_path = PROJECT_ROOT / "ab_benchmark_results.json"
        _write_results(results, results_path)
        print("\n  Results appended to: ab_benchmark_results.json")

    finally:
        print("\n[cleanup] Restoring original config...")
        _restore_config(original_config)
        _run("git checkout -- opencode.json", check=False)
        _run("git checkout -- AGENTS.md", check=False)
        print("[cleanup] Done.")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenCode A/B benchmark runner for mementic agent session efficiency."
    )
    parser.add_argument(
        "--runs", type=int, default=2, help="Runs per task per condition (default: 2)"
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task IDs to benchmark (default: all)",
    )
    args = parser.parse_args()

    if not TASKS_FILE.exists():
        print(f"Error: tasks file not found: {TASKS_FILE}")
        sys.exit(1)

    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))

    if args.tasks:
        task_filter = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in task_filter]
        print(f"Filtered to {len(tasks)} tasks: {[t['id'] for t in tasks]}")
    else:
        print(f"Loaded {len(tasks)} tasks.")

    total_sessions = args.runs * len(tasks) * 2
    print(f"Will run {args.runs} runs per condition ({total_sessions} total sessions).")
    print()
    input("Press Enter to start the benchmark...")

    run_benchmark(tasks, runs=args.runs)


if __name__ == "__main__":
    main()
