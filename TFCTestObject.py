"""Definition and registration of TFCTestObject"""
from __future__ import annotations
import sys
import pathlib
import math
import subprocess
import os
import time

from tfc_PyFactory.InputParameters import InputParameters
FILE_PATH = str(pathlib.Path(__file__).parent.resolve()) + "/"

sys.path.append(FILE_PATH + "../")

TEST_SYSTEM_PATH = f'{FILE_PATH}/../../'
PROJECT_ROOT_PATH = f'{TEST_SYSTEM_PATH}/../'

import tfc_PyFactory
from tfc_PyFactory import *

from checks import *


class TFCTestObject(TFCObject):
    """A Test object to organize tests. This object will load up a test with all
    the necessary bells-and-whistles. When executed by the test system, the test
    will execute the optional prerun script, after which a process will be
    submitted according to the executable and arguments specified. The test
    system will then continuously ping the test to ascertain it's completion
    status, which it does by calling it's checkProgress method. In this method
    (if the test has completed) the test will execute its checks, run the
    optional postrun script and mark itself completed.

    The working directory of the test is by default the directory in which it's
    configuration file lives. A relative offset may be added with the
    relative_offset_workdir parameter.

    """
    @staticmethod
    def getInputParameters() -> InputParameters:
        params = TFCObject.getInputParameters()

        params.addRequiredParam("args", ParameterType.STRING,
                                "Arguments to pass to the test program.")
        params.addRequiredParam("checks", ParameterType.ARRAY,
                                "An array of check-inputs.")
        params.addRequiredParam("project_root", ParameterType.STRING,
                                "The path to the project root directory")
        params.addOptionalParam("disable_mpi", True,
                                "Flag to suppress running the application "
                                "via mpi.")
        params.addOptionalParam("num_procs", 1,
                                "The number of mpi processes used.")
        params.addOptionalParam("weight_class", "",
                                "The weight class of this test. The classes "
                                "allowed depends on whether a custom weight "
                                "map has been assigned in the test system's "
                                "config file. If no custom weight map is "
                                "assigned then the weight class will be short, "
                                "intermediate, or long, otherwise the weight "
                                "class must correspond to one of the classes "
                                "in the custom map. If not supplied the test "
                                "will revert to what the test system's "
                                "default is.")
        params.addOptionalParam("outfileprefix", "",
                                'Will default to the test name + .out, '
                                'otherwise outfileprefix+.out.')
        params.addOptionalParam("skip", "",
                                "If non-empty, will skip with this message.")
        params.addOptionalParam("pass_flag", "",
                                "If non-empty, will pass with this message.")
        params.addOptionalParam("fail_flag", "",
                                "If non-empty, will fail with this message.")
        params.addOptionalParam("dependencies", [""],
                                "A list of dependent test names before this test can run.")
        params.addOptionalParam("prerun_script", "",
                                "A shell script to run before the test is executed.")
        params.addOptionalParam("precheck_script", "",
                                "A shell script to run after the case has been run but "
                                "before the checks are executed.")
        params.addOptionalParam("postrun_script", "",
                                "A shell script to run after the test is executed.")
        params.addOptionalParam("relative_offset_workdir", "",
                                "A relative working directory to be added to the "
                                "default working directory.")
        params.addOptionalParam("debug", False,
                                "Flag for debug printout. Will echo the output file to"
                                "the screen for failed tests")
        params.addOptionalParam("executable", "",
                                "Executable to use instead of system wide default.")
        params.addOptionalParam("copy_test", ["",""],
                                "A way to copy a given test and run it with a slight change"
                                "Input is a three item list specifying the name extension for the"
                                "new copied test, the path to the script to create the copied file,"
                                "and the file location of the input file to be copied.")
        params.addOptionalParam("requirements", [],
                                "An array of strings representing id_tags for requirements "
                                "that are addressed by this test.")
        params.addOptionalParam("test_doc_page", "",
                                "A link to a documentation page for this test.")

        return params


    def __init__(self, params: InputParameters) -> None:
        super().__init__(params)

        self.args_ = params.getParam("args").getStringValue()
        self.project_root_ = params.getParam("project_root").getStringValue()
        self.disable_mpi_ = params.getParam("disable_mpi").getBooleanValue()
        self.num_procs_ = params.getParam("num_procs").getIntegerValue()
        self.weight_class_ = params.getParam("weight_class").getStringValue()
        self.outfileprefix_ = params.getParam("outfileprefix").getStringValue()
        self.skip_ = params.getParam("skip").getStringValue()
        self.pass_flag_ = params.getParam("pass_flag").getStringValue()
        self.fail_flag_ = params.getParam("fail_flag").getStringValue()
        self.dependencies_ = params.getParam("dependencies")
        self.prerun_script_ = params.getParam("prerun_script").getStringValue()
        self.precheck_script_ = params.getParam("precheck_script").getStringValue()
        self.postrun_script_ = params.getParam("postrun_script").getStringValue()
        self.relative_offset_workdir_ = \
          params.getParam("relative_offset_workdir").getStringValue()
        self.debug_ = params.getParam("debug").getBooleanValue()
        self.executable_ = params.getParam("executable").getStringValue()
        self.copy_test_ = params.getParam("copy_test")

        self.requirements_ = []
        requirements_param = params.getParam("requirements")
        for sub_param in requirements_param:
            self.requirements_.append(sub_param.getStringValue())

        self.test_doc_page_ = params.getParam("test_doc_page").getStringValue()

        self.test_system_reference_ = None

        self.checks_: list[CheckBase] = []
        self._process_ = None
        self._time_start_ = time.perf_counter()
        self._case_time_ = self._time_start_
        self._time_end_ = self._time_start_
        self._time_current_ = 0.0
        self._command_ = ""

        self.check_inputs = params.getParam("checks")
        for check_input in self.check_inputs:
            id = str(len(self.checks_))
            check = PyFactory.makeObject(id, check_input)
            self.checks_.append(check)

        self.name_ = self.name_.replace("\\", "/")
        print(f'  Created test-job \"{self.name_}\" with {len(self.checks_)} checks')

        self.ran_: bool = False
        self.submitted_: bool = False
        self.passed_: bool = False
        self.dependency_failed_: bool = False
        self.time_limit_: bool = False

        self.test_result_annotation_ = ""

        self.tagged_results_ = {}

        # Set console output file
        dir_, testname_ = os.path.split(self.name_)

        prefix = testname_ if self.outfileprefix_ == "" else self.outfileprefix_

        self.cout_filepath_ = dir_ + f"/out/{prefix}.cout"
        self.cout_fileunit_ = None

        self.console_output_ = ""


    def setTestSystemReference(self, ref):
        self.test_system_reference_ = ref

        self.relative_offset_workdir_ = \
          self.keywordReplace(self.relative_offset_workdir_)

    def keywordReplace(self, input) -> str:
        dir_, test_name = os.path.split(self.name_)
        output = input.replace("$TEST_NAME", test_name)
        output = output.replace("$PROJECT_ROOT", self.project_root_)
        compiler_str = os.environ.get('COMPILER')
        output = output.replace("$TPF_LOC",
                                self.project_root_+f'test/exe/{compiler_str}/')
        # Executable extension
        exe_ext = ".exe" if os.name == "nt" else ".x"
        output = output.replace("$EXE_EXT", exe_ext)

        env_vars = self.test_system_reference_.env_vars_

        for env_var_name in env_vars:
            if env_var_name in os.environ:
                env_var_value = os.environ[env_var_name]
                if env_var_value != None:
                    output = output.replace(f"${env_var_name}", str(env_var_value))

        return output

    def checkDependenciesMet(self, tests: list[TFCTestObject]) -> bool:
        """Determines, from the supplied tests-list, whether this
        test's dependent tests have run.
        """
        try:
            if str(*self.dependencies_.sub_params) == '""':
                return True
        except:
            pass

        my_dir = self.name_.rsplit("/", 1)[0] if "/" in self.name_ else ""

        for dependency in self.dependencies_:
            dep_name = dependency.getStringValue()

            # Prefer a dependency in the same directory; fall back to any match.
            # This resolves ambiguity when multiple tests share the same short name.
            same_dir_match = None
            any_match = None
            for test in tests:
                test_short = test.name_.rsplit("/", 1)[-1]
                if test_short != dep_name:
                    continue
                test_dir = test.name_.rsplit("/", 1)[0] if "/" in test.name_ else ""
                if test_dir == my_dir:
                    same_dir_match = test
                    break  # same-dir match is definitive
                if any_match is None:
                    any_match = test

            match = same_dir_match or any_match

            if match is None:
                # Dependency not found in the active test list — skip this test.
                self.skip_ = "Dependency not active."
                continue

            if not match.ran_:
                return False

            # Dependency ran — check if it hit a time limit (counts as failed)
            if match.time_limit_:
                self.dependency_failed_ = True
                self.fail_flag_ = match.name_.rsplit("/", 1)[-1]

        return True

    def submit(self, test_system) -> None:
        """Submits the test to a process call"""
        self._time_start_ = time.perf_counter()
        self.submitted_ = True

        dir_, testname_ = os.path.split(self.name_)

        if self.skip_ != "" or self.dependency_failed_ == True:

            return

        if not self.prerun_script_ == "":
            script = self.prerun_script_
            script = self.keywordReplace(script)
            preprocess = test_system._runScript(script, cwd=dir_)
            out = preprocess.stdout
            err = preprocess.stderr
            error_code = preprocess.returncode

            if error_code != 0:
                print('\033[31mERROR: Prerun script for test ' +
                 f'{self.name_}:\n{script}\n failed with:\n' +
                 f'{err}\n' +
                 f'Output:\n{out}' +
                 '\033[0m')

        # Build the command
        cmd = ""
        if not self.disable_mpi_:
            cmd += "mpiexec "
            cmd += "-np " + str(self.num_procs_) + " "
        if self.executable_ == "":
            cmd += test_system.executable_ + " "
        else:
            cmd += self.executable_ + " "
        cmd += test_system.default_args_ + " "
        cmd += self.args_ + " "

        cmd = self.keywordReplace(cmd)

        self._command_ = cmd

        self._time_start_ = time.perf_counter()

        # Make the output directory
        if not os.path.isdir(dir_+"/out"):
            os.mkdir(dir_+"/out")

        # Open the console output file
        self.cout_fileunit_ = open(self.cout_filepath_, "w")
        self.cout_fileunit_.write(self._command_ + "\n")
        self.cout_fileunit_.flush()

        # Run the command
        self._process_ = subprocess.Popen(cmd,
                                        cwd=dir_ + "/" +
                                            self.relative_offset_workdir_,
                                        shell=True,
                                        stdout=self.cout_fileunit_,
                                        stderr=subprocess.STDOUT,
                                        universal_newlines=True)


    def messageResult(self, max_num_procs: int, print_width: int,
                      annotations: list[str], cntl_char_pad: int,
                      print_result_tags:bool = False) -> tuple:
        """
        Generates a formatted message summarizing the result of a test or
        process execution, along with updated control character padding.

        Args:
            max_num_procs (int):       The maximum number of processes used,
                                       which determines the width of the process
                                       count in the output string.

            print_width (int):         The desired width of the output string,
                                       used for formatting.

            annotations (list of str): A list of annotations to include in the
                                       result message.

            cntl_char_pad (int):       The initial padding for control
                                       characters, which will be updated
                                       based on the formatting applied in
                                       the message.
            print_result_tags (bool)   If set to true, will print result tags
                                       on the message.

        Returns:
            tuple: A tuple containing:
                - message (str):       The formatted result message including
                                       process count, test name, annotations,
                                       pass/fail status, and execution time.

                - cntl_char_pad (int): The updated control character padding
                                       after formatting.
        """
        pcount_width = int(math.floor(math.log10(max_num_procs)))+1

        prefix = f"\033[33m[{self.num_procs_:{pcount_width}d}]\033[0m  "
        cntl_char_pad += 5 + 4

        suffix = ""
        for annotation in annotations:
            if self.fail_flag_ != "" or self.dependency_failed_:
                suffix += "  \033[35m[" + annotation + "]\033[0m"
            else:
                suffix += "  \033[36m[" + annotation + "]\033[0m"
            cntl_char_pad += 5 + 4
        suffix += "  \033[32mPassed\033[0m" if self.passed_ else (
            "  \033[35mFailed\033[0m" if self.fail_flag_ != "" or self.dependency_failed_ else
            "  \033[31mFailed\033[0m")

        self.test_result_annotation_ = ""
        for annotation in annotations:
            self.test_result_annotation_ += f"[{annotation}]"

        cntl_char_pad += 5 + 4

        time_taken = self._time_end_ - self._time_start_

        width = print_width - len(prefix) - len(self.name_) \
              - len(suffix) + cntl_char_pad
        width = max(width, 0)

        suffix = '  ' + '.' * width + suffix + f" {time_taken:.1g}s"

        message = prefix + self.name_ + suffix

        # This will print a quick view of result tags, meant
        # only for convenience
        if print_result_tags and len(self.tagged_results_) > 0:
            tag_results = ""
            for id, tag in enumerate(self.tagged_results_):
                result = self.tagged_results_[tag]
                tag_results += f"{tag}={result}"
                if (id+1) < len(self.tagged_results_):
                    tag_results += ", "

            message += " " + tag_results

        if self.debug_:
            for check in self.checks_:
               message += "\n[Time of check " + check.__dict__["name_"] + f": {check.check_time_}]"

        return message, cntl_char_pad


    def checkProgress(self, test_system) -> str:
        """Checks whether the process is running and returns 'Running' if it is.
        If it is not running anymore the checks will be executed."""

        if self.ran_:
            return "Done"

        print_result_tags = test_system.tests_print_result_tags_

        error_code = 0
        dir_, testname_ = os.path.split(self.name_)
        annotations = []
        cntl_char_pad = 0

        self.fail_flag_reason_ = ""

        if self.skip_ == "":

            if self.dependency_failed_ == True:
                self.fail_flag_reason_ = f'Dependency "{self.fail_flag_}" failed.'
                annotations.append(f"Note: {self.fail_flag_reason_}")
                message, cntl_char_pad = (
                self.messageResult(test_system.max_num_procs_,
                                   test_system.print_width_,
                                   annotations,
                                   cntl_char_pad))
                print(message)
                self.ran_ = True
                return "Done"

            if self._process_.poll() is not None:

                self._process_.wait()
                self._case_time_ = time.perf_counter() - self._time_start_

                # Close the console output file and reopen it to read its
                # contents into memory
                self.cout_fileunit_.close()

                try:
                    with open(self.cout_filepath_, "r") as cout_file:
                        self.console_output_ = cout_file.read()
                except Exception as e:
                    annotations.append("Failed to open console output")

                # Other house keeping
                error_code = self._process_.returncode

            else:

                # If time limits are being enforced
                if not test_system.no_time_limit_:

                    # Set the current run time so we can check if the test is hanging
                    self._time_current_ = time.perf_counter() - self._time_start_

                    # Check if the test has run beyond permitted time.
                    # This prevents hanging tests that never finish.
                    if self.weight_class_ in test_system.weight_map_:
                        time_limit = test_system.weight_map_[self.weight_class_]
                        if self._time_current_ >= time_limit:
                            self.time_limit_ = True
                            self.fail_flag_ = (
                            f'"{self.weight_class_}" weight test '
                            f'exceeded time limit of {time_limit}s.')
                            annotations.append(f"Note: {self.fail_flag_}")
                            self.fail_flag_reason_ = f"Fail flag: {self.fail_flag_}"
                            self._process_.terminate()
                            self._time_end_ = time.perf_counter()
                            message, cntl_char_pad = (
                            self.messageResult(test_system.max_num_procs_,
                                               test_system.print_width_,
                                               annotations,
                                               cntl_char_pad))
                            print(message)
                            self.ran_ = True
                            return "Done"

                return "Running"

            if not self.precheck_script_ == "":
                script = self.precheck_script_
                script = self.keywordReplace(script)
                preprocess = test_system._runScript(script, cwd=dir_)
                out = preprocess.stdout
                err = preprocess.stderr
                error_code = preprocess.returncode

                if error_code != 0:
                    print('\033[31mERROR: Precheck script for test ' +
                    f'{self.name_}:\n{script}\n failed with:\n' +
                    f'{err}\n' +
                    f'Output:\n{out}' +
                    '\033[0m')

            self.passed_ = True
            test_config = dict(
                test = self,
                test_system = test_system,
                output = self.console_output_,
                error_code = error_code,
                out_file_name = self.cout_filepath_,
                out_directory = dir_+"/out",
                work_directory = dir_ + "/" + self.relative_offset_workdir_,
                test_file_directory = dir_,
                test_name = self.name_,
                checks = self.check_inputs
            )

            for check in self.checks_:
                result = check.timedExecuteCheck(test_config, annotations)
                if not result:
                    self.passed_ = False

            self._time_end_ = time.perf_counter()

            if self.pass_flag_ != "":
                annotations.append( f"Note: {self.pass_flag_}")
                self.passed_ = True

            elif self.fail_flag_ != "":
                annotations.append( f"Note: {self.fail_flag_}")
                self.passed_ = False
                self.fail_flag_reason_ = f"Fail flag: {self.fail_flag_}"

            else:
                pass

        else: # skipped
            self._time_end_ = time.perf_counter()
            self.passed_ = True
            annotations.append( f"Skipped: {self.skip_}" )

        message, cntl_char_pad = self.messageResult(test_system.max_num_procs_,
                                                    test_system.print_width_,
                                                    annotations,
                                                    cntl_char_pad,
                                                    print_result_tags)
        print(message)

        if self.skip_ == "":
            if not self.postrun_script_ == "":
                script = self.postrun_script_
                script = self.keywordReplace(script)
                preprocess = test_system._runScript(script, cwd=dir_)
                out = preprocess.stdout
                err = preprocess.stderr
                error_code = preprocess.returncode

                if error_code != 0:
                    print('\033[31mERROR: Postrun script for test ' +
                    f'{self.name_}:\n{script}\n failed with:\n' +
                    f'{err}\n' +
                    f'Output:\n{out}' +
                    '\033[0m')
                if self.debug_:
                    print("DEBUG: use postrun_script to print useful output here")
                    print(out)
            else:
                pass

        self.ran_ = True

        return "Done"


PyFactory.register(TFCTestObject, "TFCTestObject")
