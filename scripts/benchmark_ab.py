"""A/B benchmark runner for mementic agent session efficiency.

Orchestrates manual OpenCode sessions with and without mementic MCP context.

Usage:
    python scripts/benchmark_ab.py
    python scripts/benchmark_ab.py --runs 3

Process:
    1. Loads task definitions from scripts/ab_benchmark_tasks.json
    2. For each task, runs 2 conditions (A=without mementic, B=with mementic)
    3. Outputs side-by-side comparison table
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
TASKS_FILE = PROJECT_ROOT / "scripts" / "ab_benchmark_tasks.json"
MEASURE_SCRIPT = PROJECT_ROOT / "scripts" / "benchmark_ab_measure.py"
OPENCODE_JSON = PROJECT_ROOT / "opencode.json"
AGENTS_MD = PROJECT_ROOT / "AGENTS.md"
BACKUP_DIR = PROJECT_ROOT / ".benchmark_backups"

_OP_LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "opencode" / "log"
if not _OP_LOG.exists():
    _OP_LOG = Path.home() / ".local" / "share" / "opencode" / "log"
OP_LOG_DIR = _OP_LOG


# -- Helpers -----------------------------------------------------------------

_CONTEXT_LINE_RE = re.compile(r"(\d[\d,]*).*?(\d+)%?.*?\$?([\d.]+)")


def _parse_context_line(raw: str) -> tuple[int | None, int | None, float | None]:
    """Parse OpenCode's terminal 'Context X tokens Y% used $Z.ZZ spent' line."""
    m = _CONTEXT_LINE_RE.search(raw)
    if not m:
        return None, None, None
    return int(m.group(1).replace(",", "")), int(m.group(2)), float(m.group(3))


def _run(cmd: str | list[str], check: bool = True) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        return subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=check, shell=True)
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=check)


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
    (BACKUP_DIR / "original_state.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
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
            "mementic_record_constraint, or mementic_review_context.\n"
            "These are disabled for benchmark measurement.\n",
            agents,
            flags=re.DOTALL,
        )
        AGENTS_MD.write_text(agents, encoding="utf-8")

    OPENCODE_JSON.write_text(json.dumps(oc, indent=2) + "\n", encoding="utf-8")


def _git_reset(ref: str) -> None:
    _run("git stash", check=False)
    _run(f"git checkout {ref}")
    # NOTE: do NOT use git clean -fd — it destroys untracked benchmark files


def _measure_session(session_log: Path, from_line: int = 0) -> dict:
    cmd = [sys.executable, str(MEASURE_SCRIPT), str(session_log), "--json"]
    if from_line > 0:
        cmd.extend(["--from-line", str(from_line)])
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse measurement output", "raw": result.stdout[:500]}


def _count_log_lines(log_path: Path) -> int:
    """Count lines in a file efficiently."""
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


