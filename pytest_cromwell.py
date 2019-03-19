#! /usr/bin/env python
"""
Fixtures for writing tests that execute WDL workflows using Cromwell.
"""
import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import urllib.request

import delegator
import pytest


LOG = logging.getLogger("pytest-cromwell")
LOG.setLevel(os.environ.get("LOGLEVEL", "WARNING").upper())


def _deprecated(f):
    """
    Decorator for deprecated functions/methods. Deprecated functionality will be
    removed before each major release.
    """
    def decorator(*args, **kwargs):
        LOG.warning(f"Function/method {f.__name__} is deprecated and will be removed")
        f(*args, **kwargs)
    return decorator


class DataFile:
    """
    A data file, which may be located locally, remotely, or represented as a string.

    Args:
        path: Path to a local file. If this file doesn't exist, it will be created by
            either downloading the given URL or persisting the given file contents.
        url: A URL of a remote file.
        contents: The contents of the file.
        local_dir: Directory where a file should be created, if `path` is not provided.
        http_headers: Headers to add to the requrests to download the remote file from
            Artifactory.
        proxies: Proxies to add to the requests to download the remote file from
            Artifactory.
    """
    def __init__(
        self, path=None, url=None, contents=None, local_dir=".", http_headers=None,
        proxies=None, allowed_diff_lines=0
    ):
        self.url = url
        self.contents = contents
        self.http_headers = http_headers
        self.proxies = proxies
        self.allowed_diff_lines = allowed_diff_lines
        if path:
            if not os.path.isabs(path):
                path = os.path.abspath(os.path.join(local_dir, path))
            self._path = path
        elif url:
            filename = url.rsplit("/", 1)[1]
            self._path = os.path.abspath(os.path.join(local_dir, filename))
        else:
            self._path = tempfile.mkstemp(dir=local_dir)

    @property
    def path(self):
        if not os.path.exists(self._path):
            self._persist()
        return self._path

    def __str__(self):
        return self.path

    def assert_contents_equal(self, other):
        """
        Assert the contents of two files are equal.

        If `allowed_diff_lines == 0`, files are compared using MD5 hashes, otherwise
        their contents are compared using the linux `diff` command.

        Args:
            other: A `DataFile` or string file path.
        """
        allowed_diff_lines = self.allowed_diff_lines

        if isinstance(other, str):
            other_path = other
        else:
            other_path = other.path
            allowed_diff_lines = max(allowed_diff_lines, other.allowed_diff_lines)

        self._assert_contents_equal(self.path, other_path, allowed_diff_lines)

    @classmethod
    def _assert_contents_equal(cls, file1, file2, allowed_diff_lines):
        if allowed_diff_lines:
            cls._diff_contents(file1, file2, allowed_diff_lines)
        else:
            cls._compare_hashes(file1, file2)

    @classmethod
    def _diff_contents(cls, file1, file2, allowed_diff_lines):
        if file1.endswith(".gz"):
            with tempdir() as temp:
                temp_file1 = os.path.join(temp, "file1")
                temp_file2 = os.path.join(temp, "file2")
                delegator.run(f"gunzip -c {file1} > {temp_file1}", block=True)
                delegator.run(f"gunzip -c {file2} > {temp_file2}", block=True)
                diff_lines = cls._diff(temp_file1, temp_file2)
        else:
            diff_lines = cls._diff(file1, file2)

        if diff_lines > allowed_diff_lines:
            raise AssertionError(
                f"{diff_lines} lines (which is > {allowed_diff_lines} allowed) are "
                f"different between files {file1}, {file2}"
            )

    @classmethod
    def _diff(cls, file1, file2):
        cmd = "diff -y --suppress-common-lines {} {} | grep '^' | wc -l".format(
            file1, file2
        )
        return int(delegator.run(cmd, block=True).out)

    @classmethod
    def _compare_hashes(cls, file1, file2):
        with open(file1, "rb") as inp1:
            file1_md5 = hashlib.md5(inp1.read()).hexdigest()
        with open(file2, "rb") as inp2:
            file2_md5 = hashlib.md5(inp2.read()).hexdigest()
        if file1_md5 != file2_md5:
            raise AssertionError(
                f"MD5 hashes differ between expected identical files "
                f"{file1}, {file2}"
            )

    def _persist(self):
        if not (self.url or self.contents):
            raise ValueError(
                f"File {self._path} does not exist. Either a url, file contents, "
                f"or a local file must be provided."
            )

        parent = os.path.dirname(self._path)
        if not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        if self.url:
            LOG.debug(f"Persisting {self._path} from url {self.url}")
            req = urllib.request.Request(self.url)
            if self.http_headers:
                for name, value in self.http_headers.items():
                    req.add_header(name, value)
            if self.proxies:
                for proxy_type, url in self.proxies.items():
                    req.set_proxy(url, proxy_type)
            rsp = urllib.request.urlopen(req)
            with open(self._path, "wb") as out:
                shutil.copyfileobj(rsp, out)
        elif self.contents:
            LOG.debug(f"Persisting {self._path} from contents")
            with open(self._path, "wt") as out:
                out.write(self.contents)


