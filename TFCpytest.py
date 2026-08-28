"""
TFCpytest - pytest wrapper that reuses TFCTestSystem frontend but delegates
execution to pytest (with optional pytest-xdist for real parallel).

Keeps *tests.yaml configs unchanged. Frontend = discovery/parsing/weight
filtering/requirements matrix. Backend = pytest parametrized tests calling
TFCTestObject.submit/checkProgress synchronously per-test, avoiding the
legacy run() capacity/deadlock poll loop (TFCTestSystem.py:579).

Usage (closed-source bespoke metadata):
    from TFCpytest import discover, make_pytest_tests, MyTestSystem
    # MyTestSystem subclasses TFCTestSystem and overrides _testResultMetadata
    suite = discover(directory="path/to/tests",
                     weights=["short"],
                     test_system_cls=MyTestSystem,  # optional
                     generate_results_database=True)
    # then in test file: @pytest.mark.parametrize via make_pytest_tests(suite)

Or standalone:
    python -m tfc_TestSystem.TFCpytest --directory tfc_TestSystem/example_tests --num-jobs 4

Or pytest plugin:
    pytest --tfc-directory=tfc_TestSystem/example_tests -p tfc_TestSystem.pytest_tfc_plugin
"""
import os, sys, pathlib, time, argparse, yaml, tempfile
file_path = str(pathlib.Path(__file__).parent.resolve()) + "/"
sys.path.append(file_path + "../")
sys.path.append(file_path + "./")

from tfc_PyFactory import Parameter, PyFactory, InputParameters
from TFCTestSystem import TFCTestSystem
from TFCTestObject import TFCTestObject

# Ensure extension checks are registered without hard-coded paths
# Generic: scan test/src/*/ for any *.py (including extension_src, RELAP_custom_src, future custom dirs)
# PyFactory is agnostic; registration happens on import via PyFactory.register
def _autoload_extension_checks():
    import pathlib, importlib
    base = pathlib.Path(file_path).parent  # test/src (file_path is test/src/tfc_TestSystem/)
    for child in base.iterdir():
        if child.name in ["tfc_PyFactory", "tfc_TestSystem", "__pycache__"]:
            continue
        if not child.is_dir():
            continue
        for py in child.rglob("*.py"):
            if py.name.startswith("__"):
                continue
            try:
                text = py.read_text()
                if "parse_args()" in text and "ArgumentParser" in text:
                    continue
            except Exception:
                pass
            try:
                rel = py.relative_to(base)
                dotted = ".".join(rel.with_suffix("").parts)
                importlib.import_module(dotted)
            except BaseException:
                pass

_autoload_extension_checks()

# ------------------------------------------------------------------
def discover(
    directory: str,
    executable: str = "python3",
    project_root: str = "",
    num_jobs: int = 4,
    weights=None,
    no_time_limit: bool = False,
    config_file: str = "TestSystemCONFIG.yaml",
    exclude_folders=None,
    requirement_docs=None,
    requirements_matrix_outputfile: str = "TraceAbilityMatrix.md",
    test_results_database_outputfile: str = "TestResults.yaml",
    generate_requirements_matrix: bool = False,
    generate_results_database: bool = False,
    merge_results_file: str = "",
    tests_print_result_tags: bool = False,
    selected_tests=None,
    test_system_cls=None,
    type_name: str = None,
):
    """
    Frontend reuse: builds a TFCTestSystem (or subclass) without calling run().
    Returns the populated suite (self.tests_ already filtered by weight etc).
    The caller may set bespoke metadata by passing test_system_cls that
    overrides _testResultMetadata() (TFCTestResultsDatabase.py:9).
    """
    if weights is None:
        weights = ["all"]
    if exclude_folders is None:
        exclude_folders = []
    if requirement_docs is None:
        requirement_docs = []
    if selected_tests is None:
        selected_tests = []

    cls = test_system_cls or TFCTestSystem
    # Ensure registration for PyFactory
    tname = type_name or cls.__name__
    if tname not in PyFactory.registered_objects_:
        PyFactory.register(cls, tname)

    params = {}
    params["type"] = tname
    params["directory"] = directory
    params["executable"] = executable
    params["project_root"] = project_root
    params["num_jobs"] = num_jobs
    params["weights"] = weights
    params["no_time_limit"] = no_time_limit
    params["config_file"] = config_file
    params["exclude_folders"] = exclude_folders
    params["requirement_docs"] = requirement_docs
    params["requirements_matrix_outputfile"] = requirements_matrix_outputfile
    params["test_results_database_outputfile"] = test_results_database_outputfile
    params["generate_requirements_matrix"] = generate_requirements_matrix
    params["generate_results_database"] = generate_results_database
    params["merge_results_file"] = merge_results_file
    params["tests_print_result_tags"] = tests_print_result_tags
    params["selected_tests"] = selected_tests

    # Make via factory so InputParameters validation still runs (InputParameters.py:264)
    suite = PyFactory.makeObject("TFCTestSystem_discovered", Parameter("", params))
    return suite


