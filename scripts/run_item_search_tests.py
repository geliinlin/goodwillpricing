"""Run the maintained Item Search offline unit tests (no MySQL writes).

Run from project root: python -m scripts.run_item_search_tests
The actual tests live in tests/test_item_search*.py, not here.
"""

from pathlib import Path
import unittest


def main() -> None:
    test_dir = Path(__file__).resolve().parents[1] / "tests"
    if not test_dir.is_dir():
        raise SystemExit(f"Tests folder not found: {test_dir}")
    suite = unittest.TestLoader().discover(
        start_dir=str(test_dir), pattern="test_item_search*.py"
    )
    if suite.countTestCases() == 0:
        raise SystemExit("No item-search tests found; install the tests/ files first.")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
