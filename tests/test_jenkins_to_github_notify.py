import json
from contextlib import contextmanager
from contextlib import nullcontext
from pathlib import Path

import github3
import jenkins
import pytest
import requests
from jenkins_to_github_notify.app import handle_jenkins_notification
from jenkins_to_github_notify.notify import BuildStatus
from jenkins_to_github_notify.notify import check_configuration
from jenkins_to_github_notify.notify import compute_job_alias
from jenkins_to_github_notify.notify import fetch_build_info
from jenkins_to_github_notify.notify import fetch_repo_infos_based_on_job_xml_config
from jenkins_to_github_notify.notify import parse_repos_and_branches
from jenkins_to_github_notify.notify import parse_slug
from jenkins_to_github_notify.notify import post_status_to_github
from jenkins_to_github_notify.notify import RepoBuildInfo
from jenkins_to_github_notify.notify import validate_event
from jenkins_to_github_notify.notify import validate_secret
from pytest_mock import MockerFixture


@pytest.fixture
def fake_config() -> dict[str, str]:
    return {
        "JENKINS_URL": "http://FAKE_JENKINS_URL",
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
        ("jenkins-response-1.json", BuildStatus.Success),
        ("jenkins-response-2.json", BuildStatus.Failure),
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
    job_name = "alfasim-fb-ASIM-4742-add-gui-support-esp-catolog-app-newlinux"
    build_number = 6
    with mocking_jenkins_build_info(
        mocker, datadir / json_name, fake_config, job_name, build_number
    ):
        result = fetch_build_info(fake_config, job_name, build_number)
        branch_name = "fb-ASIM-4742-add-gui-support-esp-catolog"
        assert list(result.repo_infos) == [
            RepoBuildInfo(
                slug="ESSS/alfasim",
                branch_name=branch_name,
                commit="183c3b5d60eb015704eb081d600b79e2c261f3a3",
            ),
            RepoBuildInfo(
                slug="ESSS/qmxgraph",
                branch_name=branch_name,
                commit="3a93b466b35fa703897d0d35fe93f12ea027da90",
            ),
            RepoBuildInfo(
                slug="ESSS/hookman",
                branch_name=branch_name,
                commit="4a7af78b5dc6d1bdd820ccea9c12beb07a113d13",
            ),
            RepoBuildInfo(
                slug="ESSS/alfasim-sdk",
                branch_name=branch_name,
                commit="de93939e21c4c942e2d0ad00d017c1df15b8f3ab",
            ),
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
        assert list(result.repo_infos) == []
        assert result.status is BuildStatus.Failure


def test_fetch_build_status_no_build_info(
    fake_config: dict[str, str],
    datadir: Path,
    mocker: MockerFixture,
) -> None:
    """
    Fallback to ask GitHub for branches/commits in case the job build info from Jenkins
    does not contain that information.
    """
    job_name = "test-code-cov-fb-EDEN-2506-github-notification-newlinux"
    build_number = 16
    mock_login = mocker.patch.object(github3, "login")
    mock_repository = mock_login.return_value.repository
    mock_branch = mock_repository.return_value.branch
    mock_branch.return_value.commit.sha = "deadbeef"

    with mocking_jenkins_build_info(
        mocker, datadir / "jenkins-response-no-build-data.json", fake_config, job_name, build_number
    ) as jenkins_mock:
        job_xml_config = datadir.joinpath("job-multiple-repos.xml").read_text("UTF-8")
        jenkins_mock.return_value.get_job_config.return_value = job_xml_config
        result = fetch_build_info(fake_config, job_name, build_number)
        assert list(result.repo_infos) == [
            RepoBuildInfo(
                slug="ESSS/test-code-cov",
                branch_name="fb-EDEN-2506-github-notification",
                commit="deadbeef",
            )
        ]
        assert result.status is BuildStatus.Pending


@pytest.mark.parametrize(
    "input_url, expected_url",
    [
        ("ssh://git@github.com/ESSS/qmxgraph.git", "ESSS/qmxgraph"),
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
            job_url="job/test-code-cov-fb-EDEN-2506-github-notification-newlinux/8",
            build_number=8,
            status=BuildStatus.Success,
        )
    url = f"https://api.github.com/repos/ESSS/test-code-cov/statuses/80fd371bb50211b938afa7fa7c04f7b0f5fefecb"
    job_alias = "test-code-cov/newlinux"
    description = f"build #8 success"
    json_data = {
        "state": "success",
        "target_url": "http://FAKE_JENKINS_URL/job/test-code-cov-fb-EDEN-2506-github-notification-newlinux/8",
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


def test_handle_jenkins_notification(
    mocker: MockerFixture, fake_config: dict[str, str], datadir: Path
) -> None:
    """Integration test for the main endpoint."""
    import jenkins_to_github_notify.app

    job_name = "alfasim-fb-ASIM-4742-add-gui-support-esp-catolog-app-newlinux"
    build_number = 6

    with mocking_jenkins_build_info(
        mocker, datadir / "jenkins-response-1.json", fake_config, job_name, 6
    ):
        post_mock = mocker.patch.object(requests, "post")
        post_mock.return_value.status_code = 200

        mocker.patch.dict(jenkins_to_github_notify.app.config, values=fake_config, clear=True)

        handle_jenkins_notification(
            secret=fake_config["JENKINS_SECRET"],
            event="jenkins.job.started",
            job_name=job_name,
            build_number=build_number,
            url=f"job/{job_name}/{build_number}",
        )

    headers = {"Accept": "application/vnd.github+jso", "Authorization": "token FAKE_GH_TOKEN"}
    json = {
        "state": "success",
        "target_url": "http://FAKE_JENKINS_URL/job/alfasim-fb-ASIM-4742-add-gui-support-esp-catolog-app-newlinux/6",
        "description": "build #6 success",
        "context": "alfasim/app-newlinux job",
    }
    url_prefix = "https://api.github.com/repos/ESSS"
    assert post_mock.call_args_list == [
        mocker.call(
            url=f"{url_prefix}/alfasim/statuses/183c3b5d60eb015704eb081d600b79e2c261f3a3",
            json=json,
            headers=headers,
        ),
        mocker.call(
            url=f"{url_prefix}/qmxgraph/statuses/3a93b466b35fa703897d0d35fe93f12ea027da90",
            json=json,
            headers=headers,
        ),
        mocker.call(
            url=f"{url_prefix}/hookman/statuses/4a7af78b5dc6d1bdd820ccea9c12beb07a113d13",
            json=json,
            headers=headers,
        ),
        mocker.call(
            url=f"{url_prefix}/alfasim-sdk/statuses/de93939e21c4c942e2d0ad00d017c1df15b8f3ab",
            json=json,
            headers=headers,
        ),
    ]


class TestParseReposAndBranches:
    def test_parse_repos_and_branches_multiple(self, datadir: Path) -> None:
        job_xml_config = datadir.joinpath("job-multiple-repos.xml").read_text("UTF-8")
        result = parse_repos_and_branches(job_xml_config)
        assert result == [
            ("git@github.com:ESSS/test-code-cov.git", "fb-EDEN-2506-github-notification"),
            (
                "ssh://git@eden.fln.esss.com.br:7999/esss/eden.git",
                "fb-EDEN-2506-github-notification",
            ),
        ]

    def test_parse_repos_and_branches_single(self, datadir: Path) -> None:
        job_xml_config = datadir.joinpath("job-single-repo.xml").read_text("UTF-8")
        result = parse_repos_and_branches(job_xml_config)
        assert result == [
            ("ssh://git@eden.fln.esss.com.br:7999/esss/eden.git", "master"),
        ]


class TestFetchRepoInfosBasedOnJobXMLConfig:
    def test_fetch_repo_infos_based_on_job_xml_config(
        self, fake_config: dict[str, str], mocker: MockerFixture, datadir: Path
    ) -> None:
        mock_login = mocker.patch.object(github3, "login")
        mock_repository = mock_login.return_value.repository
        mock_branch = mock_repository.return_value.branch
        mock_branch.return_value.commit.sha = "deadbeef"

        job_xml_config = datadir.joinpath("job-multiple-repos.xml").read_text("UTF-8")

        result = fetch_repo_infos_based_on_job_xml_config(fake_config, job_xml_config)
        assert result == [
            RepoBuildInfo(
                slug="ESSS/test-code-cov",
                branch_name="fb-EDEN-2506-github-notification",
                commit="deadbeef",
            )
        ]
        assert mock_login.call_args == mocker.call(token=fake_config["GH_TOKEN"])
        assert mock_repository.call_args == mocker.call("ESSS", "test-code-cov")
        assert mock_branch.call_args == mocker.call("fb-EDEN-2506-github-notification")

    def test_fetch_repo_infos_based_on_job_xml_config_no_github_repo(
        self, fake_config: dict[str, str], mocker: MockerFixture, datadir: Path
    ) -> None:
        mock_login = mocker.patch.object(github3, "login")
        job_xml_config = datadir.joinpath("job-single-repo.xml").read_text("UTF-8")
        result = fetch_repo_infos_based_on_job_xml_config(fake_config, job_xml_config)
        assert result == []
        assert mock_login.call_args is None


@contextmanager
def mocking_jenkins_build_info(
    mocker: MockerFixture, response_json: Path, config: dict[str, str], job_name: str, number: int
):
    jenkins_mock = mocker.patch.object(jenkins, "Jenkins")
    response_data = json.loads(response_json.read_text("UTF-8"))
    jenkins_mock().get_build_info.return_value = response_data

    yield jenkins_mock

    assert jenkins_mock.call_args == mocker.call(
        config["JENKINS_URL"],
        username=config["JENKINS_USERNAME"],
        password=config["JENKINS_PASSWORD"],
    )
    assert jenkins_mock().get_build_info.call_args == mocker.call(job_name, number=number)
