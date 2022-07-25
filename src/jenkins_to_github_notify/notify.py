import re
import secrets
from enum import Enum
from io import StringIO
from typing import Any
from typing import Sequence
from xml.etree.ElementTree import parse

import attr
import github3
import jenkins
import requests


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


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class RepoBuildInfo:
    """
    Information about a repository that is part of job run.
    """

    slug: str
    branch_name: str
    commit: str


@attr.s(auto_attribs=True, frozen=True)
class JobBuildInfo:
    """
    Information about a Jenkins job.
    """

    repo_infos: Sequence[RepoBuildInfo]
    status: BuildStatus


def fetch_build_info(config: dict[str, str], job_name: str, number: int) -> JobBuildInfo:
    """Fetches a JobBuildInfo structure containing GitHub repositories and commits for the given job."""
    server = jenkins.Jenkins(
        config["JENKINS_URL"],
        username=config["JENKINS_USERNAME"],
        password=config["JENKINS_PASSWORD"],
    )
    response_data = server.get_build_info(job_name, number=number)

    try:
        repo_infos = _extract_repo_infos_from_response(response_data)
    except NoRepositoriesFoundError:
        # Build info response does not contain any repositories/commits (happens when
        # starting a job), so we need to parse the repositories/branches configuration
        # from the job configuration and query GitHub.
        job_xml_config = server.get_job_config(job_name)
        repo_infos = fetch_repo_infos_based_on_job_xml_config(config, job_xml_config)

    job_result = response_data.get("result", "")
    if job_result is None:
        status = BuildStatus.Pending
    elif job_result == "SUCCESS":
        status = BuildStatus.Success
    else:
        status = BuildStatus.Failure
    return JobBuildInfo(repo_infos, status)


def _extract_repo_infos_from_response(response_data: dict[str, Any]) -> Sequence[RepoBuildInfo]:
    """
    Extract a sequence of RepoBuildInfo from the the  response of a get_build_info() call
    for a jenkins job.

    This returns only build information for GitHub repositories, any other servers are ignored.

    Raises NoRepositoriesFoundError if there is no repository at all definition in the response (GitHub or not).
    """
    repo_infos = []
    found_any_repos_at_all = False
    for action in response_data.get("actions", []):
        if action.get("_class", "") != "hudson.plugins.git.util.BuildData":
            continue
        remote_urls = action.get("remoteUrls", [])
        if not remote_urls:
            continue
        found_any_repos_at_all = True
        for remote_url in remote_urls:
            if slug := parse_slug(remote_url):
                branch_data = action["lastBuiltRevision"]["branch"][0]
                branch_name = branch_data["name"].removeprefix("origin/")
                commit = branch_data["SHA1"]
                repo_infos.append(RepoBuildInfo(slug=slug, commit=commit, branch_name=branch_name))

    if found_any_repos_at_all:
        return repo_infos
    else:
        raise NoRepositoriesFoundError


def fetch_repo_infos_based_on_job_xml_config(
    config: dict[str, str], job_xml_config: str
) -> Sequence[RepoBuildInfo]:
    """
    Parse the given Jenkins job configuration and extract the GitHub repositories and branches from it,
    using then the GitHub API to query the latest commit for that branch.
    """
    repos_and_branches = parse_repos_and_branches(job_xml_config)
    slugs_and_branches = [
        (parse_slug(url), branch) for url, branch in repos_and_branches if parse_slug(url)
    ]
    if not slugs_and_branches:
        return []

    result = []
    gh = github3.login(token=config["GH_TOKEN"])
    for slug, branch in slugs_and_branches:
        assert slug is not None
        owner, name = slug.split("/")
        repo = gh.repository(owner, name)
        sha = repo.branch(branch).commit.sha
        result.append(RepoBuildInfo(slug=slug, branch_name=branch, commit=sha))

    return result


def parse_repos_and_branches(job_xml_config: str) -> Sequence[tuple[str, str]]:
    """
    Given the XML configuration of a job, parse it and return a sequence of
    (repo-url, branch-name).
    """
    et = parse(StringIO(job_xml_config))
    r = et.getroot()

    # Multiple repositories:
    git_roots = list(r.findall(".//hudson.plugins.git.GitSCM"))
    # Single repositories:
    git_roots += list(r.findall(".//scm[@class='hudson.plugins.git.GitSCM']"))

    result = []
    for git_root in git_roots:
        names = [x.text for x in git_root.findall(".//hudson.plugins.git.BranchSpec/name")]
        urls = [x.text for x in git_root.findall(".//hudson.plugins.git.UserRemoteConfig/url")]
        if names and urls:
            name = names[0]
            url = urls[0]
            if name and url:
                result.append((url, name))
    return result


def parse_slug(url: str) -> str | None:
    """
    Parses the GitHub slug from the given HTTPS or SSH URLs (a slug is a string in the form "owner/repo").
    If it is not a valid GitHub address returns None.
    """
    if m := re.match(r"(?:ssh://)?git@github.com[:/]([\w_-]+)/([\w_-]+)\.git", url):
        return f"{m.group(1)}/{m.group(2)}"
    elif m := re.match(r"https://github.com/([\w_-]+)/([\w_-]+)", url):
        return f"{m.group(1)}/{m.group(2)}"
    else:
        return None


def post_status_to_github(
    *,
    config: dict[str, str],
    slug: str,
    commit: str,
    branch_name: str,
    job_name: str,
    job_url: str,
    build_number: int,
    status: BuildStatus,
) -> None:
    url = f"https://api.github.com/repos/{slug}/statuses/{commit}"
    job_alias = compute_job_alias(job_name=job_name, branch_name=branch_name)
    description = f"build #{build_number} {status.value}"
    json_data = {
        "state": status.value,
        "target_url": config["JENKINS_URL"] + "/" + job_url,
        "description": description,
        "context": f"{job_alias} job",
    }
    headers = {
        "Accept": "application/vnd.github+jso",
        "Authorization": f"token {config['GH_TOKEN']}",
    }
    response = requests.post(url=url, json=json_data, headers=headers)
    if not (200 <= response.status_code < 300):
        raise RuntimeError(f"ERROR {response.status_code} ({response.json()})")


def compute_job_alias(*, job_name: str, branch_name: str) -> str:
    """
    Given a full job name and branch name, returns a more compact version
    to display in the PR.

    For example:

        >>> compute_job_alias(job_name="alfasim-fb-EDEN-2505-app-win64", branch_name="fb-EDEN-2505")
        "alfasim/app-win64"
    """
    origin_prefix = "origin/"
    if branch_name.startswith(origin_prefix):
        branch_name = branch_name[len(origin_prefix) :]
    # Remove branch name from the job name:
    # "alfasim-fb-EDEN-2505-app-win64" -> "alfasim--app-win64"
    alias = job_name.replace(branch_name, "")
    # Change "--" from removing the branch to "/" for a nice visual separation:
    # "alfasim--app-win64" -> "alfasim/app-win64"
    alias = alias.replace("--", "/")
    # Remove any trailing "-" in case the branch is the last part:
    # "test-repo-fb-EDEN-2505" -> "test-repo-" -> "test-repo"
    alias = alias.rstrip("-")
    return alias


class NoRepositoriesFoundError(Exception):
    """
    Raised by _extract_repo_infos_from_response if not repositories are found in
    the response.
    """
