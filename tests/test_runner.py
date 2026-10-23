"""Run ChatterPal tests and generate coverage reports from any directory."""

import importlib
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PROJECT_ROOT / "tests"


def run_tests():
    """Run all tests using the coverage threshold in the project configuration."""
    print("Running ChatterPal Test Suite")
    test_args = [
        "--verbose",
        "--tb=short",
        f"--cov={PROJECT_ROOT / 'src/chatterpal'}",
        "--cov-report=term-missing",
        f"--cov-report=html:{PROJECT_ROOT / 'htmlcov'}",
        str(TEST_ROOT),
    ]
    try:
        result = pytest.main(test_args)
        if result == 0:
            print("All tests passed!")
            print(f"Coverage report generated in {PROJECT_ROOT / 'htmlcov'}")
        else:
            print(f"Tests failed with exit code: {result}")
        return result
    except Exception as exc:
        print(f"Error running tests: {exc}")
        return 1


def run_specific_module_tests():
    """Run the original core module groups and retain each group's outcome."""
    test_modules = [
        "test_config.py",
        "test_asr.py",
        "test_tts.py",
        "test_llm.py",
        "test_assessment.py",
        "test_services.py",
        "test_web_components.py",
    ]
    results = {}
    for module in test_modules:
        print(f"Testing {module}...")
        try:
            result = pytest.main(["--verbose", "--tb=short", str(TEST_ROOT / module)])
            results[module] = "PASS" if result == 0 else "FAIL"
        except Exception as exc:
            results[module] = f"ERROR: {exc}"
    print("Test Results Summary")
    for module, result in results.items():
        print(f"{module}: {result}")
    return results


def check_test_dependencies():
    """Resolve distribution names to their actual importable module names."""
    required_packages = {
        "pytest": "pytest",
        "pytest-cov": "pytest_cov",
        "pytest-asyncio": "pytest_asyncio",
        "numpy": "numpy",
        "gradio": "gradio",
    }
    missing_packages = []
    for package, module in required_packages.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing_packages.append(package)
    if missing_packages:
        print(f"Missing packages: {', '.join(missing_packages)}")
        print("Install with: uv add --dev " + " ".join(missing_packages))
        return False
    print("All test dependencies available")
    return True


def run_integration_tests():
    """Run service and web component integration groups."""
    integration_tests = [
        f"{TEST_ROOT / 'test_services.py'}::TestServiceIntegration",
        f"{TEST_ROOT / 'test_web_components.py'}::TestWebComponentIntegration",
    ]
    for test in integration_tests:
        result = pytest.main(["--verbose", "--tb=short", test])
        if result != 0:
            print(f"Integration test failed: {test}")
            return False
    print("All integration tests passed!")
    return True


def generate_test_report():
    """Generate coverage and JUnit reports in the project directory."""
    result = pytest.main([
        "--verbose",
        "--tb=short",
        f"--cov={PROJECT_ROOT / 'src/chatterpal'}",
        f"--cov-report=xml:{PROJECT_ROOT / 'coverage.xml'}",
        f"--cov-report=html:{PROJECT_ROOT / 'htmlcov'}",
        f"--junit-xml={PROJECT_ROOT / 'test-results.xml'}",
        str(TEST_ROOT),
    ])
    if result == 0:
        print(f"Test reports generated in {PROJECT_ROOT}")
    return result


def main():
    """Dispatch the requested runner command and return a useful exit status."""
    if not check_test_dependencies():
        print("Cannot run tests due to missing dependencies")
        return 1
    command = sys.argv[1] if len(sys.argv) > 1 else "coverage"
    if command == "modules":
        results = run_specific_module_tests()
        return 0 if all(result == "PASS" for result in results.values()) else 1
    if command == "integration":
        return 0 if run_integration_tests() else 1
    if command == "report":
        return generate_test_report()
    if command == "coverage":
        return run_tests()
    print(f"Unknown command: {command}")
    print("Available commands: modules, integration, report, coverage")
    return 1


def test_dependency_import_names(monkeypatch):
    """The dependency preflight must use installed plugins' import names."""
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))
    assert check_test_dependencies() is True
    assert imported == ["pytest", "pytest_cov", "pytest_asyncio", "numpy", "gradio"]


def test_missing_dependency_is_reported(monkeypatch, capsys):
    def import_module(name):
        if name == "pytest_cov":
            raise ImportError("not installed")

    monkeypatch.setattr(importlib, "import_module", import_module)
    assert check_test_dependencies() is False
    assert "pytest-cov" in capsys.readouterr().out


def test_runner_uses_project_coverage_policy(monkeypatch, tmp_path):
    """Running elsewhere must preserve cwd and the configured coverage floor."""
    monkeypatch.chdir(tmp_path)
    invoke = Mock(return_value=pytest.ExitCode.TESTS_FAILED)
    monkeypatch.setattr(pytest, "main", invoke)
    assert run_tests() == pytest.ExitCode.TESTS_FAILED
    arguments = invoke.call_args.args[0]
    assert str(TEST_ROOT) in arguments
    assert f"--cov={PROJECT_ROOT / 'src/chatterpal'}" in arguments
    assert not any(argument.startswith("--cov-fail-under") for argument in arguments)
    assert Path.cwd() == tmp_path


@pytest.mark.parametrize("module_result, expected_exit", [("PASS", 0), ("FAIL", 1)])
def test_module_command_exit_status(monkeypatch, module_result, expected_exit):
    """Failures in the modules command must reach the shell's exit status."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "check_test_dependencies", lambda: True)
    monkeypatch.setattr(
        module, "run_specific_module_tests", lambda: {"test_asr.py": module_result}
    )
    monkeypatch.setattr(sys, "argv", [__file__, "modules"])
    assert main() == expected_exit


def test_module_results_include_failures_and_errors(monkeypatch):
    """Keep running the remaining modules when one invocation fails."""
    invoke = Mock(side_effect=[0, 1, RuntimeError("broken runner"), 0, 0, 0, 0])
    monkeypatch.setattr(pytest, "main", invoke)
    results = run_specific_module_tests()
    assert results["test_config.py"] == "PASS"
    assert results["test_asr.py"] == "FAIL"
    assert results["test_tts.py"] == "ERROR: broken runner"
    assert results["test_web_components.py"] == "PASS"
    assert invoke.call_count == 7


if __name__ == "__main__":
    raise SystemExit(main())
