#!/usr/bin/env python3
"""Test runner for the longport-quant system."""

import asyncio
import sys
import os
from datetime import datetime
import subprocess

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger


class TestRunner:
    """Comprehensive test runner with reporting."""

    def __init__(self):
        """Initialize test runner."""
        self.results = {}
        self.start_time = None
        self.end_time = None

    def run_unit_tests(self):
        """Run all unit tests."""
        print("\n" + "="*60)
        print("RUNNING UNIT TESTS")
        print("="*60)

        test_modules = [
            "tests.test_data_sync",
            "tests.test_indicators",
            "tests.test_strategies",
            "tests.test_risk_controls"
        ]

        for module in test_modules:
            print(f"\nTesting {module}...")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", f"{module.replace('.', '/')}.py", "-v"],
                capture_output=True,
                text=True
            )

            self.results[module] = {
                'returncode': result.returncode,
                'passed': result.returncode == 0,
                'output': result.stdout + result.stderr
            }

            if result.returncode == 0:
                print(f"✅ {module} - PASSED")
            else:
                print(f"❌ {module} - FAILED")
                print(result.stderr[:500])  # Show first 500 chars of error

    def run_integration_tests(self):
        """Run integration tests."""
        print("\n" + "="*60)
        print("RUNNING INTEGRATION TESTS")
        print("="*60)

        print("\nTesting integration flows...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_integration.py", "-v", "-s"],
            capture_output=True,
            text=True
        )

        self.results["integration"] = {
            'returncode': result.returncode,
            'passed': result.returncode == 0,
            'output': result.stdout + result.stderr
        }

        if result.returncode == 0:
            print("✅ Integration tests - PASSED")
        else:
            print("❌ Integration tests - FAILED")

    def run_performance_tests(self):
        """Run performance benchmarks."""
        print("\n" + "="*60)
        print("RUNNING PERFORMANCE TESTS")
        print("="*60)

        # Test batch insert performance
        print("\nTesting batch insert performance...")
        try:
            from tests.test_performance import run_batch_insert_benchmark
            result = asyncio.run(run_batch_insert_benchmark())
            self.results["performance_batch"] = result
            print(f"✅ Batch insert: {result.get('records_per_second', 0):.0f} records/sec")
        except Exception as e:
            print(f"❌ Performance test failed: {e}")
            self.results["performance_batch"] = {'error': str(e)}

    def run_coverage_analysis(self):
        """Run test coverage analysis."""
        print("\n" + "="*60)
        print("RUNNING COVERAGE ANALYSIS")
        print("="*60)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--cov=longport_quant", "--cov-report=term-missing"],
            capture_output=True,
            text=True
        )

        self.results["coverage"] = {
            'output': result.stdout
        }

        # Extract coverage percentage
        for line in result.stdout.split('\n'):
            if "TOTAL" in line:
                parts = line.split()
                if len(parts) >= 4:
                    coverage_pct = parts[-1]
                    print(f"📊 Total coverage: {coverage_pct}")
                    break

    def generate_report(self):
        """Generate test report."""
        print("\n" + "="*60)
        print("TEST REPORT")
        print("="*60)

        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results.values()
                          if isinstance(r, dict) and r.get('passed', False))

        print(f"\nTest Summary:")
        print(f"  Total test suites: {total_tests}")
        print(f"  Passed: {passed_tests}")
        print(f"  Failed: {total_tests - passed_tests}")
        print(f"  Success rate: {passed_tests/total_tests*100:.1f}%")

        print("\nDetailed Results:")
        for name, result in self.results.items():
            if isinstance(result, dict):
                status = "✅ PASS" if result.get('passed', False) else "❌ FAIL"
                print(f"  {name:30} {status}")

        # Save report to file
        report_file = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_file, 'w') as f:
            f.write("LONGPORT-QUANT TEST REPORT\n")
            f.write(f"Generated: {datetime.now()}\n")
            f.write("="*60 + "\n\n")

            for name, result in self.results.items():
                f.write(f"\n{name}\n")
                f.write("-"*40 + "\n")
                if isinstance(result, dict):
                    f.write(f"Status: {'PASSED' if result.get('passed', False) else 'FAILED'}\n")
                    if 'output' in result:
                        f.write(f"Output:\n{result['output'][:1000]}\n")

        print(f"\n📄 Report saved to: {report_file}")

    def run_all(self):
        """Run all tests."""
        self.start_time = datetime.now()
        print(f"🚀 Starting test suite at {self.start_time}")

        try:
            # Run tests in order
            self.run_unit_tests()
            self.run_integration_tests()
            # self.run_performance_tests()  # Optional
            # self.run_coverage_analysis()  # Optional

        except Exception as e:
            logger.error(f"Test runner error: {e}")

        finally:
            self.end_time = datetime.now()
            duration = (self.end_time - self.start_time).total_seconds()
            print(f"\n⏱️  Total test time: {duration:.2f} seconds")

            # Generate report
            self.generate_report()


async def run_batch_insert_benchmark():
    """Simple performance benchmark for batch inserts."""
    from longport_quant.data.batch_insert import BatchInsertService, BatchConfig
    from longport_quant.persistence.db import DatabaseSessionManager
    from longport_quant.config.settings import get_settings

    settings = get_settings()
    test_dsn = settings.database_dsn.replace("/longport", "/longport_test")
    db = DatabaseSessionManager(test_dsn)

    try:
        config = BatchConfig(batch_size=1000)
        service = BatchInsertService(db, config)

        # Create test records
        records = [
            {
                'symbol': f'TEST{i}.HK',
                'trade_date': datetime.now().date(),
                'open': 100.0,
                'high': 105.0,
                'low': 99.0,
                'close': 104.0,
                'volume': 1000000
            }
            for i in range(10000)
        ]

        start = datetime.now()
        result = await service.bulk_insert_klines_optimized(
            "kline_daily",
            records,
            ["symbol", "trade_date"]
        )
        elapsed = (datetime.now() - start).total_seconds()

        return {
            'total_records': len(records),
            'elapsed_time': elapsed,
            'records_per_second': len(records) / elapsed if elapsed > 0 else 0
        }

    finally:
        await db.close()


def main():
    """Main entry point."""
    runner = TestRunner()

    if len(sys.argv) > 1:
        test_type = sys.argv[1]

        if test_type == "unit":
            runner.run_unit_tests()
        elif test_type == "integration":
            runner.run_integration_tests()
        elif test_type == "performance":
            runner.run_performance_tests()
        elif test_type == "coverage":
            runner.run_coverage_analysis()
        else:
            print(f"Unknown test type: {test_type}")
            print("Available: unit, integration, performance, coverage")
            sys.exit(1)
    else:
        # Run all tests
        runner.run_all()


if __name__ == "__main__":
    main()