def run_benchmark(tasks: list[dict], runs: int) -> list[dict]:
    results: list[dict] = []

    print("=" * 60)
    print("  Mementic A/B Benchmark")
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
                        results.append({"task": tid, "run": run_num, "condition": condition, "success": False, "error": "no session log"})
                        continue

                    log_lines_before = _count_log_lines(latest)
                    print(f"[log] {latest.name} has {log_lines_before:,} lines before session")

                    input("  [ACTION] Run OpenCode with the task above, then press Enter...")

                    raw_ctx = input(
                        "  [input] Paste the 'Context' line from OpenCode terminal:\n"
                        "          e.g. Context 67,901 tokens  7% used  $0.03 spent\n"
                        "          Enter 3 numbers: <tokens> <%used> <cost> (or Enter to skip)\n"
                        "  > "
                    ).strip()
                    llm_ctx_tokens, llm_ctx_pct, llm_ctx_cost = _parse_context_line(raw_ctx)
                    if llm_ctx_tokens:
                        print(f"[input]   -> {llm_ctx_tokens:,} tokens, {llm_ctx_pct}% used, ${llm_ctx_cost:.2f} spent")

                    print(f"\n[test] Running: {success_cmd}")
                    sr = _run(success_cmd, check=False)
                    success = sr.returncode == 0
                    print(f"[test] {'PASS' if success else 'FAIL'} (exit {sr.returncode})")

                    print(f"[measure] Parsing: {latest.name} (from line {log_lines_before + 1})...")
                    metrics = _measure_session(latest, from_line=log_lines_before + 1)

                    run_result = {
                        "task": tid,
                        "run": run_num,
                        "condition": condition,
                        "condition_label": "without mementic" if condition == "A" else "with mementic",
                        "success": success,
                        "session_log": str(latest),
                        "metrics": metrics,
                        "llm_context_tokens": llm_ctx_tokens,
                        "llm_context_percent": llm_ctx_pct,
                        "llm_cost_spent": llm_ctx_cost,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    results.append(run_result)

                    if "error" not in metrics:
                        print(f"[result] Files read: {metrics.get('total_file_reads', '?')}")
                        print(f"[result] LLM context tokens: {llm_ctx_tokens or '?'}")
                    print()

        # Print summary
        print("\n" + "=" * 60)
        print("  Final Summary")
        print("=" * 60)

        for task in tasks:
            tid = task["id"]
            tr = [r for r in results if r["task"] == tid]

            def _avg(rs, key):
                vals = [r["metrics"].get(key, 0) for r in rs if "metrics" in r and "error" not in r.get("metrics", {})]
                return f"{sum(vals)/len(vals):,.0f}" if vals else "-"

            def _avg_result(rs, key):
                """Average a top-level result field (not inside metrics dict)."""
                vals = [r.get(key) for r in rs if r.get(key) is not None]
                return f"{sum(vals)/len(vals):,.0f}" if vals else "-"

            def _rate(rs):
                if not rs:
                    return "-"
                return f"{sum(1 for r in rs if r.get('success'))}/{len(rs)}"

            a = [r for r in tr if r["condition"] == "A"]
            b = [r for r in tr if r["condition"] == "B"]

            print(f"\n  Task: {tid}")
            print(f"  {'Metric':<25} {'Without mementic':>18} {'With mementic':>18}")
            print(f"  {'-'*25} {'-'*18} {'-'*18}")
            print(f"  {'Files read':<25} {_avg(a, 'total_file_reads'):>18} {_avg(b, 'total_file_reads'):>18}")
            print(f"  {'Context calls':<25} {_avg(a, 'context_cost'):>18} {_avg(b, 'context_cost'):>18}")
            print(f"  {'MCP calls':<25} {'-':>18} {_avg(b, 'mcp_cost'):>18}")
            print(f"  {'--- What matters ---':<25}")
            print(f"  {'LLM context tokens':<25} {_avg_result(a, 'llm_context_tokens'):>18} {_avg_result(b, 'llm_context_tokens'):>18}")
            print(f"  {'Success rate':<25} {_rate(a):>18} {_rate(b):>18}")

        results_path = PROJECT_ROOT / "ab_benchmark_results.json"
        results_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n  Results saved to: ab_benchmark_results.json")

    finally:
        print("\n[cleanup] Restoring original config...")
        _restore_config(original_config)
        _run("git checkout -- opencode.json", check=False)
        _run("git checkout -- AGENTS.md", check=False)
        print("[cleanup] Done.")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B benchmark runner for mementic agent session efficiency.")
    parser.add_argument("--runs", type=int, default=2, help="Runs per task per condition (default: 2)")
    parser.add_argument("--tasks", type=str, default=None, help="Comma-separated task IDs to benchmark (default: all)")
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

    print(f"Will run {args.runs} runs per condition ({args.runs * len(tasks) * 2} total sessions).")
    print()
    input("Press Enter to start the benchmark...")

    run_benchmark(tasks, runs=args.runs)


if __name__ == "__main__":
    main()
