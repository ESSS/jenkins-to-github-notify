"""
FastAPI app entry point.

Here we declare all the FastAPI entry points, which should do minimal work as possible,
delegating to other modules the actual work.
"""
from dotenv import dotenv_values
from fastapi import FastAPI
from jenkins_to_github_notify.notify import check_configuration
from jenkins_to_github_notify.notify import fetch_build_info
from jenkins_to_github_notify.notify import validate_event
from jenkins_to_github_notify.notify import validate_secret

app = FastAPI()
config: dict[str, str] = {}


@app.on_event("startup")
async def startup_event() -> None:
    """Fail early during startup in case any variable is missing."""
    for key, value in dotenv_values(".env").items():
        assert isinstance(value, str), f"Unexpected type in config value: {value!r} {type(value)}"
        config[key] = value
    check_configuration(config)


@app.get("/jobs/notify")
def handle_jenkins_notification(
    secret: str,
    event: str,
    job_name: str,
    build_number: int,
    url: str,
) -> None:
    """ """
    validate_secret(config, secret)
    validate_event(event)
    fetch_build_info(config, job_name, build_number)
