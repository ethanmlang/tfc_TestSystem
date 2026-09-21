import pathlib
file_path = str(pathlib.Path(__file__).parent.resolve()) + "/"

import sys
sys.path.append(file_path + "../")
sys.path.append(file_path + "./")

from tfc_PyFactory import *
import TFCTestObject
from TFCTestObject import *
from TFCTraceabilityMatrix import *
from TFCTestResultsDatabase import *

import shutil
import os
import yaml
import re
import platform
import shlex
import subprocess

# Blurb to fix yaml.safe_load reading scientific notation floats as strings,
# i.e. 1e5 is seen as a string by default.
loader = yaml.SafeLoader
loader.add_implicit_resolver(
    u'tag:yaml.org,2002:float',
    re.compile(u'''^(?:
     [-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?
    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
    |\\.[0-9_]+(?:[eE][-+][0-9]+)?
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*
    |[-+]?\\.(?:inf|Inf|INF)
    |\\.(?:nan|NaN|NAN))$''', re.X),
    list(u'-+0123456789.'))

import time

# ===================================================================
class TFCTestSystem(TFCObject, TFCTraceabilityMatrix, TFCTestResultsDatabase):
    """Test system to run any type of test with any type of executable.
    The system has a set of input parameters that can be set directly and
    additional options that can be set from a configuration file. By default
    the configuration file will be in the same directory as this file
    and have the name TestSystemCONFIG.yaml (which can be customized)."""
    @staticmethod
    def getInputParameters() -> InputParameters:
        params = TFCObject.getInputParameters()

        params.addRequiredParam("directory", ParameterType.STRING,
                                "The test directory in which to find the tests. "
                                "If this string is a comma separated string, " \
                                "each directory will be scanned.")
        params.addOptionalParam("executable", "python3",
                                "The executable to use for the tests (May be overridden"
                                " from the configuration file).")
        params.addOptionalParam("project_root", "",
                                "Root of the project. If not supplied will revert to " \
                                "current working directoy.")
        params.addOptionalParam("num_jobs", int(4),
                                "The number of jobs that may run at the same time.")
        params.addOptionalParam("weights", ["all"],
                                'Weight classes to execute. '
                                'Defaults to ["all"]. The classes allowed '
                                'depends on whether a custom weight map has '
                                'been assigned in the test system config file. '
                                'If no custom weight map is assigned then the '
                                'weight classes will be short, intermediate, or '
                                'long, or all.')
        params.addOptionalParam("no_time_limit", False,
                                "Don't enforce weight class time limits. "
                                "Defaults to False.")
        params.addOptionalParam("config_file", "TestSystemCONFIG.yaml",
                                "The name of the default config file")
        params.addOptionalParam("exclude_folders", [],
                                "List of directories to exclude when searching for test files")
        params.addOptionalParam("requirement_docs", [],
                                "List of documents to search for requirements. If a folder, "
                                "will recursively search for .md files. Start with ! to exclude a "
                                "file/folder.")
        params.addOptionalParam("requirements_matrix_outputfile", "TraceAbilityMatrix.md",
                                "File to which to write the requirements "
                                "traceability matrix.")
        params.addOptionalParam("test_results_database_outputfile", "TestResults.yaml",
                                "File to which the results database is to be written.")
        params.addOptionalParam("generate_requirements_matrix", False,
                                "Generate requirements traceability matrix.")
        params.addOptionalParam("generate_results_database", False,
                                "Generate results database.")
        params.addOptionalParam("merge_results_file", "",
                                "Existing TestResults.yaml file to merge updated test results into.")
        params.addOptionalParam("rerun_failures_file", "",
                                "Existing TestResults.yaml file whose failed tests "
                                "should be rerun.")
        params.addOptionalParam("rebuild_rtm_file", "",
                                "TestResults.yaml file from which to rebuild the "
                                "requirements traceability matrix after the run.")
        params.addOptionalParam("tests_print_result_tags", False,
                                "A flag, when set, makes tests verbosely print their"
                                " result tags and values on the same line as "
                                "the pass and fail lines.")
        params.addOptionalParam("selected_tests", [],
                                "Explicit list of test names to execute. Any dependent "
                                "tests required by the selected tests are included automatically.")


        return params


    def __init__(self, params: InputParameters) -> None:
        super().__init__(params)

        # Input parameters
        # AFCF = Also From Config File
        self.directory_ = params.getParam("directory").getStringValue()
        self.executable_ = params.getParam("executable").getStringValue() # AFCF
        self.num_jobs_ = params.getParam("num_jobs").getIntegerValue()

        weights = params.getParam("weights")
        self.weights_ = []
        for subparam in weights:
            self.weights_.append(subparam.getStringValue())

        self.no_time_limit_ = params.getParam("no_time_limit").getBooleanValue()

        # This is an absolute value that gets populated with a non-zero value by the performance unit test.
        # Case specific performance tests are benchmarked against a ratio relative to this.
        self.execution_time_ = 0.0

        self.config_file_ = params.getParam("config_file").getStringValue()
        self.compiler_ = os.getenv("COMPILER")
        self.merge_results_file_ = params.getParam("merge_results_file").getStringValue()
        self.rerun_failures_file_ = params.getParam("rerun_failures_file").getStringValue()
        self.rebuild_rtm_file_ = params.getParam("rebuild_rtm_file").getStringValue()
        selected_tests = params.getParam("selected_tests")
        self.selected_tests_ = []
        for subparam in selected_tests:
            self.selected_tests_.append(subparam.getStringValue())
        if self.rerun_failures_file_:
            if self.selected_tests_:
                raise ValueError(
                    "selected_tests and rerun_failures_file cannot both be set.")
            self.selected_tests_ = self._failedTestNamesFromResults(
                self.rerun_failures_file_)
        self.os_version_ = platform.version()
        self.os_release_ = platform.release()
        self.os_details_ = platform.platform()

        exclude_param = params.getParam("exclude_folders")
        self.exclude_folders_ = []
        for subparam in exclude_param:
            self.exclude_folders_.append(subparam.getStringValue())

        # Requirement Documents
        self.generate_requirements_matrix_ = params.getParam("generate_requirements_matrix").getBooleanValue()

        requirement_docs_param = params.getParam("requirement_docs")
        temp_requirement_docs = []
        excluded_requirement_docs = []
        for subparam in requirement_docs_param:
            str_subparam = subparam.getStringValue()
            if len(str_subparam) > 0 and str_subparam[0:1] == "!":
                excluded_requirement_docs += [str_subparam[1:]]
                continue
            temp_requirement_docs += [str_subparam]

        print(excluded_requirement_docs)

        self.requirement_docs_ = []
        if len(temp_requirement_docs) > 0:
            print("Requirement docs:")
            for req_path in temp_requirement_docs:
                if req_path in excluded_requirement_docs:
                    continue

                if os.path.isfile(req_path):
                    self.requirement_docs_ += [req_path]
                    print(f"  {req_path}")
                elif os.path.isdir(req_path):
                    for dir_path, sub_dirs, file_names in os.walk(req_path):
                        for file_name in file_names:
                            base_name, extension = os.path.splitext(file_name)
                            if extension != ".md": continue

                            sub_req_path = f"{dir_path}{file_name}"
                            if not sub_req_path in excluded_requirement_docs:
                                self.requirement_docs_ += [f"{sub_req_path}"]
                                print(f"  {sub_req_path}")
                else:
                    print(f"  {req_path} (Not Found)")

        self.requirement_blocks_ = []
        for req_doc in self.requirement_docs_:
            if not os.path.isfile(req_doc):
                continue

            doc_reqs = self.parseRequirementDocument(req_doc)

            self.requirement_blocks_.append(doc_reqs)

            # # Verbose printing for testing
            # print(f'Requirements block for "{doc_reqs["filepath"]}":')
            # for req in doc_reqs["requirements"]:
            #     print(f"  {req["id_tag"]}:")
            #     print("    " + req["basic_title"])
            #     for line in req["text"].splitlines():
            #         print("    " + line.strip())

        self.requirements_matrix_outputfile_ = \
            params.getParam("requirements_matrix_outputfile").getStringValue()

        self.generate_results_database_ = params.getParam("generate_results_database").getBooleanValue()
        self.test_results_database_outputfile_ = \
            params.getParam("test_results_database_outputfile").getStringValue()

        self.tests_print_result_tags_ = \
            params.getParam("tests_print_result_tags").getBooleanValue()

        project_root = params.getParam("project_root").getStringValue()
        if project_root == "":
            self.project_root_ = os.getcwd() + "/" if project_root == "" else project_root

        print("Project-root", self.project_root_)

        # Process the config file
        self.num_init_warnings_ = 0
        config_file_path = ""
        possible_config_file_path1 = self.project_root_ + self.config_file_
        possible_config_file_path2 = file_path + "../" + self.config_file_

        if os.path.isfile(possible_config_file_path1):
            config_file_path = possible_config_file_path1
        elif os.path.isfile(possible_config_file_path2):
            config_file_path = possible_config_file_path2
        else:
            print(f'\033[31mWARNING: No config file {self.config_file_} ' +
                  f' found at either {possible_config_file_path1} or '
                  f' {possible_config_file_path2}\033[0m')
            self.num_init_warnings_ += 1

        # Config file options
        try:
            size = shutil.get_terminal_size()
            width = size.columns
            self.print_width_ = width - 10
        except:
            self.print_width_ = 120
        self.default_args_ = ""
        self.env_vars_ = []
        self.default_weight_ = ""
        self.weight_map_ = ""

        with open(config_file_path) as yaml_file:
            yaml_dict = yaml.safe_load(yaml_file)

            for param in yaml_dict:
                if param == "default_executable":
                    self.executable_ = yaml_dict[param]
                if param == "print_width":
                    self.print_width_ = yaml_dict[param]
                if param == "default_args":
                    self.default_args_ = yaml_dict[param]
                if param == "env_vars":
                    self.env_vars_ = yaml_dict[param]
                if param == "weight_map":
                    self.weight_map_ = yaml_dict[param]
                if param == "default_weight":
                    self.default_weight_ = yaml_dict[param]

                if param == "requirement_docs":
                    requirement_docs = yaml_dict[param]
                    self.requirement_docs_ = []
                    for subparam in requirement_docs:
                        self.requirement_docs_.append(subparam)

                    if len(self.requirement_docs_) > 0:
                        print("Requirement docs:")
                        for req_doc in self.requirement_docs_:
                            existence_status = "" if os.path.isfile(req_doc) else " (not found)"
                            print(f"  {req_doc}" + existence_status)

                    self.requirement_blocks_ = []
                    for req_doc in self.requirement_docs_:
                        if not os.path.isfile(req_doc):
                            continue

                        doc_reqs = self.parseRequirementDocument(req_doc)

                        self.requirement_blocks_.append(doc_reqs)

                if param == "requirements_output":
                    self.requirements_matrix_outputfile_ = yaml_dict[param]
                if param == "results_output":
                    self.test_results_database_outputfile_= yaml_dict[param]

        if self.merge_results_file_:
            self.generate_results_database_ = True
            self.test_results_database_outputfile_ = self.merge_results_file_

        if self.weight_map_ == "":
            self.weight_map_ = {"short": 2.0, "intermediate": 10.0, "long": 20.0}

        if self.default_weight_ != "":
            if self.default_weight_ not in self.weight_map_:
                print(f'\033[31mWARNING: default_weight set in config file {self.config_file_} ' +
                      f'but not found in weight_map. Ignoring default_weight.\033[0m')
                self.default_weight_ = ""

        if "all" in self.weights_:
            self.weights_ = list(self.weight_map_.keys())

        # check if we have unrecognized weights
        for weight in self.weights_:
            if weight not in self.weight_map_:
                print(f'\033[31mWARNING: weight "{weight}" is not in weight_map. '
                      f'Falling back to "all".\033[0m')
                self.weights_ = list(self.weight_map_.keys())

        self.weight_classes_allowed_ = self.weights_

        # Append "none" weight class to weight_classes_allowed
        # This makes the test system agnostic to the weight class definitions
        # and allows tests to run that have no assignment without explicity
        # assigning them to an arbitrary member of the weight classes
        self.weight_classes_allowed_.append("none")

        self.max_num_procs_ = 1

        self.tests_: list[TFCTestObject] = []

        directories = []
        if self.directory_.find(",") < 0:
            directories.append(self.directory_)
        else:
            directory_strings = self.directory_.split(",")
            directories = directory_strings

        for directory in directories:
            test_files = self._recursiveFindTestListFiles(directory,
                                                          self.exclude_folders_,
                                                          True)
            self._parseTestFiles(test_files=test_files)

        if self.selected_tests_ or self.rerun_failures_file_:
            self.tests_ = self._getFilteredSelectedTests()

        for test in self.tests_:
            self.max_num_procs_ = max(self.max_num_procs_, test.num_procs_)

        # Clean up executable
        exe_printed_value = self.executable_.strip()
        if exe_printed_value.find("$") == 0:
            original_exe = exe_printed_value
            try:
                env_var_value = os.environ[self.executable_[1:]]
                exe_printed_value += f" ({env_var_value})"
            except:
                exe_printed_value = original_exe

        print("\n***** TFCTestSystem created *****")

        print(f" Main executable          : {exe_printed_value}")
        print(f" Number of jobs           : {self.num_jobs_}")
        print(f" Weight map               : {self.weight_classes_allowed_}")
        print(f" Weight class time limits : {self.weight_map_}")
        print(f" Default weight class     : {self.default_weight_}")
        print(f" Compiler env-var         : {self.compiler_}")
        print(f" System details           : {self.os_details_}")
        print(f" System version           : {self.os_version_}")
        print(f" System release           : {self.os_release_}")
        print(f" ")

    def _runScript(self, script, cwd=None):
        """Run a POSIX-style test script and wait for it to finish."""
        if isinstance(script, (list, tuple)):
            command = shlex.join(
                str(argument)
                for argument in script
            )
        else:
            command = str(script)

        if platform.system() == "Windows":
            # Commands run through Git Bash, so use POSIX-compatible
            # separators for any Windows paths embedded in the command.
            command = command.replace("\\", "/")

            bash = None

            for variable in ("ProgramFiles", "ProgramFiles(x86)"):
                program_files = os.environ.get(variable)
                if program_files:
                    candidate = os.path.join(
                        program_files,
                        "Git",
                        "bin",
                        "bash.exe",
                    )
                    if os.path.isfile(candidate):
                        bash = candidate
                        break

            if bash is None:
                bash = shutil.which("bash")

            if bash is None:
                raise FileNotFoundError(
                    "Git Bash was not found. Install Git for Windows or add "
                    "its bin directory to PATH."
                )

            args = [
                bash,
                "--noprofile",
                "--norc",
                "-lc",
                command,
            ]
            shell = False
        else:
            args = command
            shell = True

        return subprocess.run(
            args,
            cwd=cwd,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _recursiveFindTestListFiles(self, test_dir: str, exclude_folders: list, verbose: bool = False):
        """Recurses through a directory to find *tests*.yaml files"""

        if not os.path.isdir(test_dir):
            raise Exception('"' + test_dir + '" directory does not exist')

        test_files: list[str] = []  # Map of directories to lua files
        for dir_path, sub_dirs, files in os.walk(test_dir):
            if all([exdir not in dir_path for exdir in exclude_folders]):
                for file_name in files:
                    base_name, extension = os.path.splitext(file_name)
                    if extension == ".yaml" and (base_name.find("tests") > 0):
                        test_files.append(dir_path + "/" + file_name)

        if verbose:
            print("Test files identified:")
            for test_file in test_files:
                print(f"  {test_file}")

        return test_files


    def _parseTestFiles(self, test_files: list[str]):
        """Parses each *tests*.yaml file and creates the tests."""
        for file_name in test_files:
            pretty_name = os.path.relpath(file_name, self.project_root_)
            print("Parsing " + pretty_name)
            with open(file_name) as yaml_file:
                try:
                    yaml_dict = yaml.load(yaml_file, Loader=loader)
                    self.my_dict = yaml_dict
                    # This if check might be redundant
                    if not yaml_dict:
                        print(f"\033[31mWARNING: Error parsing yaml input \"{file_name}\"\033[0m")
                        self.num_init_warnings_ += 1
                        continue
                except Exception as ex:
                    print(f"\033[31mWARNING: Error parsing yaml input \"{file_name}\"\033[0m")
                    self.num_init_warnings_ += 1
                    print(ex)
                    continue

            # ============================== Grab a separate dict of templates only
            templates: dict[dict] = {}
            executable = ""
            for test_name in yaml_dict:
                if test_name.find("TEMPLATE_") >= 0:
                    templates[test_name] = yaml_dict[test_name]
                    continue

            # ============================== Expand all templates and special keys
            executable = ""
            expanded_yaml_dict = {}
            for test_name in yaml_dict:
                temp_dict = yaml_dict[test_name]

                if test_name.find("TEMPLATE_") >= 0: continue
                if test_name == "__executable":
                    executable = self.project_root_ + "/" + yaml_dict[test_name]
                    continue

                # Init the test's parameters dictionary
                test_dict = {}

                # Set to a template if needed
                if "from_template" in temp_dict:
                    template_name = temp_dict["from_template"]
                    if not template_name in templates:
                        print(f"\033[31mWARNING: Error test \"{test_name}\": " +
                        f'template name "{template_name} not found."\033[0m')
                        self.num_init_warnings_ += 1
                        continue
                    else:
                        test_dict = templates[template_name].copy()

                # Copy/overwrite other original parameters
                for param_name in temp_dict:
                    if param_name == "from_template": continue

                    test_dict[param_name] = temp_dict[param_name]

                expanded_yaml_dict[test_name] = test_dict

            # ============================== Handle test copying
            new_yaml_dict = {}
            for test_name in expanded_yaml_dict:
                temp_dict = expanded_yaml_dict[test_name]

                new_yaml_dict[test_name] = temp_dict.copy()

                if 'copy_test' in temp_dict:
                    # if copy parameters are not exactly 3. Do not copy
                    if len(temp_dict['copy_test']) != 3: continue

                    copy_ext,copy_script,input_dir = temp_dict['copy_test']

                    #copy_dict[param] = temp_dict[param]
                    temp_dict['postrun_script'] += ' \n rm $TEST_NAME.i'

                    # check if weight class is allowed
                    if 'weight_class' not in temp_dict:
                            continue
                    else:
                        if temp_dict['weight_class'] not in self.weight_classes_allowed_:
                            continue
                    # run script to create copy test file
                    result = self._runScript(
                        [copy_script, input_dir + test_name + '.i']
                    )
                    # check if subprocess ran correctly
                    if result.returncode != 0:
                        print(f"\033[31mWARNING: Error running copy script \"{copy_script}\"\033[0m")
                        self.num_init_warnings_ += 1
                    # check if subprocess created new file
                    copy_name = f'{test_name+copy_ext}'
                    if str(result.stdout).find('was created') >=0 :
                        new_yaml_dict[copy_name] = temp_dict.copy()

            # make tests
            for test_name in new_yaml_dict:
                temp_dict = new_yaml_dict[test_name]

                if 'weight_class' not in temp_dict:
                    if self.default_weight_ != "":
                        temp_dict['weight_class'] = self.default_weight_
                    else:
                        temp_dict['weight_class'] = "none"

                if not isinstance(temp_dict, dict):
                    print(f"\033[31mWARNING: Error test \"{test_name}\" is not a dict\033[0m")
                    self.num_init_warnings_ += 1
                    continue

                dir_, name_ = os.path.split(file_name)
                test_true_name = f'{dir_}/{test_name}'
                test_dict = {}

                if "env_var_skip" in temp_dict:
                   env_var_skip_list = temp_dict["env_var_skip"]

                   if isinstance(env_var_skip_list, list):
                       num_items = len(env_var_skip_list)

                       if num_items%2 != 0:
                           print(f'\033[31mWARNING: Number of items in parameter "env_var_skip" ' +
                                 f'of test "{test_name}" is not a factor of 2.\033[0m')
                           self.num_init_warnings_ += 1

                       num_pairs = int(num_items/2)
                       for k in range(0, num_pairs):
                           env_var_name = env_var_skip_list[2*k]
                           env_var_value = env_var_skip_list[2*k+1]

                           envvar_defined = env_var_name in os.environ
                           envvar_isvalue = os.environ[env_var_name] == env_var_value
                           if not "skip" in test_dict:
                               test_dict["skip"] = ""
                           if envvar_defined and envvar_isvalue:
                               test_dict["skip"] += f'{env_var_name}=={env_var_value}'

                test_dict["type"] = "TFCTestObject"
                if not "project_root" in test_dict:
                    test_dict["project_root"] = self.project_root_ + "/"

                for param_name in temp_dict:
                    if param_name == "from_template": continue
                    if param_name == "env_var_skip": continue
                    test_dict[param_name] = temp_dict[param_name]

                if executable != "" and "executable" not in test_dict:
                    test_dict["executable"] = executable

                try:
                    test = PyFactory.makeObject(test_true_name, Parameter("", test_dict))
                    test.setTestSystemReference(self)
                    self.tests_.append(test)
                except Exception as ex:
                    print(f"\033[31mWARNING: Error creating test \"{test_name}\"\033[0m\n" +
                          ex.__str__())
                    self.num_init_warnings_ += 1


    def _getFilteredSelectedTests(self):
        selected_names = {
            name.replace("\\", "/")
            for name in self.selected_tests_
        }

        dependency_names: set[str] = set()
        queue = [
            test
            for test in self.tests_
            if test.name_ in selected_names
        ]

        while queue:
            test = queue.pop()
            for dependency in test.dependencies_:
                dep_name = dependency.getStringValue()
                if dep_name in ("", '""'):
                    continue

                dependency_names.add(dep_name)

                for candidate in self.tests_:
                    candidate_name = candidate.name_.rsplit("/", 1)[-1]
                    if candidate_name == dep_name:
                        queue.append(candidate)

        filtered_tests = []
        existing_names = {test.name_ for test in self.tests_}
        missing_tests = sorted(selected_names.difference(existing_names))

        for test in self.tests_:
            short_name = test.name_.rsplit("/", 1)[-1]
            if (
                test.name_ in selected_names
                or short_name in dependency_names
            ):
                filtered_tests.append(test)

        print("Selected test filter active:")
        print(f"  Requested tests          : {len(selected_names)}")
        print(f"  Including dependencies   : {len(dependency_names)}")
        print(f"  Tests scheduled to run   : {len(filtered_tests)}")

        if missing_tests:
            print("  Requested tests not found:")
            for name in missing_tests:
                print(f"    {name}")

        return filtered_tests


    def run(self):
        """Actually executes the test system"""

        start_time = time.perf_counter()

        job_state = {}
        capacity = self.num_jobs_
        system_load = 0
        active_tests: list[TFCTestObject] = []

        # ======================================= Testing phase
        print("\nRunning tests with weight class:" + self.weight_classes_allowed_.__str__() + "")
        k = 0
        while True:
            k += 1

            done = True  # Assume we are done
            for test in self.tests_:
                if test.ran_:
                    continue
                if test.weight_class_ not in self.weight_classes_allowed_:
                    continue
                done = False

                if not test.submitted_ and test.checkDependenciesMet(self.tests_):
                    if test.num_procs_ <= (capacity - system_load):
                        system_load += test.num_procs_

                        test.submit(self)

                        active_tests.append(test)

            # Check test progression
            system_load = 0
            for test in active_tests:
                try:
                    if test.checkProgress(self) == "Running":
                        system_load += test.num_procs_
                except Exception as ex:
                    print(f"\033[31mERROR: Test {test.name_}"
                          " had a Python failure\033[0m\n" + ex.__str__())
                    raise ex

            time.sleep(0.01)

            if done:
                break # from while-loop

        # ======================================= Post-test phase
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time

        print("Done executing tests with weight class: " +
          self.weight_classes_allowed_.__str__())

        num_tests_failed = 0
        num_tests_skipped = 0
        for test in active_tests:
            if not test.passed_:
                num_tests_failed += 1
            if test.skip_:
                num_tests_skipped += 1

        print()
        print("Elapsed time                       : {:.2f} seconds".format(elapsed_time))
        print(f"Number of initialization warnings : {self.num_init_warnings_}")
        print(f"Number of tests run               : {len(active_tests)}")
        print(f"Number of tests skipped           : {num_tests_skipped}")
        if num_tests_failed == 0:
            print(f"Number of failed tests            : {num_tests_failed}")
        else:
            print(f"\033[31mNumber of failed tests            : {num_tests_failed}\033[0m")

        if self.generate_requirements_matrix_:
            self.writeRequirementsTraceabilityMatrix()
        if self.generate_results_database_:
            self.writeResultsDatabase()
        if self.rebuild_rtm_file_:
            self.writeRequirementsTraceabilityMatrixFromResults(
                self.rebuild_rtm_file_)

        # Printing failure logs
        failure_reasons: list[str] = []
        for test in active_tests:

            if test.passed_:
                continue

            reason = f'{test.name_}:\n' + f'{test.fail_flag_reason_}'

            for check_id, check in enumerate(test.checks_):
                if check.failed_:
                    raw_reason = f'Check {check_id:2d} type={check.type_} {check.fail_reason_}'
                    reason += raw_reason.rstrip() + os.linesep

            failure_reasons.append(reason)

        if len(failure_reasons) > 0:
            print("\n\033[31mFailure reasons:")
            for reason in failure_reasons:
                print(reason)
            print("\033[0m")

        for test in active_tests:
            if test.debug_ == True:
                if test.passed_:
                    continue
                dir_, testname_ = os.path.split(test.name_)
                print("Output of failed test: testname_")
                filename = dir_ + f"/out/{testname_}.cout"
                print(filename)
                file = open(filename,"r")
                contents = file.read()
                print(contents)
                file.close()

        if num_tests_failed > 0:
            return 1
        return 0


PyFactory.register(TFCTestSystem, "TFCTestSystem")
