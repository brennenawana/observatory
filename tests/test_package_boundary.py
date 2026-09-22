from __future__ import annotations

import unittest
from importlib.metadata import distribution

import observatory


class PackageBoundaryTests(unittest.TestCase):
    def test_core_package_registers_no_entry_points(self) -> None:
        self.assertEqual(
            list(distribution("agent-observatory-contract").entry_points), []
        )

    def test_distribution_version_matches_package_version(self) -> None:
        self.assertEqual(
            distribution("agent-observatory-contract").version,
            observatory.__version__,
        )


if __name__ == "__main__":
    unittest.main()