class VcfDataFile(DataFile):
    @classmethod
    def _assert_contents_equal(cls, file1, file2, allowed_diff_lines):
        cls._diff_contents(file1, file2, allowed_diff_lines)

    @classmethod
    def _diff(cls, file1, file2):
        """
        Special handling for VCF files to only compare the first 5 columns.

        Args:
            file1:
            file2:
        """
        with tempdir() as temp:
            cmp_file1 = os.path.join(temp, "file1")
            cmp_file2 = os.path.join(temp, "file2")
            job1 = delegator.run(
                f"cat {file1} | grep -vP '^#' | cut -d$'\t' -f 1-5 > {cmp_file1}"
            )
            job2 = delegator.run(
                f"cat {file2} | grep -vP '^#' | cut -d$'\t' -f 1-5 > {cmp_file2}"
            )
            for job in (job1, job2):
                job.block()
            return super()._diff(cmp_file1, cmp_file2)


DATA_TYPES = {
    "vcf": VcfDataFile,
    "default": DataFile
}


class Data:
    """
    Class that manages test data.

    Args:
        data_dir: Directory where test data files should be stored temporarily, if
            they are being downloaded from a remote server.
        data_file: JSON file describing the test data.
        http_headers: Http(s) headers.
        proxies: Http(s) proxies.
    """
    def __init__(self, data_dir, data_file, http_headers, proxies):
        self.data_dir = data_dir
        self.http_headers = http_headers
        self.proxies = proxies
        self._values = {}
        with open(data_file, "rt") as inp:
            self._data = json.load(inp)

    def __getitem__(self, name):
        if name not in self._values:
            if name not in self._data:
                raise ValueError(f"Unrecognized name {name}")
            value = self._data[name]
            if isinstance(value, dict):
                data_file_class = DATA_TYPES[value.pop("type", "default")]
                self._values[name] = data_file_class(
                    local_dir=self.data_dir, http_headers=self.http_headers,
                    proxies=self.proxies, **value
                )
            else:
                self._values[name] = value

        return self._values[name]


