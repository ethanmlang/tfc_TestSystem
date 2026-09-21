#!/usr/bin/env python3

import sys
import os
import argparse
import shutil

import pathlib
script_path = str(pathlib.Path(__file__).parent.resolve()) + "/"
cwd = os.getcwd()

src_dir = script_path + "../"# <--- CHANGE THIS
sys.path.append(src_dir)
sys.path.append(src_dir + "../")


from tfc_PyFactory import *
from TFCTestSystem import *

# from tfc_PyFactory import *
# from tfc_TestSystem import *
# import tfc_TestSystem

# ---------------------------------------------------------
#                    Link custom modules here
# sys.path.append(file_path + "../path_to_so")
# from Custom_module import *
# ---------------------------------------------------------

# ========================================================= Process commandline
#                                                           arguments
arguments_help = "Runs a test file"

parser = argparse.ArgumentParser(
    description="A script to run a test file.",
    epilog=arguments_help
)

parser.add_argument(
    "-d", "--directory", default=None, type=str, required=True,
    help="The test directory to inspect recursively"
)
parser.add_argument(
    "-j", "--num_jobs", default=4, type=int, required=False,
    help="The number of job slots available to run tests"
)
parser.add_argument(
    "-w",
    "--weights",
    default=["all"],
    nargs="+",
    type=str,
    required=False,
    help=(
        "Weight classes to execute. Pass one or more configured class "
        "names, or 'all'."
    ),
)
parser.add_argument(
    "-c", "--config_file", default="TestSystemCONFIG.yaml", type=str,
    required=False,
    help="The name of the default config file"
)
parser.add_argument(
    "-x", "--exclude_folders", default=[], nargs="+",
    required=False,
    help="List of directories to exclude when looking for test files"
)

argv = parser.parse_args()  # argv = argument values

params: dict = {}
params["type"] = "TFCTestSystem"
params["directory"] = argv.directory
params["num_jobs"] = argv.num_jobs
params["weights"] = argv.weights
params["config_file"] = argv.config_file
if argv.exclude_folders != ['']:
    params["exclude_folders"] = argv.exclude_folders
params["requirement_docs"] = ["example_requirements/requirements.md",
                              "example_requirements/requirements1.md"]

# ========================================================= Process Environment
#                                                           Variables
if not "COMPILER" in os.environ:
    print()
    print("ERROR:The environment variable ""COMPILER"" is not set.")
    print("Execute 'export COMPILER=linuxgf' or 'export COMPILER='linuxntl' or")
    print("'export COMPILER=linuxgf_macos'.")
    print()

    exit(1)
else:
    compiler = os.environ["COMPILER"]
    print(f'COMPILER set to {compiler}')

    os.environ["TEST_EXE"] = 'python'
    print(f'TEST_EXE set to {os.environ["TEST_EXE"]}')

#  Check if the executable exists
if shutil.which(os.environ["TEST_EXE"]) is None:
    print()
    print(f"ERROR:A TEST_EXE executable does not exist at {os.environ["TEST_EXE"]}.")
    print("This could indicate that compilation failed or did not occur.")
    print("Please attempt to compile the code or contact the development team.")
    print()

    exit(1)

# ========================================================= Run the test system
test_system = PyFactory.makeObject("TFCTestSystem", Parameter("", params))
error_code = test_system.run()

exit(error_code)