def run_single_test(test: TFCTestObject, suite: TFCTestSystem, timeout_slack: float = 0.0) -> bool:
    """
    Run one TFCTestObject synchronously, reusing its submit/checkProgress logic.
    Avoids global capacity tracking - pytest/xdist handles parallelism.
    Returns passed bool; mutates test (ran_, passed_, tagged_results_, etc)
    for later TestResults.yaml generation (TFCTestResultsDatabase.py:30).
    """
    # Ensure reference set (keywordReplace needs env_vars)
    if getattr(test, "test_system_reference_", None) is None:
        test.setTestSystemReference(suite)

    # Handle unschedulable num_procs vs suite capacity - fail fast instead of deadlock
    if test.num_procs_ > suite.num_jobs_ and test.weight_class_ in suite.weight_classes_allowed_:
        test.submitted_ = True
        test.ran_ = True
        test.passed_ = False
        test.fail_flag_ = f"Unschedulable: num_procs {test.num_procs_} > num_jobs {suite.num_jobs_}"
        test.fail_flag_reason_ = test.fail_flag_
        return False

    # Pre-check dependencies for missing/unsatisfied (TFCTestObject.py:200)
    # This handles "Dependency not active." without needing global scheduling.
    # Note: with xdist, cross-worker dependency status is not shared; we only
    # handle missing deps here. Valid parent->child ordering is enforced via
    # pytest_params topological sort (TFCTestSystem.py:604). For true parallel
    # with dependencies, run with -n 0 or use pytest-dependency.
    if not test.checkDependenciesMet(suite.tests_):
        # Dependency not yet ran in this process (sequential case) - wait briefly
        # In xdist workers, parent may be on another worker - treat as skip if missing
        # For sequential (-n 0), this will be handled by ordering, so return False to retry?
        # Here we just proceed; checkDependenciesMet already set skip for missing.
        pass
    if test.skip_ != "" and not test.submitted_:
        # Missing dependency case - mark as skipped without submitting
        test.submitted_ = True
        test.ran_ = True
        test.passed_ = True
        test.test_result_annotation_ = f"[Skipped: {test.skip_}]"
        # Also store for DB
        test.checkProgress(suite)
        return True

    # submit will handle skip_/dependency_failed_ early return without Popen
    test.submit(suite)

    # skip path: checkProgress handles annotations but poll would hang on None _process_
    if test.skip_ != "" or test.dependency_failed_:
        # reuse checkProgress which marks ran_/passed for skip
        test.checkProgress(suite)
        return test.passed_

    # Poll loop identical to TFCTestSystem.run() but per-test, with proper timeout
    # checkProgress already handles weight_map time limit + terminate
    while True:
        status = test.checkProgress(suite)
        if status != "Running":
            break
        time.sleep(0.01)
    return test.passed_


