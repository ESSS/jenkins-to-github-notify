========================
jenkins-to-github-notify
========================

Notify build status using a Jenkins Webhook.

This service receives events posted by Jenkins WebHooks and updates `GitHub commit status API <https://docs.github.com/en/rest/commits/statuses>`_.


Installation
============

This repository contains a ``Dockerfile`` which can be used to build an image, which can then be run in any server.

Service configuration
---------------------

Place a ``.env`` file in the current working directory where the service is started::

    JENKINS_URL=https://some-server.com/jenkins
    JENKINS_USERNAME=jenkins-bot
    JENKINS_PASSWORD=PASSWORD_FOR_JENKINS_BOT
    JENKINS_SECRET=GENERATED_SECRET

    GH_TOKEN=GITHUB_TOKEN


* ``JENKINS_URL``: full URL to the Jenkins server.
* ``JENKINS_USERNAME``: user name to access the Jenkins API.
* ``JENKINS_PASSWORD``: password or token for ``JENKINS_USERNAME``. Prefer tokens when possible as they can be easily revoked and has less permissions.
* ``JENKINS_SECRET``: this is a secret that will be sent by the Web Hook and verified by the service to ensure the requests are coming from the expected place.

  Can be generated easily with:

  .. code-block:: python

     >>> import secrets
     >>> secrets.token_hex()
     'GENERATED TOKEN'

  This same secret will be used in the Jenkins configuration below.

* ``GH_TOKEN``: a `personal access token <https://github.com/settings/tokens/>`__ with ``repo`` permissions to the repositories that will have their status updated.


Jenkins configuration
---------------------

Go to *Configure System*, and enter a new *Web Hook*:

* Enter the address/port where the container is running.
* Enter a timeout (30s is fine).
* Click *Advanced*, uncheck *All Events*, and mark:

  - ``jenkins.job.started``
  - ``jenkins.job.completed``

  For both of them, *customize* the URL with::

    ${url}?event=${event.name}&build_number=${run.number}&job_name=${run.project.name}&url=${run.getUrl()}&secret=JENKINS_SECRET

  Replace ``JENKINS_SECRET`` with the value of the ``JENKINS_SECRET`` variable.


Development
===========

Create a virtual environment for Python 3.10, activate it, then execute:


.. code-block:: console

    pip install piptools
    pip-sync
    pre-commit install


To run the tests:

.. code-block:: console

    pytest

Deployment
==========

There is a ``deploy`` workflow which is `triggered manually <https://github.com/ESSS/jenkins-to-github-notify/actions/workflows/deploy.yml>`__.

The inputs are:

* ``version``: the ref version to push, usually a tag.
* ``push``: if we should push the build image to the configured docker registry or not.

This workflow uses these organization/repository secrets:

* ``docker_registry``: the URL of the docker registry used to login.
* ``docker_registry_push_url``: the URL where we should push images to.
* ``docker_push_user``: user name with has push access to the registry.
* ``docker_push_password``: password of the user.

Similar projects
================

There are Jenkins plugins which do something similar, but did not met all our requirements:

* `GitHub plugin <https://plugins.jenkins.io/github/>`_: also reports build status, however it does not work
  for multiple repositories (see `JENKINS-28177 <https://issues.jenkins.io/browse/JENKINS-28177>`_).
* `GitHub Checks plugin <https://plugins.jenkins.io/github-checks/>`_: unfortunately requires a more complex setup requiring
  a GitHub App for authentication, and seems (but not 100% sure) to require setting up a special "github project"
  in Jenkins (instead of a normal Git repository).


License
=======

MIT.
