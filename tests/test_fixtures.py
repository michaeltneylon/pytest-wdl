import os
import pytest


@pytest.fixture(scope="module")
def project_root(request):
    return os.path.abspath(os.path.join(os.path.dirname(request.fspath), ".."))


@pytest.fixture(scope="module")
def test_data_dir(project_root):
    return os.path.join(project_root, "tests")


def test_fixtures(test_data, workflow_runner):
    inputs = {
        "in_txt": test_data["in_txt"]
    }
    outputs = {
        "out_txt": test_data["out_txt"]
    }
    workflow_runner("tests/test.wdl", "cat_file", inputs, outputs)