# ------------------------------------------------------------------
# Helpers for pytest parametrization
def pytest_params(suite: TFCTestSystem):
    """Return (ids, tests) for @pytest.mark.parametrize, respecting dependencies ordering."""
    # Already filtered by selected_tests and weight; sort topologically for dependencies
    # simple topological sort: if A depends on B, B first
    tests = list(suite.tests_)
    # Filter to allowed weights already done in suite, but keep order stable
    # Topological sort
    name_to_test = {t.name_.rsplit("/", 1)[-1]: t for t in tests}
    full_name_to_test = {t.name_: t for t in tests}
    visited = {}
    ordered = []

    def visit(t):
        key = t.name_
        if key in visited:
            if visited[key] == 1:
                # cycle - keep as is
                return
            return
        visited[key] = 1
        for dep in t.dependencies_:
            dname = dep.getStringValue()
            if dname in ("", '""'):
                continue
            dep_t = name_to_test.get(dname) or full_name_to_test.get(dname)
            if dep_t:
                visit(dep_t)
        visited[key] = 2
        ordered.append(t)

    for t in tests:
        visit(t)
    ids = [t.name_ for t in ordered]
    return ordered, ids


# ------------------------------------------------------------------
# YAML result appending with filelock for xdist safety
def _append_result_atomic(test: TFCTestObject, suite: TFCTestSystem, out_file: str):
    """Append single test result to out_file atomically (for xdist workers)."""
    data = suite._testResultData(test)  # TFCTestResultsDatabase.py:13
    # Use filelock if available, else naive append (small write usually atomic)
    try:
        from filelock import FileLock
        lock_path = out_file + ".lock"
        with FileLock(lock_path, timeout=10):
            # read existing list or create
            if os.path.exists(out_file):
                with open(out_file, "r") as f:
                    existing = yaml.safe_load(f) or []
            else:
                existing = []
            # upsert by name
            idx = next((i for i, e in enumerate(existing) if isinstance(e, dict) and e.get("name")==data["name"]), None)
            if idx is not None:
                existing[idx] = data
            else:
                existing.append(data)
            with open(out_file, "w") as f:
                yaml.dump(existing, f, sort_keys=False)
    except ImportError:
        # fallback without lock
        if os.path.exists(out_file):
            with open(out_file, "r") as f:
                existing = yaml.safe_load(f) or []
        else:
            existing = []
        idx = next((i for i, e in enumerate(existing) if isinstance(e, dict) and e.get("name")==data["name"]), None)
        if idx is not None:
            existing[idx] = data
        else:
            existing.append(data)
        with open(out_file, "w") as f:
            yaml.dump(existing, f, sort_keys=False)