class CromwellHarness:
    """
    Class that manages the running of WDL workflows using Cromwell.

    Args:
        project_root: The root path to which non-absolute WDL script paths are
            relative.
        import_paths_file: File that contains relative or absolute paths to
            directories containing WDL scripts that should be available as
            imports (one per line).

    Env:
        JAVA_HOME: path containing the bin dir that contains the java executable.
        CROMWELL_JAR: path to the cromwell JAR file.
    """
    def __init__(
        self, project_root, import_paths_file=None, java_bin="/usr/bin/java",
        cromwell_jar_file="cromwell.jar"
    ):
        self.java_bin = java_bin
        self.cromwell_jar = cromwell_jar_file
        self.project_root = os.path.abspath(project_root)
        self.import_paths = None
        if import_paths_file:
            with open(import_paths_file, "rt") as inp:
                self.import_paths = [
                    self._get_path(import_path)
                    for import_path in inp.read().splitlines(keepends=False)
                ]

    @_deprecated
    def __call__(self, *args, **kwargs):
        """
        Briefly used as a replacement for run_workflow.
        """
        self.run_workflow(*args, **kwargs)

    @_deprecated
    def run_workflow_in_tempdir(self, *args, **kwargs):
        """
        Conveience method for running a workflow with a temporary execution directory.
        """
        with tempdir() as tmpdir:
            self(*args, **kwargs, execution_dir=tmpdir)

    def run_workflow(
        self, wdl_script, workflow_name, inputs, expected, **kwargs
    ):
        """
        Run a WDL workflow on given inputs, and check that the output matches
        given expected values.

        Args:
            wdl_script: The WDL script to execute.
            workflow_name: The name of the workflow in the WDL script.
            inputs: Object that will be serialized to JSON and provided to Cromwell
                as the workflow inputs.
            expected: Dict mapping output parameter names to expected values.
            kwargs: Additional keyword arguments, mostly for debugging:
                * execution_dir: DEPRECATED
                * inputs_file: Path to the Cromwell inputs file to use. Inputs are
                    written to this file only if it doesn't exist.
                * imports_file: Path to the WDL imports file to use. Imports are
                    written to this file only if it doesn't exist.
                * java_args: Additional arguments to pass to Java runtime.
                * cromwell_args: Additional arguments to pass to `cromwell run`.
        """
        if "execution_dir" in kwargs:
            LOG.warning(
                f"Parameter execution_dir is deprecated and will be removed. Use "
                f"the `chdir` context manager instead."
            )
            os.chdir(kwargs["execution_dir"])

        write_inputs = True
        if "inputs_file" in kwargs:
            inputs_file = os.path.abspath(kwargs["inputs_file"])
            if os.path.exists(inputs_file):
                write_inputs = False
            else:
                os.makedirs(os.path.dirname(inputs_file), exist_ok=True)
        else:
            inputs_file = tempfile.mkstemp(suffix=".json")[1]

        write_imports = True
        if "imports_file" in kwargs:
            imports_file = os.path.abspath(kwargs["imports_file"])
            if os.path.exists(imports_file):
                write_imports = False
            else:
                os.makedirs(os.path.dirname(imports_file), exist_ok=True)
        else:
            imports_file = tempfile.mkstemp(suffix=".zip")[1]

        java_args = kwargs.get("java_args", "")
        cromwell_args = kwargs.get("cromwell_args", "")
        wdl_path = self._get_path(wdl_script)

        if write_inputs:
            cromwell_inputs = dict(
                (
                    f"{workflow_name}.{key}",
                    value.path if isinstance(value, DataFile) else value
                )
                for key, value in inputs.items()
            )
            with open(inputs_file, "wt") as out:
                json.dump(cromwell_inputs, out, default=str)
        else:
            with open(inputs_file, "rt") as inp:
                cromwell_inputs = json.load(inp)

        imports_zip_arg = ""
        if write_imports and self.import_paths:
            imports = " ".join(
                os.path.join(self.project_root, path, "*.wdl")
                for path in self.import_paths
            )
            LOG.info(f"Writing imports {imports} to zip file {imports_file}")
            exe = delegator.run(f"zip -j - {imports} > {imports_file}", block=True)
            if not exe.ok:
                raise Exception(
                    f"Error creating imports zip file; stdout={exe.out}; "
                    f"stderr={exe.err}"
                )
            imports_zip_arg = f"-p {imports_file}"

        cmd = (
            f"{self.java_bin} -Ddocker.hash-lookup.enabled=false -jar {java_args} "
            f"{self.cromwell_jar} run {cromwell_args} -i {inputs_file} "
            f"{imports_zip_arg} {wdl_path}"
        )
        LOG.info(
            f"Executing cromwell command '{cmd}' with inputs "
            f"{json.dumps(cromwell_inputs, default=str)}"
        )
        exe = delegator.run(cmd, block=True)
        if not exe.ok:
            raise Exception(
                f"Cromwell command failed; stdout={exe.out}; stderr={exe.err}"
            )

        outputs = self.get_cromwell_outputs(exe.out)

        for name, expected_value in expected.items():
            key = f"{workflow_name}.{name}"
            if key not in outputs:
                raise AssertionError(f"Workflow did not generate output {key}")
            if isinstance(expected_value, DataFile):
                expected_value.assert_contents_equal(outputs[key])
            else:
                assert expected_value == outputs[key]

    def _get_path(self, path):
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        return path

    @staticmethod
    def get_cromwell_outputs(output):
        lines = output.splitlines(keepends=False)
        start = None
        for i, line in enumerate(lines):
            if line == "{" and lines[i+1].lstrip().startswith('"outputs":'):
                start = i
            elif line == "}" and start is not None:
                end = i
                break
        else:
            raise AssertionError("No outputs JSON found in Cromwell stdout")
        return json.loads("\n".join(lines[start:(end + 1)]))["outputs"]


