## Install from Artifactory PyPi

The module is stored in the private PyPi repository `elilillyco.jfrog.io/elilillyco/api/pypi/omics-pypi-lc/simple`

### Preferred Artifactory Install Method

To add this repo to your environment for all future installs, edit your `~/.pip/pip.conf` file like below, adding your username and password for Artifactory which is your email and the Artifactory API token:

```
[global]
index-url = https://pypi.org/simple
extra-index-url =
    https://<email>:<artifactory_token>@elilillyco.jfrog.io/elilillyco/api/pypi/omics-pypi-lc/simple
```

### One-Time Artifactory Install

If you just want to do this one-time, you can embed the extra-index-url into the pip command. You can also leave out the auth details and it will interactively prompt for them:

```commandline
pip install --extra-index-url https://elilillyco.jfrog.io/elilillyco/api/pypi/omics-pypi-lc/simple pytest_wdl
```
Which will then prompt for your username and password, the Artifactory email and token.

### Environment variables

The following environment variables are necessary to use in the Lilly environment, in conjunction with custom fixtures places in `conftest.py` in your project's root directory:

| variable name | recommended | description |
| ------------- | ----------- | ----------- |
| `HTTPS_PROXY` | required if behind proxy | |
| `HTTP_PROXY`  | required if behind proxy | |
| `TOKEN`       | yes         | currently this is an Artifactory token which is needed to fetch test data from the generic repo |

```python
# conftest.py
import pytest

@pytest.fixture(scope="session")
def http_header_map() -> dict:
    """
    Fixture that provides a mapping from HTTP header name to the environment variable
    from which the value should be retrieved.
    """
    return {"X-JFrog-Art-Api": "TOKEN"}


@pytest.fixture(scope="session")
def proxy_map() -> dict:
    """
    Fixture that provides a mapping from proxy name to the environment variable
    from which the value should be retrieved.
    """
    return {
        "http": "HTTP_PROXY",
        "https": "HTTPS_PROXY"
    }
```