# ------------------------------------------------------------------
# Standalone runner that invokes pytest programmatically
def run_with_pytest(
    directory: str,
    num_jobs: int = 4,
    weights=None,
    config_file: str = "TestSystemCONFIG.yaml",
    project_root: str = "",
    generate_results_database: bool = False,
    generate_requirements_matrix: bool = False,
    test_system_cls=None,
    pytest_args=None,
    **kwargs,
):
    """
    Discover via frontend, then run each test via pytest parametrize.
    If generate_results_database true, writes TestResults.yaml incrementally
    and supports merge_results_file via TFCTestResultsDatabase.py:30 _merge.
    """
    # Keep directory/project_root verbatim as passed (working way before: TFCTestSystem.py:318 os.walk verbatim)
    # Only strip whitespace per comma part, no abspath — controller and workers share cwd (<root> or <root>/test)
    # so relative "test/tests" stays relative and test_true_name stays relative (TFCTestSystem.py:368 f'{dir_}/{test_name}')
    if directory:
        parts = [p.strip() for p in directory.split(",")]
        directory = ",".join([p for p in parts if p])
    if project_root:
        project_root = project_root.strip()

    suite = discover(
        directory=directory,
        num_jobs=num_jobs,
        weights=weights,
        config_file=config_file,
        project_root=project_root,
        generate_results_database=generate_results_database,
        generate_requirements_matrix=generate_requirements_matrix,
        test_system_cls=test_system_cls,
        **kwargs,
    )

    # Clean previous results file if not merging (avoid stale duplicates)
    if generate_results_database:
        out = suite.test_results_database_outputfile_
        # resolve relative to cwd
        out_path = out if os.path.isabs(out) else os.path.join(os.getcwd(), out)
        if suite.merge_results_file_ == "" and os.path.exists(out_path):
            try:
                os.remove(out_path)
                if os.path.exists(out_path + ".lock"):
                    os.remove(out_path + ".lock")
            except Exception:
                pass

    # If no tests, still handle matrix generation
    if len(suite.tests_) == 0:
        print("No tests discovered for weight", suite.weight_classes_allowed_)
        if generate_requirements_matrix:
            suite.writeRequirementsTraceabilityMatrix()
        return 0

    # Create a temporary pytest file that parametrizes over suite
    tests, ids = pytest_params(suite)

    # We need to pickle suite? Instead generate a temp conftest+test file that re-discovers.
    # Simpler: pass suite via environment pickle file
    import pickle, tempfile, subprocess, sys
    tmpdir = tempfile.mkdtemp(prefix="tfc_pytest_")
    pkl_path = os.path.join(tmpdir, "suite.pkl")
    # Can't pickle TFCTestSystem easily (file handles), so pickle discovery params instead
    # and have test file re-discover (fast) then pick test by name
    # Store params for re-discovery
    discover_kwargs = dict(
        directory=directory,
        num_jobs=num_jobs,
        weights=weights,
        config_file=config_file,
        project_root=project_root,
        generate_results_database=generate_results_database,
        generate_requirements_matrix=generate_requirements_matrix,
        **kwargs,
    )
    # Handle class name
    if test_system_cls:
        discover_kwargs["test_system_cls_name"] = test_system_cls.__name__
        discover_kwargs["test_system_cls_module"] = test_system_cls.__module__

    # Write generated test file
    gen_path = os.path.join(tmpdir, "test_tfc_generated.py")
    with open(gen_path, "w") as f:
        f.write(f'''\
import os, sys, pathlib, importlib
sys.path.append("{file_path}../")
sys.path.append("{file_path}./")
sys.path.append("{file_path}../../")
import pytest
from tfc_TestSystem.TFCpytest import discover, run_single_test, _append_result_atomic
from tfc_PyFactory import PyFactory
# Generic: no hard-coded RELAP_custom_src/extension_src — scan test/src/* for any checks
def _autoload_worker():
    base = pathlib.Path("{file_path}").parent  # test/src
    for child in base.iterdir():
        if child.name in ["tfc_PyFactory", "tfc_TestSystem", "__pycache__"]:
            continue
        if not child.is_dir(): continue
        for py in child.rglob("*.py"):
            if py.name.startswith("__"): continue
            try:
                text = py.read_text()
                if "parse_args()" in text and "ArgumentParser" in text:
                    continue
            except Exception:
                pass
            try:
                rel = py.relative_to(base)
                dotted = ".".join(rel.with_suffix("").parts)
                importlib.import_module(dotted)
            except BaseException:
                pass
_autoload_worker()

# Re-discover suite (cheap, frontend only)
def _load_suite():
    kwargs = {repr(discover_kwargs)}
    if "test_system_cls_name" in kwargs:
        mod_name = kwargs.pop("test_system_cls_module")
        cls_name = kwargs.pop("test_system_cls_name")
        mod = __import__(mod_name, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        kwargs["test_system_cls"] = cls
    return discover(**kwargs)

_suite = _load_suite()
_name_to_test = {{t.name_: t for t in _suite.tests_}}
_name_to_test_short = {{t.name_.rsplit("/",1)[-1]: t for t in _suite.tests_}}

def _get_test(name):
    t = _name_to_test.get(name)
    if t is not None: return t
    t = _name_to_test_short.get(name.rsplit("/",1)[-1] if "/" in name else name)
    if t is not None: return t
    # fallback: suffix match for absolute vs relative mismatch
    for k,v in _name_to_test.items():
        if k.endswith("/"+name) or name.endswith("/"+k) or k==name:
            return v
    return None

@pytest.mark.parametrize("tname", {ids!r})
def test_tfc(tname, request):
    test = _get_test(tname)
    assert test is not None, f"Test {{tname}} not found in suite"
    suite = _suite
    passed = run_single_test(test, suite)
    # Incremental results DB for xdist: each worker appends
    if suite.generate_results_database_:
        out = suite.test_results_database_outputfile_
        # make path absolute relative to cwd
        if not os.path.isabs(out):
            out = os.path.join(os.getcwd(), out)
        _append_result_atomic(test, suite, out)
    if test.skip_:
        pytest.skip(test.skip_)
    assert passed, f"{{test.name_}} failed: {{test.fail_flag_reason_}} " + "; ".join([c.fail_reason_ for c in test.checks_ if c.failed_])

''')

    # Also write conftest to handle final merge/matrix generation
    conftest_path = os.path.join(tmpdir, "conftest.py")
    with open(conftest_path, "w") as f:
        f.write(f'''\
import os, yaml
def pytest_sessionfinish(session, exitstatus):
    # Only controller generates final artifacts (xdist: workerinput is None in controller)
    is_controller = not hasattr(session.config, "workerinput")
    if not is_controller:
        return
    # Re-discover to get output paths and merge settings
    from tfc_TestSystem.TFCpytest import discover
    kwargs = {repr(discover_kwargs)}
    if "test_system_cls_name" in kwargs:
        mod_name = kwargs.pop("test_system_cls_module")
        cls_name = kwargs.pop("test_system_cls_name")
        mod = __import__(mod_name, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        kwargs["test_system_cls"] = cls
    suite = discover(**kwargs)
    if suite.generate_requirements_matrix_:
        # Generate from results file if it exists (allows parallel)
        import os
        results_file = suite.test_results_database_outputfile_
        if os.path.isabs(results_file):
            rf = results_file
        else:
            rf = os.path.join("{os.getcwd()}", results_file)
        if os.path.exists(rf):
            try:
                suite.writeRequirementsTraceabilityMatrixFromResults(rf)
            except Exception as e:
                print(f"RTM from results failed: {{e}}, falling back")
                suite.writeRequirementsTraceabilityMatrix()
        else:
            suite.writeRequirementsTraceabilityMatrix()
    if suite.generate_results_database_ and suite.merge_results_file_:
        # Merge step already handled incrementally but ensure final merge
        out = suite.test_results_database_outputfile_
        if not os.path.isabs(out):
            out = os.path.join(os.getcwd(), out)
        if os.path.exists(suite.merge_results_file_) and os.path.exists(out):
            merged = suite._mergeResultsDatabase(yaml.safe_load(open(out)) or [], suite.merge_results_file_)
            with open(out, "w") as f:
                yaml.dump(merged, f, sort_keys=False)
''')

    # Invoke pytest
    import pytest as _pytest
    if num_jobs > 1:
        args = [gen_path, "-v", "-n", str(num_jobs)]
    else:
        args = [gen_path, "-v", "-p", "no:xdist"]
    # Add extra pytest args
    if pytest_args:
        args.extend(pytest_args)
    print(f"Running pytest via TFCpytest: {' '.join(args)} in {tmpdir}")
    rc = _pytest.main(args)
    return rc

