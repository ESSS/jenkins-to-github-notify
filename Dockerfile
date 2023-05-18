FROM python:3.10.5-slim

# pass with --build-arg SETUPTOOLS_SCM_PRETEND_VERSION=VERSION; this is needed by setuptools_scm
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt
COPY ./README.rst /app/README.rst
COPY ./LICENSE /app/LICENSE
COPY ./setup.cfg /app/setup.cfg
COPY ./pyproject.toml /app/pyproject.toml
COPY ./src /app/src
COPY ./tests /app/tests
COPY ./pytest.ini /app/pytest.ini

RUN pip install --upgrade pip
RUN pip install --upgrade pip-tools
RUN pip-sync

EXPOSE 5000

CMD ["uvicorn", "jenkins_to_github_notify.app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "4"]
