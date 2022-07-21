========================
jenkins-to-github-notify
========================

Notify build status using a Jenkins Webhook.


Jenkins Configuration
=====================

Go to *Configure System*, and enter a new *Web Hook*:

* Enter the address/port where the container is running.
* Enter a timeout (default is fine).
* Click *Advanced*, uncheck *All Events*, and mark:
  - ``jenkins.job.started``
  - ``jenkins.job.completed``

  For both of them, *customize* the URL with::

    ${url}?event=${event.name}&build_number=${run.number}&job_name=${run.project.name}&url=${run.getUrl()}&secret=JENKINS_SECRET

  Replace ``JENKINS_SECRET`` with the value of the ``JENKINS_SECRET`` variable.
