# Releasing Local Board

Local Board publishes to PyPI from GitHub Actions using a PyPI Trusted
Publisher. The workflow does not use a long-lived PyPI API token.

The initial PyPI project may be created from a maintainer's personal PyPI
account; it does not need a PyPI organization. Project ownership and automated
publishing are separate: add another trusted person as a PyPI project owner for
recovery, and let the GitHub workflow perform routine releases.

## One-time PyPI setup

Before the first release, create a pending Trusted Publisher for the
`local-board` project in PyPI with these values:

- **PyPI project name:** `local-board`
- **GitHub owner:** the account or organization that owns the repository
- **Repository name:** `local-board`
- **Workflow filename:** `publish-pypi.yml`
- **Environment name:** `pypi`

For an existing PyPI project, add the same publisher under the project's
publishing settings instead. The owner and repository values must match the
current GitHub location exactly.

Create a GitHub environment named `pypi`. Configure required reviewers on the
environment if releases should require a manual approval. No PyPI password or
API token should be stored in GitHub.

## Publish a release

1. Update the version in `pyproject.toml` and `local_board/__init__.py`.
2. Run the complete test suite.
3. Commit and merge the version change.
4. Create a GitHub release whose tag is the version prefixed with `v`, such as
   `v0.0.1`.
5. Publish the GitHub release.

Publishing the GitHub release starts `.github/workflows/publish-pypi.yml`. The
workflow verifies that the tag and package version match, runs the test suite,
builds both a source distribution and a wheel, and exchanges GitHub's OIDC
identity for short-lived PyPI publishing credentials.

PyPI release files are immutable. If publishing fails after PyPI accepts
version `0.0.1`, increment the package version before retrying; do not reuse the
same version number.

## Move the repository to a GitHub organization

The first release may be published from a repository in a personal GitHub
account and the repository may be transferred to an organization later. The
PyPI project name and published versions do not change, and this workflow has no
hard-coded GitHub owner.

PyPI matches a Trusted Publisher against the repository's current GitHub owner,
repository name, workflow filename, and environment. After transferring the
repository, update the trust relationship before publishing another release:

1. Verify that the transferred repository still has a GitHub environment named
   `pypi`, including any required-reviewer protection rules.
2. In the `local-board` PyPI project's publishing settings, add a new Trusted
   Publisher with the organization as **GitHub owner** and keep the repository,
   workflow, and environment values unchanged.
3. Publish the next release from the transferred repository and confirm that
   trusted publishing succeeds.
4. Remove the old publisher that names the personal GitHub account.

Transferring the GitHub repository does not require transferring the PyPI
project to a PyPI organization. A maintainer's personal PyPI account may remain
the project owner while the GitHub organization workflow publishes releases.
