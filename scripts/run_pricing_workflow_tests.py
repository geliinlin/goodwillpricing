"""Run the maintained pricing-decision and barcode offline unit tests.

Run from project root: python -m scripts.run_pricing_workflow_tests
No real MySQL records are changed.
"""

from pathlib import Path
import unittest


def main() -> None:
    test_dir = Path(__file__).resolve().parents[1] / "tests"
    if not test_dir.is_dir():
        raise SystemExit(f"Tests folder not found: {test_dir}")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for pattern in ("test_pricing_decision.py", "test_barcode.py"):
        suite.addTests(loader.discover(start_dir=str(test_dir), pattern=pattern))
    if suite.countTestCases() == 0:
        raise SystemExit("Pricing and barcode tests not found; install the tests/ files first.")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
