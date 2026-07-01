"""
manual_rollback.py

Manual, on-demand trigger for online_learning.py's rollback_to_version().

WHY THIS EXISTS
  rollback_to_version() was written and correct, but had zero call sites
  anywhere in the codebase -- meaning if a newly deployed model started
  underperforming, there was no way to actually revert it. This script
  wires that function to something you can run by hand.

WHY MANUAL (NOT AUTOMATIC) FOR NOW
  With only ~1 day of paper trading history, there isn't enough of a
  track record yet to trust an automatic performance-threshold trigger
  to decide on its own when to roll back. Keeping this manual means you
  stay in the loop and can review context before reverting a model.

USAGE
  # See version history for a model before deciding
  python manual_rollback.py history <model_name>

  # Roll back to a specific version
  python manual_rollback.py rollback <model_name> <version>

  # Check what's currently live
  python manual_rollback.py current <model_name>

EXAMPLE
  python manual_rollback.py history conviction_scorer
  python manual_rollback.py rollback conviction_scorer 3
"""

import sys

from online_learning import (
    rollback_to_version,
    version_history,
    get_live_model,
)


def cmd_history(model_name: str) -> None:
    history = version_history(model_name)
    if not history:
        print(f"No version history found for '{model_name}'.")
        return
    print(f"Version history for '{model_name}':")
    for entry in history:
        print(f"  {entry}")


def cmd_current(model_name: str) -> None:
    live = get_live_model(model_name)
    if not live:
        print(f"No live model found for '{model_name}'.")
        return
    print(f"Currently live for '{model_name}':")
    print(f"  {live}")


def cmd_rollback(model_name: str, version_str: str) -> None:
    try:
        version = int(version_str)
    except ValueError:
        print(f"Version must be an integer, got: {version_str!r}")
        sys.exit(1)

    print(f"About to roll back '{model_name}' to version {version}.")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        print("Aborted. No changes made.")
        return

    result = rollback_to_version(model_name, version)
    print("Rollback result:")
    print(f"  {result}")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    model_name = sys.argv[2]

    if command == "history":
        cmd_history(model_name)
    elif command == "current":
        cmd_current(model_name)
    elif command == "rollback":
        if len(sys.argv) < 4:
            print("Usage: python manual_rollback.py rollback <model_name> <version>")
            sys.exit(1)
        cmd_rollback(model_name, sys.argv[3])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
