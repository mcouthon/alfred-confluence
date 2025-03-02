#!/usr/bin/env python
#
# Copyright (c) 2014 Fabio Niephaus <fabio.niephaus@gmail.com>,
#       Dean Jackson <deanishe@deanishe.net>
#
# MIT Licence. See http://opensource.org/licenses/MIT
#
# Created on 2014-08-16
#

"""Self-updating from GitHub.

.. versionadded:: 1.9

.. note::

   This module is not intended to be used directly. Automatic updates
   are controlled by the ``update_settings`` :class:`dict` passed to
   :class:`~workflow.workflow.Workflow` objects.

"""


import os
import tempfile
import subprocess

from version import Version
import workflow
import web

# __all__ = []


RELEASES_BASE = "https://api.github.com/repos/{0}/releases"


_wf = None


def wf():
    """Lazy `Workflow` object."""
    global _wf
    if _wf is None:
        _wf = workflow.Workflow()
    return _wf


def download_workflow(url):
    """Download workflow at ``url`` to a local temporary file.

    :param url: URL to .alfredworkflow file in GitHub repo
    :returns: path to downloaded file

    """
    filename = url.split("/")[-1]

    if not url.endswith(".alfredworkflow") or not filename.endswith(".alfredworkflow"):
        raise ValueError(f"Attachment `{filename}` not a workflow")

    local_path = os.path.join(tempfile.gettempdir(), filename)

    wf().logger.debug("Downloading updated workflow from `%s` to `%s` ...", url, local_path)

    response = web.get(url)

    with open(local_path, "wb") as output:
        output.write(response.content)

    return local_path


def build_api_url(slug):
    """Generate releases URL from GitHub slug.

    :param slug: Repo name in form ``username/repo``
    :returns: URL to the API endpoint for the repo's releases

    """
    if len(slug.split("/")) != 2:
        raise ValueError(f"Invalid GitHub slug : {slug}")

    return RELEASES_BASE.format(slug)


def _validate_release(release):
    """Return release for running version of Alfred."""
    alf3 = wf().alfred_version.major == 3

    downloads = {".alfredworkflow": [], ".alfred3workflow": []}
    dl_count = 0
    version = release["tag_name"]

    for asset in release.get("assets", []):
        url = asset.get("browser_download_url")
        if not url:  # pragma: nocover
            continue

        ext = os.path.splitext(url)[1].lower()
        if ext not in downloads:
            continue

        # Ignore Alfred 3-only files if Alfred 2 is running
        if ext == ".alfred3workflow" and not alf3:
            continue

        downloads[ext].append(url)
        dl_count += 1

        # download_urls.append(url)

    if dl_count == 0:
        wf().logger.warning("Invalid release %s : No workflow file", version)
        return None

    for k in downloads:
        if len(downloads[k]) > 1:
            wf().logger.warning("Invalid release %s : multiple %s files", version, k)
            return None

    # Prefer .alfred3workflow file if there is one and Alfred 3 is
    # running.
    if alf3 and len(downloads[".alfred3workflow"]):
        download_url = downloads[".alfred3workflow"][0]

    else:
        download_url = downloads[".alfredworkflow"][0]

    wf().logger.debug("Release `%s` : %s", version, download_url)

    return {"version": version, "download_url": download_url, "prerelease": release["prerelease"]}


def get_valid_releases(github_slug, prereleases=False):
    """Return list of all valid releases.

    :param github_slug: ``username/repo`` for workflow's GitHub repo
    :param prereleases: Whether to include pre-releases.
    :returns: list of dicts. Each :class:`dict` has the form
        ``{'version': '1.1', 'download_url': 'http://github.com/...',
        'prerelease': False }``


    A valid release is one that contains one ``.alfredworkflow`` file.

    If the GitHub version (i.e. tag) is of the form ``v1.1``, the leading
    ``v`` will be stripped.

    """
    api_url = build_api_url(github_slug)
    releases = []

    wf().logger.debug("Retrieving releases list from `%s` ...", api_url)

    def retrieve_releases():
        wf().logger.info("Retrieving releases for `%s` ...", github_slug)
        return web.get(api_url).json()

    slug = github_slug.replace("/", "-")
    for release in wf().cached_data(f"gh-releases-{slug}", retrieve_releases):

        wf().logger.debug("Release : %r", release)

        release = _validate_release(release)
        if release is None:
            wf().logger.debug("Invalid release")
            continue

        elif release["prerelease"] and not prereleases:
            wf().logger.debug("Ignoring prerelease : %s", release["version"])
            continue

        releases.append(release)

    return releases


def check_update(github_slug, current_version, prereleases=False):
    """Check whether a newer release is available on GitHub.

    :param github_slug: ``username/repo`` for workflow's GitHub repo
    :param current_version: the currently installed version of the
        workflow. :ref:`Semantic versioning <semver>` is required.
    :param prereleases: Whether to include pre-releases.
    :type current_version: ``unicode``
    :returns: ``True`` if an update is available, else ``False``

    If an update is available, its version number and download URL will
    be cached.

    """
    releases = get_valid_releases(github_slug, prereleases)

    wf().logger.info("%d releases for %s", len(releases), github_slug)

    if not len(releases):
        raise ValueError("No valid releases for %s", github_slug)

    # GitHub returns releases newest-first
    latest_release = releases[0]

    # (latest_version, download_url) = get_latest_release(releases)
    vr = Version(latest_release["version"])
    vl = Version(current_version)
    wf().logger.debug("Latest : %r Installed : %r", vr, vl)
    if vr > vl:

        wf().cache_data(
            "__workflow_update_status",
            {"version": latest_release["version"], "download_url": latest_release["download_url"], "available": True},
        )

        return True

    wf().cache_data("__workflow_update_status", {"available": False})
    return False


def install_update():
    """If a newer release is available, download and install it.

    :returns: ``True`` if an update is installed, else ``False``

    """
    update_data = wf().cached_data("__workflow_update_status", max_age=0)

    if not update_data or not update_data.get("available"):
        wf().logger.info("No update available")
        return False

    local_file = download_workflow(update_data["download_url"])

    wf().logger.info("Installing updated workflow ...")
    subprocess.call(["open", local_file])

    update_data["available"] = False
    wf().cache_data("__workflow_update_status", update_data)
    return True


if __name__ == "__main__":  # pragma: nocover
    import sys

    def show_help():
        """Print help message."""
        print("Usage : update.py (check|install) github_slug version " "[--prereleases]")
        sys.exit(1)

    argv = sys.argv[:]
    prereleases = "--prereleases" in argv

    if prereleases:
        argv.remove("--prereleases")

    if len(argv) != 4:
        show_help()

    action, github_slug, version = argv[1:]

    if action not in ("check", "install"):
        show_help()

    if action == "check":
        check_update(github_slug, version, prereleases)
    elif action == "install":
        install_update()
