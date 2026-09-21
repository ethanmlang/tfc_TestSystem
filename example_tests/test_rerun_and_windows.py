#!/usr/bin/env python3

import os
import pathlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml


HERE = pathlib.Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parent

# tfc_TestSystem lives at REPOSITORY_ROOT and tfc_PyFactory is expected to be
# cloned alongside it, as documented by tfc_TestSystem/README.md.
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT.parent))

from TFCTestObject import TFCTestObject
from TFCTestResultsDatabase import TFCTestResultsDatabase
from TFCTestSystem import TFCTestSystem


class StringParameter:
    def __init__(self, value):
        self.value = value

    def getStringValue(self):
        return self.value


class TestDouble:
    def __init__(self, name, dependencies=()):
        self.name_ = name
        self.dependencies_ = [
            StringParameter(dependency)
            for dependency in dependencies
        ]


def make_test_system(selected_names, tests):
    system = TFCTestSystem.__new__(TFCTestSystem)
    system.selected_tests_ = list(selected_names)
    system.tests_ = list(tests)
    return system


def failed_test_is_selected():
    with tempfile.TemporaryDirectory() as temp_dir:
        results_file = pathlib.Path(temp_dir) / "TestResults.yaml"
        results_file.write_text(
            yaml.safe_dump([
                {"name": "suite/passing", "passed": True},
                {"name": "suite/failing", "passed": False},
            ]),
            encoding="utf-8",
        )

        selected_names = TFCTestResultsDatabase._failedTestNamesFromResults(
            str(results_file)
        )
        passing = TestDouble("suite/passing")
        failing = TestDouble("suite/failing")
        system = make_test_system(selected_names, [passing, failing])

        assert system._getFilteredSelectedTests() == [failing]


def selected_test_dependencies_are_included():
    setup = TestDouble("suite/setup")
    selected = TestDouble("suite/selected", dependencies=["setup"])
    unrelated = TestDouble("suite/unrelated")
    system = make_test_system(
        ["suite/selected"],
        [setup, selected, unrelated],
    )

    assert system._getFilteredSelectedTests() == [setup, selected]


def merge_preserves_results_not_rerun():
    with tempfile.TemporaryDirectory() as temp_dir:
        results_file = pathlib.Path(temp_dir) / "TestResults.yaml"
        original = [
            {"name": "suite/passing", "passed": True},
            {"name": "suite/not-rerun", "passed": False},
        ]
        results_file.write_text(
            yaml.safe_dump(original),
            encoding="utf-8",
        )

        database = TFCTestResultsDatabase()
        merged = database._mergeResultsDatabase([], str(results_file))

        assert merged == original


def merge_replaces_rerun_result():
    with tempfile.TemporaryDirectory() as temp_dir:
        results_file = pathlib.Path(temp_dir) / "TestResults.yaml"
        results_file.write_text(
            yaml.safe_dump([
                {"name": "suite/rerun", "passed": False},
                {"name": "suite/untouched", "passed": True},
            ]),
            encoding="utf-8",
        )

        rerun_result = {
            "name": "suite/rerun",
            "passed": True,
            "annotations": "passed on rerun",
        }
        database = TFCTestResultsDatabase()
        merged = database._mergeResultsDatabase(
            [rerun_result],
            str(results_file),
        )

        assert merged == [
            rerun_result,
            {"name": "suite/untouched", "passed": True},
        ]


def windows_name_matches_normalized_name():
    selected = TestDouble("test/vnv/cases/example")
    system = make_test_system(
        [r"test\vnv\cases\example"],
        [selected],
    )

    assert system._getFilteredSelectedTests() == [selected]


def main_executable_uses_native_shell():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        case_dir = temp_path / "case"
        output_dir = case_dir / "out"
        output_dir.mkdir(parents=True)

        test_object = TFCTestObject.__new__(TFCTestObject)
        test_object.name_ = (case_dir / "native-shell").as_posix()
        test_object.skip_ = ""
        test_object.dependency_failed_ = False
        test_object.prerun_script_ = ""
        test_object.disable_mpi_ = True
        test_object.num_procs_ = 1
        test_object.executable_ = ""
        test_object.args_ = "-i input.i"
        test_object.relative_offset_workdir_ = ""
        test_object.project_root_ = temp_path.as_posix() + "/"
        test_object.cout_filepath_ = str(
            output_dir / "native-shell.cout"
        )
        test_object.cout_fileunit_ = None

        test_system = SimpleNamespace(
            executable_="%RELAP_EXE%",
            default_args_="",
            env_vars_=[],
            _runScript=Mock(
                side_effect=AssertionError(
                    "The main executable must not use _runScript()."
                )
            ),
        )
        test_object.test_system_reference_ = test_system
        process = object()

        try:
            with patch(
                "TFCTestObject.subprocess.Popen",
                return_value=process,
            ) as popen:
                test_object.submit(test_system)

            test_system._runScript.assert_not_called()
            popen.assert_called_once()

            command = popen.call_args.args[0]
            options = popen.call_args.kwargs

            assert command.startswith("%RELAP_EXE% ")
            assert options["shell"] is True
            assert options["stdout"] is test_object.cout_fileunit_
            assert options["stderr"] is subprocess.STDOUT
            assert test_object._process_ is process
        finally:
            if test_object.cout_fileunit_ is not None:
                test_object.cout_fileunit_.close()


TESTS = {
    "failed_test_is_selected": failed_test_is_selected,
    "selected_test_dependencies_are_included": (
        selected_test_dependencies_are_included
    ),
    "merge_preserves_results_not_rerun": (
        merge_preserves_results_not_rerun
    ),
    "merge_replaces_rerun_result": merge_replaces_rerun_result,
    "windows_name_matches_normalized_name": (
        windows_name_matches_normalized_name
    ),
    "main_executable_uses_native_shell": (
        main_executable_uses_native_shell
    ),
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TESTS:
        print("Usage: test_rerun_and_windows.py TEST_NAME")
        print("Available tests:")
        for name in sorted(TESTS):
            print(f"  {name}")
        return 2

    test_name = sys.argv[1]
    TESTS[test_name]()
    print(f"PASS: {test_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