@contextlib.contextmanager
def tempdir():
    """
    Context manager that creates a temporary directory, yields it, and then
    deletes it after return from the yield.
    """
    temp = tempfile.mkdtemp()
    try:
        yield temp
    finally:
        shutil.rmtree(temp)


@contextlib.contextmanager
def chdir(todir):
    """
    Context manager that temporarily changes directories.

    Args:
        todir: The directory to change to.
    """
    curdir = os.getcwd()
    try:
        os.chdir(todir)
        yield todir
    finally:
        os.chdir(curdir)


@pytest.fixture(scope="module")
def project_root(request):
    """
    Fixture that provides the root directory of the project.
    """
    return os.path.abspath(os.path.join(os.path.dirname(request.fspath), "../.."))


@pytest.fixture(scope="module")
def test_data_file():
    """
    Fixture that provides the path to the JSON file that describes test data files.
    """
    return "tests/test_data.json"


@pytest.fixture(scope="module")
def test_data_dir(project_root):
    """
    Fixture that provides the directory in which to cache the test data. If the
    "TEST_DATA_DIR" environment variable is set, the value will be used as the
    execution directory path, otherwise a temporary directory is used.

    Args:
        project_root: The root directory to use when the test data directory is
            specified as a relative path.
    """
    with _test_dir("TEST_DATA_DIR", project_root) as data_dir:
        yield data_dir


@pytest.fixture(scope="function")
def test_execution_dir(project_root):
    """
    Fixture that provides the directory in which the test is to be executed. If the
    "EXECUTION_DIR" environment variable is set, the value will be used as the
    execution directory path, otherwise a temporary directory is used.

    Args:
        project_root: The root directory to use when the execution directory is
            specified as a relative path.
    """
    with _test_dir("TEST_EXECUTION_DIR", project_root) as execution_dir:
        yield execution_dir


@contextlib.contextmanager
def _test_dir(envar, project_root):
    """
    Context manager that looks for a specific environment variable to specify a
    directory. If the environment variable is not set, a temporary directory is
    created and cleaned up upon return from the yield.

    Args:
        envar: The environment variable to look for.
        project_root: The root directory to use when the path is relative.

    Yields:
        A directory path.
    """
    test_dir = os.environ.get(envar, None)
    cleanup = False
    if not test_dir:
        test_dir = tempfile.mkdtemp()
        cleanup = True
    else:
        if not os.path.isabs(test_dir):
            test_dir = os.path.abspath(os.path.join(project_root, test_dir))
        if not os.path.exists(test_dir):
            os.makedirs(test_dir, exist_ok=True)
    try:
        yield test_dir
    finally:
        if cleanup:
            shutil.rmtree(test_dir)


@pytest.fixture(scope="session")
@_deprecated
def default_env():
    """
    No longer used.
    """
    return None


@pytest.fixture(scope="session")
def http_header_map():
    """
    Fixture that provides a mapping from HTTP header name to the environment variable
    from which the value should be retrieved.
    """
    return {"X-JFrog-Art-Api": "TOKEN"}


@pytest.fixture(scope="session")
def http_headers(http_header_map):
    """
    Fixture that provides request HTTP headers to use when downloading files.
    """
    return _env_map(http_header_map)