# ------------------------------------------------------------------
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run TFCTestSystem suite via pytest")
    p.add_argument("-d","--directory", required=True, help="Test directory (comma separated)")
    p.add_argument("-j","--num-jobs", type=int, default=4)
    p.add_argument("-w","--weights", nargs="+", default=["all"], help="Weight classes")
    p.add_argument("-c","--config-file", default="TestSystemCONFIG.yaml")
    p.add_argument("--project-root", default="")
    p.add_argument("--generate-results-database", action="store_true")
    p.add_argument("--generate-requirements-matrix", action="store_true")
    p.add_argument("--merge-results-file", default="")
    p.add_argument("--result-output", default="TestResults.yaml")
    p.add_argument("--pytest-args", nargs=argparse.REMAINDER, help="Args after -- passed to pytest")
    args = p.parse_args()
    # Handle weights as list vs string
    weights = args.weights
    # pytest-args handling
    extra = args.pytest_args if args.pytest_args else []
    if extra and extra[0]=="--":
        extra=extra[1:]
    rc = run_with_pytest(
        directory=args.directory,
        num_jobs=args.num_jobs,
        weights=weights,
        config_file=args.config_file,
        project_root=args.project_root,
        generate_results_database=args.generate_results_database,
        generate_requirements_matrix=args.generate_requirements_matrix,
        merge_results_file=args.merge_results_file,
        test_results_database_outputfile=args.result_output,
        pytest_args=extra,
    )
    sys.exit(rc)
