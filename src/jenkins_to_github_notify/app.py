"""
FastAPI app entry point.

Here we declare all the FastAPI entry points, which should do minimal work as possible,
delegating to other modules the actual work.
"""
import logging

from dotenv import dotenv_values
from fastapi import FastAPI
from jenkins_to_github_notify.notify import check_configuration
from jenkins_to_github_notify.notify import fetch_build_info
from jenkins_to_github_notify.notify import post_status_to_github
from jenkins_to_github_notify.notify import validate_event
from jenkins_to_github_notify.notify import validate_secret

app = FastAPI()
config: dict[str, str] = {}
logger = logging.getLogger("app")


@app.on_event("startup")
async def startup_event() -> None:
    """Fail early during startup in case any variable is missing."""
    for key, value in dotenv_values(".env").items():
        assert isinstance(value, str), f"Unexpected type in config value: {value!r} {type(value)}"
        config[key] = value
    check_configuration(config)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s  %(message)s")
    logger.setLevel(logging.INFO)
    logger.info("Configuration validated.")


@app.get("/jobs/notify")
def handle_jenkins_notification(
    secret: str,
    event: str,
    job_name: str,
    build_number: int,
    url: str,
) -> None:
    """
    End-point that is hit by Jenkins whenever an job starts or completes.

    It gathers all GitHub repositories that are checked out by the job and
    their hashes, and posts a status to each of their commits on GitHub.
    """
    logger.info("New job received")
    logger.info(f"  event: {event}")
    logger.info(f"  job_name: {job_name}")
    logger.info(f"  build_number: {build_number}")
    logger.info(f"  url: {url}")
    validate_secret(config, secret)
    validate_event(event)
    logger.info(f"  fetching build_info...")
    build_info = fetch_build_info(config, job_name, build_number)
    logger.info(f"  build_info: {build_info}")
    if build_info.repo_infos:
        logger.info(f"  posting status {build_info.status.value}")
    for repo_info in build_info.repo_infos:
        logger.info(f"  * {repo_info.slug} {repo_info.commit}")
        post_status_to_github(
            config=config,
            slug=repo_info.slug,
            commit=repo_info.commit,
            branch_name=repo_info.branch_name,
            job_name=job_name,
            build_number=build_number,
            status=build_info.status,
            job_url=url,
        )
