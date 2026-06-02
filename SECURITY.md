# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report vulnerabilities privately through GitHub's "Report a vulnerability"
button under the repository's **Security** tab (Security Advisories). This
opens a private channel visible only to the maintainers.

When reporting, please include:

- A description of the issue and the impact you think it has
- Steps to reproduce, or a proof of concept if you have one
- The affected component (controller API, browser proxy, worker, desktop SPA, etc.)
- Any relevant logs or configuration (with secrets redacted)

## What to expect

- An acknowledgement within a few days
- An assessment of severity and scope, and follow-up questions if needed
- A fix or mitigation plan, with credit to the reporter if you would like it
- Coordinated disclosure once a fix is available

Please give us reasonable time to address the issue before disclosing it
publicly.

## Supported versions

taOS is developed on `master`, which is what running installs track. Fixes
land on `master`; there is no separate long-term support branch. Please test
against the latest `master` before reporting.

## Scope

In scope:

- The controller (FastAPI) and its HTTP/WebSocket APIs
- The browser proxy and the live browser worker
- Agent deployment and container handling
- Authentication, session handling, and the SSRF guards
- The desktop SPA

Out of scope:

- Findings that require a pre-compromised host or physical access
- Denial of service from unrealistic traffic volumes
- Vulnerabilities in third-party dependencies that are already public and
  have an upstream fix (open a normal dependency-bump PR instead)
- Issues only reproducible on unsupported, modified, or end-of-life setups