@pytest.fixture(scope="session")
def proxy_map():
    """
    Fixture that provides a mapping from proxy name to the environment variable
    from which the value should be retrieved.
    """
    return {
        "http": "HTTP_PROXY",
        "https": "HTTPS_PROXY"
    }


@pytest.fixture(scope="session")
def proxies(proxy_map):
    """
    Fixture that provides the proxies to use when downloading files.
    """
    return _env_map(proxy_map)


def _env_map(key_env_map):
    """
    Given a mapping of keys to environment variables, creates a mapping of the keys
    to the values of those environment variables (if they are set).
    """
    env_map = {}
    for name, ev in key_env_map.items():
        value = os.environ.get(ev, None)
        if value:
            env_map[name] = value
    return env_map


@pytest.fixture(scope="session")
def import_paths():
    """
    Fixture that provides the path to a file that lists the names of WDL scripts
    to make avaialble as imports.
    """
    return None


@pytest.fixture(scope="session")
def java_bin():
    """
    Fixture that provides the path to the java binary.
    """
    return os.path.join(
        os.path.abspath(os.environ.get("JAVA_HOME", "/usr")),
        "bin", "java"
    )


@pytest.fixture(scope="session")
def cromwell_jar_file():
    """
    Fixture that provides the path to the Cromwell JAR file. First looks for the
    CROMWELL_JAR environment variable, then searches the classpath for a JAR file
    whose name starts with "cromwell". Defaults to "cromwell.jar" in the current
    directory.
    """
    if "CROMWELL_JAR" in os.environ:
        return os.environ.get("CROMWELL_JAR")

    classpath = os.environ.get("CLASSPATH")
    if classpath:
        for path in classpath.split(os.pathsep):
            if os.path.basename(path).lower().startswith("cromwell"):
                return path

    return "cromwell.jar"


@pytest.fixture(scope="module")
def test_data(test_data_file, test_data_dir, http_headers, proxies):
    """
    Provides an accessor for test data files, which may be local or in a remote
    repository. Requires a `test_data_file` argument, which is provided by a
    `@pytest.mark.parametrize` decoration. The test_data_file file is a JSON file
    with keys being workflow input or output parametrize, and values being hashes with
    any of the following keys:

    * url: URL to the remote data file
    * path: Path to the local data file
    * type: File type; recoginzed values: "vcf"

    Args:
        test_data_file: test_data_file fixture.
        test_data_dir: test_data_dir fixture.
        http_headers: http_headers fixture.
        proxies: proxies fixture.

    Examples:
        @pytest.fixture(scope="session")
        def test_data_file():
            return "tests/test_data.json"

        def test_workflow(test_data):
            print(test_data["myfile"])
    """
    return Data(test_data_dir, test_data_file, http_headers, proxies)


@pytest.fixture(scope="module")
def cromwell_harness(project_root, import_paths, java_bin, cromwell_jar_file):
    """
    Provides a harness for calling Cromwell on WDL scripts. Accepts an `import_paths`
    argument, which is provided by a `@pytest.mark.parametrize` decoration. The
    import_paths file contains a list of directories (one per line) that contain WDL
    files that should be made available as imports to the running WDL workflow.

    Args:
        project_root: project_root fixture.
        import_paths: import_paths fixture.
        java_bin: java_bin fixture.
        cromwell_jar_file: cromwell_jar_file fixture.

    Examples:
        @pytest.fixture(scope="session")
        def import_paths():
            return "tests/import_paths.txt"

        def test_workflow(cromwell_harness):
            cromwell_harness.run_workflow(...)
    """
    return CromwellHarness(project_root, import_paths, java_bin, cromwell_jar_file)


@pytest.fixture(scope="function")
def workflow_runner(cromwell_harness, test_execution_dir):
    """
    Provides a callable that runs a workflow (with the same signature as
    `CromwellHarness.run_workflow`) with the execution_dir being the one
    provided by the `test_execution_dir` fixture.
    """
    def _run_workflow(wdl_script, workflow_name, inputs, expected):
        with chdir(test_execution_dir):
            cromwell_harness.run_workflow(
                wdl_script, workflow_name, inputs, expected
            )
    return _run_workflow
