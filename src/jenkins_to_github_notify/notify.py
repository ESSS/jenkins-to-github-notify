import re
import secrets
from enum import Enum
from typing import Sequence

import attr
import jenkins


def check_configuration(config: dict[str, str]) -> None:
    """
    Check that the given configuration dict contains all required variables.
    """

    def check_value(name: str) -> None:
        if name not in config:
            raise RuntimeError(f"Missing configuration variable: {name}.\nCheck your .env file.")

    check_value("JENKINS_URL")
    check_value("JENKINS_USERNAME")
    check_value("JENKINS_PASSWORD")
    check_value("JENKINS_SECRET")
    check_value("GH_TOKEN")


def validate_secret(config: dict[str, str], secret: str) -> None:
    """Ensures the secret provided by a Jenkins webhook matches what we expect in the configuration."""
    if not secrets.compare_digest(config["JENKINS_SECRET"], secret):
        raise RuntimeError("Invalid secret.")


def validate_event(event: str) -> None:
    """Ensures the event posted by the Jenkins webhook is one we expect."""
    if event not in ("jenkins.job.started", "jenkins.job.completed"):
        raise RuntimeError(f"Invalid event received: {event}")


class BuildStatus(Enum):
    """
    Status of a job.

    While Jenkins jobs have other possible status, for the purposes of this
    service we are only interested if it has failed or not after it has completed.
    """

    Pending = "pending"
    Success = "success"
    Failure = "failure"


@attr.s(auto_attribs=True, frozen=True)
class JobBuildInfo:
    """
    Information about a Jenkins job, in the context of this service.
    """

    slugs_and_commits: Sequence[tuple[str, str]]
    status: BuildStatus


def fetch_build_info(config: dict[str, str], job_name: str, number: int) -> JobBuildInfo:
    """Fetches a JobBuildInfo structure containing GitHub repositories and commits for the given job."""
    server = jenkins.Jenkins(
        config["JENKINS_URL"],
        username=config["JENKINS_USERNAME"],
        password=config["JENKINS_PASSWORD"],
    )
    response_data = server.get_build_info(job_name, number=number)

    slugs_and_commits = []
    for action in response_data.get("actions", []):
        if action.get("_class", "") != "hudson.plugins.git.util.BuildData":
            continue
        remote_urls = action.get("remoteUrls", [])
        if not remote_urls:
            continue
        for remote_url in remote_urls:
            if slug := parse_slug(remote_url):
                commit = action["lastBuiltRevision"]["SHA1"]
                slugs_and_commits.append((slug, commit))
    job_result = response_data.get("result", "")
    if job_result is None:
        status = BuildStatus.Pending
    elif job_result == "SUCCESS":
        status = BuildStatus.Success
    else:
        status = BuildStatus.Failure
    return JobBuildInfo(slugs_and_commits, status)


_SSH_URL = re.compile(r"git@github.com:([\w_-]+)/([\w_-]+)\.git")
_HTTPS_URL = re.compile(r"https://github.com/([\w_-]+)/([\w_-]+)")


def parse_slug(url: str) -> str | None:
    """
    Parses the GitHub slug from the given HTTPS or SSH URLs.
    If it is not a valid GitHub address returns None.
    """
    if m := _SSH_URL.match(url):
        return f"{m.group(1)}/{m.group(2)}"
    elif m := _HTTPS_URL.match(url):
        return f"{m.group(1)}/{m.group(2)}"
    else:
        return None
