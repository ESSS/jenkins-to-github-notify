import json
from contextlib import contextmanager
from contextlib import nullcontext
from pathlib import Path

import jenkins
import pytest
import requests
from jenkins_to_github_notify.notify import BuildStatus
from jenkins_to_github_notify.notify import check_configuration
from jenkins_to_github_notify.notify import compute_job_alias
from jenkins_to_github_notify.notify import fetch_build_info
from jenkins_to_github_notify.notify import parse_slug
from jenkins_to_github_notify.notify import post_status_to_github
from jenkins_to_github_notify.notify import validate_event
from jenkins_to_github_notify.notify import validate_secret
from pytest_mock import MockerFixture


@pytest.fixture
def fake_config() -> dict[str, str]:
    return {
        "JENKINS_URL": "FAKE_JENKINS_URL",
        "JENKINS_USERNAME": "FAKE_JENKINS_USERNAME",
        "JENKINS_PASSWORD": "FAKE_JENKINS_PASSWORD",
        "JENKINS_SECRET": "FAKE_JENKINS_SECRET",
        "GH_TOKEN": "FAKE_GH_TOKEN",
    }


def test_check_configuration() -> None:
    config = {
        "JENKINS_URL": "",
        "JENKINS_USERNAME": "",
        "JENKINS_PASSWORD": "",
        "JENKINS_SECRET": "",
    }
    with pytest.raises(RuntimeError):
        check_configuration(config)

    config["GH_TOKEN"] = ""
    check_configuration(config)


def test_validate_secret() -> None:
    with pytest.raises(RuntimeError):
        validate_secret(dict(JENKINS_SECRET="secret1"), "secret2")

    validate_secret(dict(JENKINS_SECRET="secret2"), "secret2")


@pytest.mark.parametrize(
    "json_name, expected_status",
    [
        ("jenkins-response-1.json", BuildStatus.Failure),
        ("jenkins-response-2.json", BuildStatus.Success),
        ("jenkins-response-3.json", BuildStatus.Pending),
    ],
)
def test_fetch_build_status(
    fake_config: dict[str, str],
    datadir: Path,
    json_name: str,
    expected_status: BuildStatus,
    mocker: MockerFixture,
) -> None:
    with mocking_jenkins_build_info(
        mocker, datadir / json_name, fake_config, "alfasim-master-app-win64", 2179
    ):
        result = fetch_build_info(fake_config, "alfasim-master-app-win64", 2179)
        assert list(result.slugs_and_commits) == [
            ("ESSS/alfasim", "9be65f035ce5af7fe93c8c59f6a174860d152cc5"),
        ]
        assert result.status is expected_status


def test_fetch_build_status_no_github_repos(
    fake_config: dict[str, str], datadir: Path, mocker: MockerFixture
) -> None:
    with mocking_jenkins_build_info(
        mocker,
        datadir / "jenkins-response-4.json",
        fake_config,
        "rocky20-fb-ROCKY-15386-contact-api-functors-esss-benchmark",
        6,
    ):
        result = fetch_build_info(
            fake_config, "rocky20-fb-ROCKY-15386-contact-api-functors-esss-benchmark", 6
        )
        assert list(result.slugs_and_commits) == []
        assert result.status is BuildStatus.Failure


@pytest.mark.parametrize(
    "input_url, expected_url",
    [
        ("git@github.com:ESSS/alfasim-sdk.git", "ESSS/alfasim-sdk"),
        ("https://github.com/ESSS/alfasim-sdk", "ESSS/alfasim-sdk"),
        ("git@github.com:ESSS/alfasim_sdk.git", "ESSS/alfasim_sdk"),
        ("https://github.com/ESSS/alfasim_sdk", "ESSS/alfasim_sdk"),
        ("git@otherserver.com:ESSS/alfasim.git", None),
        ("https://otherserver.com/ESSS/alfasim", None),
    ],
)
def test_parse_slug(input_url: str, expected_url: str | None) -> None:
    assert parse_slug(input_url) == expected_url


def test_validate_event() -> None:
    validate_event("jenkins.job.started")
    validate_event("jenkins.job.completed")


@pytest.mark.parametrize(
    "status_code, expected_outcome",
    [
        (201, nullcontext()),
        (301, pytest.raises(RuntimeError, match="ERROR 301")),
    ],
)
def test_post_status_to_github(
    mocker: MockerFixture, fake_config: dict[str, str], status_code: int, expected_outcome
) -> None:
    post_mock = mocker.patch.object(requests, "post")
    post_mock.return_value.status_code = status_code
    with expected_outcome:
        post_status_to_github(
            config=fake_config,
            slug="ESSS/test-code-cov",
            commit="80fd371bb50211b938afa7fa7c04f7b0f5fefecb",
            branch_name="fb-EDEN-2506-github-notification",
            job_name="test-code-cov-fb-EDEN-2506-github-notification-newlinux",
            job_url="https://eden.esss.co/jenkins/job/test-code-cov-fb-EDEN-2506-github-notification-newlinux/8",
            job_number=8,
            status=BuildStatus.Success,
        )
    url = f"https://api.github.com/repos/ESSS/test-code-cov/statuses/80fd371bb50211b938afa7fa7c04f7b0f5fefecb"
    job_alias = "test-code-cov/newlinux"
    description = f"build #8 success"
    json_data = {
        "state": "success",
        "target_url": "https://eden.esss.co/jenkins/job/test-code-cov-fb-EDEN-2506-github-notification-newlinux/8",
        "description": description,
        "context": f"{job_alias} job",
    }
    headers = {
        "Accept": "application/vnd.github+jso",
        "Authorization": f"token {fake_config['GH_TOKEN']}",
    }
    assert post_mock.call_args == mocker.call(url=url, json=json_data, headers=headers)


@pytest.mark.parametrize(
    "job_name, branch_name, expected_alias",
    [
        (
            "eden-fb-EDEN-2505-win64",
            "fb-EDEN-2505",
            "eden/win64",
        ),
        (
            "eden-fb-EDEN-2505-win64",
            "origin/fb-EDEN-2505",
            "eden/win64",
        ),
        ("eden-fb-EDEN-2505", "fb-EDEN-2505", "eden"),
    ],
)
def test_compute_job_alias(job_name: str, branch_name: str, expected_alias: str) -> None:
    assert compute_job_alias(job_name=job_name, branch_name=branch_name) == expected_alias


@contextmanager
def mocking_jenkins_build_info(
    mocker: MockerFixture, response_json: Path, config: dict[str, str], job_name: str, number: int
):
    jenkins_mock = mocker.patch.object(jenkins, "Jenkins")
    response_data = json.loads(response_json.read_text("UTF-8"))
    jenkins_mock().get_build_info.return_value = response_data

    yield

    assert jenkins_mock.call_args == mocker.call(
        config["JENKINS_URL"],
        username=config["JENKINS_USERNAME"],
        password=config["JENKINS_PASSWORD"],
    )
    assert jenkins_mock().get_build_info.call_args == mocker.call(job_name, number=number)
