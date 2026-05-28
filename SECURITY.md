# Security Policy

`agent-continuity-layer` is a continuity substrate that runs as the operator's user, with access to their continuity memory (decision log, project registry, task queue) and — once wired via `connect` — to MCP-client configs and thin-skill installs in Claude/Codex/OpenClaw homes. The security surface that matters most is therefore: integrity of the released artifacts, soundness of the local trust model, and absence of unintended write authority.

## Reporting a vulnerability

**Please do NOT open a public issue for security findings.**

Use GitHub's Private Vulnerability Reporting:

→ <https://github.com/KING-MOM/agent-continuity-layer/security/advisories/new>

This creates a private advisory visible only to the project maintainer. You can include reproduction steps, affected versions, and proposed mitigations in the same thread.

If you cannot use GitHub Advisories for any reason, open a normal issue with the title `security: contact requested` and no details. The maintainer will respond with an out-of-band channel.

## What counts as a security issue

In scope:

- Anything that lets an attacker substitute a malicious release artifact while bypassing the documented integrity checks.
- Anything in the install / bootstrap / connect path that escalates privilege beyond the documented "writes as user, only to documented paths" model.
- Schema injection or path traversal in any of the six adapter operations (`whoami`, `read_context`, `read_decisions`, `append_decision`, `claim_task`, `submit_result`).
- Trust-policy bypasses: a request reaching enforcement-bearing code despite the policy rejecting it.
- Decision-log tampering that goes undetected by `doctor` checks.
- Adapter identity spoofing that defeats audit attribution beyond the descriptive identity model already documented.
- Any path by which the MCP stdio server, the bundle ingest path, or the Python SDK lets a caller escape the declared tool surface.

Out of scope (these are documented design limits, not bugs):

- Lack of cryptographic publisher identity for releases — see "Trust model" in `docs/trust-policy.md`. Signed releases are planned and tracked in the roadmap.
- Lack of multi-tenant isolation — v0.1.x assumes a single trusted operator.
- Lack of network-level adversary defense (the project has no network surface beyond optional VM sync over SSH that the operator configures).
- Any issue that requires existing root access on the operator's machine.

## Coordinated disclosure

We follow a **90-day coordinated disclosure window** by default, starting from the date of the initial private acknowledgement. If a fix is not yet shipped by day 90 and the reporter wants to publish, they are within their rights to do so. We may request a single extension of up to 30 days if a fix is materially in progress.

If a vulnerability is actively being exploited in the wild, the window may be shortened to whatever is operationally necessary.

## Response expectations (aspirational, not contractual)

This project is currently maintained by a single contributor. The following are best-effort targets, not service-level guarantees:

- **Acknowledgement:** within 72 hours of report submission.
- **Initial assessment** (severity, whether we accept the report as a security issue): within 7 days.
- **Fix timeline target by severity:**
  - Critical (remote code execution, install-path compromise): 30 days
  - High (privilege escalation, integrity bypass): 60 days
  - Medium (information disclosure, denial-of-service): 90 days
  - Low (defense-in-depth, hardening): best-effort, no commitment

These targets reflect what we *aim* to do. They are not warranties. The project ships under the MIT license, which explicitly disclaims warranty. If you need contractual security commitments, you need a vendor relationship that this OSS project does not currently offer.

## What we do NOT do

- We do not run a bug bounty program.
- We do not pay for vulnerability reports.
- We do not request that reporters sign NDAs.
- We do not attempt to identify researchers who report responsibly.

## CVE handling

For confirmed vulnerabilities of medium severity or above, we request a CVE through GitHub's CNA partnership (which is the default path when publishing a GitHub Security Advisory). The reporter is credited in the advisory unless they request anonymity.

We do not assign CVEs for low-severity findings or for defense-in-depth hardening. These ship in regular releases with a notation in the release notes.

## Supported versions

Only the latest `v0.1.x` minor is supported with security fixes at this time. Older minors are superseded; users on those versions are expected to upgrade. When the project reaches `v1.0` (criteria documented in `docs/versioning.md`), a formal LTS policy will be defined.

## What changed in our security posture recently

This file documents the project's current security stance honestly. The trust model has known limits (descriptive adapter identity, single-operator assumption). Some are tracked in `docs/threat-model.md` and `docs/roadmap.md`; the M15 release-integrity arc closed the remainder of the release-trust gaps.

## Verifying a release signature

Starting with **v0.2.0**, every GitHub Release artifact (tarball, sha256 file, SBOM, bootstrap.sh) is signed with [cosign](https://github.com/sigstore/cosign) using sigstore keyless OIDC. The signing identity is this repo's release workflow on the release tag; consumers verify by pinning to that identity.

Install cosign first (`brew install cosign` on macOS or see [sigstore.dev/install](https://docs.sigstore.dev/cosign/installation/)). Then:

```bash
VERSION=0.2.0
BASE="https://github.com/KING-MOM/agent-continuity-layer/releases/download/v${VERSION}"

# Download tarball + signature + certificate
curl -fsSL -o "agent-continuity-v${VERSION}.tar.gz"      "${BASE}/agent-continuity-v${VERSION}.tar.gz"
curl -fsSL -o "agent-continuity-v${VERSION}.tar.gz.sig"  "${BASE}/agent-continuity-v${VERSION}.tar.gz.sig"
curl -fsSL -o "agent-continuity-v${VERSION}.tar.gz.crt"  "${BASE}/agent-continuity-v${VERSION}.tar.gz.crt"

# Verify
cosign verify-blob \
  --certificate "agent-continuity-v${VERSION}.tar.gz.crt" \
  --signature "agent-continuity-v${VERSION}.tar.gz.sig" \
  --certificate-identity-regexp '^https://github\.com/KING-MOM/agent-continuity-layer/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  "agent-continuity-v${VERSION}.tar.gz"
```

Exit code 0 means the signature is valid and the artifact was produced by this repo's release workflow on a `v*` tag. Any other identity (different workflow file, different repo, non-tag ref) does not pass.

The same pattern applies to the other signed artifacts: `agent-continuity-v${VERSION}.sha256`, `agent-continuity-v${VERSION}.cdx.json`, and `bootstrap.sh`. Each has a sibling `.sig` and `.crt` on the release page.

The `bootstrap.sh` install path performs this verification automatically when cosign is available. The `install.sh` path performs it when `.sig`/`.crt` files are present alongside the tarball. Use `--no-verify` (in bootstrap only) to bypass in genuine emergencies; it is loud and logged.

Transparency: every signature is logged to the [Rekor transparency log](https://rekor.sigstore.dev/), so the audit trail extends beyond just the GitHub release page. Look up the signature's entry by feeding the `.sig` + `.crt` to `rekor-cli search`.

## Out-of-band contact

The maintainer's GitHub account is the canonical contact: <https://github.com/KING-MOM>

If you believe the maintainer's GitHub account itself is compromised, that is itself a security-relevant claim. Report it via GitHub Support or via any verified channel you have to the maintainer — and please be specific about why you believe so.
