"""
Master runner for all local (state-level) government contracts.
Processes Texas ESBD, Pennsylvania eMarketplace, and Louisiana LaPAC
in sequence, then optionally triggers the n8n local contract flow.
"""
import sys
import time
import traceback

from esbd_csv_exporter import auto_process_yesterday_solicitations as run_texas
from localContracts_pa import main as run_pennsylvania
from localContracts_la import main as run_louisiana
from runN8nFlows import call_LocalContractFlow


STATE_RUNNERS = [
    ("Texas (ESBD / TX SmartBuy)", run_texas),
    ("Pennsylvania (eMarketplace)", run_pennsylvania),
    ("Louisiana (LaPAC)", run_louisiana),
]


def main():
    print("\n" + "=" * 80)
    print("  LOCAL CONTRACTS MASTER RUNNER")
    print("  States: Texas | Pennsylvania | Louisiana")
    print("=" * 80)

    results = {}

    for label, runner in STATE_RUNNERS:
        print(f"\n{'─' * 80}")
        print(f"  ▶  Starting: {label}")
        print(f"{'─' * 80}")
        start = time.time()
        try:
            runner()
            elapsed = time.time() - start
            results[label] = ("SUCCESS", elapsed)
            print(f"\n  ✓  {label} completed in {elapsed:.1f}s")
        except Exception as exc:
            elapsed = time.time() - start
            results[label] = ("FAILED", elapsed)
            print(f"\n  ✗  {label} failed after {elapsed:.1f}s: {exc}")
            traceback.print_exc()

    # Trigger n8n webhook after all states are processed
    print(f"\n{'─' * 80}")
    print("  ▶  Triggering n8n Local Contract Flow")
    print(f"{'─' * 80}")
    try:
        call_LocalContractFlow()
        print("  ✓  n8n flow triggered")
    except Exception as exc:
        print(f"  ✗  n8n flow failed: {exc}")

    # Summary
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    for label, (status, elapsed) in results.items():
        icon = "✓" if status == "SUCCESS" else "✗"
        print(f"  {icon}  {label:<40} {status:<10} ({elapsed:.1f}s)")
    print("=" * 80 + "\n")

    if any(s == "FAILED" for s, _ in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
