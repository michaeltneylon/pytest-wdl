#! /usr/bin/env python

"""Test hello_world parent workflow"""

import os

import pytest


@pytest.fixture(scope="module")
def project_root():
    """
    Override the project root for this test since it doesn't follow a
    standard pattern.
    """
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def workflow_data_descriptor_file():
    """
    Fixture that provides the path to the JSON file that describes test data files.
    """
    return os.path.abspath(os.path.join(os.path.dirname(__file__),
                                        "test_data.json"))


def test_hello_world_parent_workflow(workflow_data, workflow_runner, request):
    """Test the hello_world parent workflow with fixed inputs and outputs."""
    inputs = {
        "input_files": [
            workflow_data["test_file"],
            workflow_data["test_file"],
        ]
    }
    expected = {
        "single_file": workflow_data["test_file"]
    }
    workflow_runner(
        os.path.abspath(os.path.join(os.path.dirname(
            request.fspath), "../parent_workflow.wdl")),
        "hello_world_workflow",
        inputs,
        expected
    )
