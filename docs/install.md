# Install

Two install paths. Both ship the same substrate, both verify integrity the same way; they differ only in how much you eyeball before code runs.

## One-liner (recommended)

Two variants. Pick based on whether you want bootstrap to also wire your local agents.

**Install + wire local agents (one command):**

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash -s -- --connect-all
```

**Install only, wiring stays explicit (conservative default):**

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash
# then, when you're ready:
agent-continuity connect all --apply
```

The bootstrap script:

1. Queries the GitHub API for the latest release tag.
2. Downloads `agent-continuity-v{X.Y.Z}.tar.gz` and its sibling `.sha256`.
3. Verifies the tarball against the sha256 (cross-platform: `shasum -a 256 -c` or `sha256sum -c`).
4. Aborts on mismatch — nothing is installed.
5. Extracts to a tempdir, runs `install.sh`, deletes the tempdir.
6. If `--connect-all` was passed: runs `agent-continuity connect all --apply` to wire Claude Desktop / Cursor / Zed / thin skills. On connect failure, install is still complete and bootstrap exits 0 with a warning pointing at `connect doctor`.
7. Otherwise: prints next-steps for `agent-continuity connect doctor` and `connect all --apply`.

The install step itself writes only to:
- `$XDG_DATA_HOME/agent-continuity/v{X.Y.Z}/` (the substrate code)
- `$XDG_DATA_HOME/agent-continuity/active` (symlink to current version)
- `$HOME/.local/bin/agent-continuity` (PATH shim)

It does NOT touch `~/.config/agent-continuity/`, `~/.cache/agent-continuity/`, `~/.local/state/agent-continuity/`. Without `--connect-all`, it also doesn't touch any MCP-client config files — wiring is a separate, explicit step.

`--connect-all` is opt-in by design. Writing into Claude Desktop, Cursor, and Zed configs is a real side effect on third-party files the operator owns. Making it explicit means `curl … | bash` never silently edits those configs unless the user typed the flag.

## Using Claude Code or Codex CLI

Shell-capable agents can run the one-liner for you. Tell them:

> Install agent-continuity-layer from https://github.com/KING-MOM/agent-continuity-layer

They'll fetch the bootstrap and run it the same way you would manually.

GUI MCP clients (Claude Desktop, Cursor, Zed) cannot install agent-continuity themselves — they can only call tools you've already wired in, and the install IS the prerequisite for wiring. Use the one-liner from a shell.

## Manual tarball install

If you'd rather see and verify each step before any code runs:

```bash
# 1. Pick a release version; the latest is shown on the GitHub releases page
VERSION=0.1.5

# 2. Download tarball and checksum
curl -L -o "agent-continuity-v${VERSION}.tar.gz" \
  "https://github.com/KING-MOM/agent-continuity-layer/releases/download/v${VERSION}/agent-continuity-v${VERSION}.tar.gz"
curl -L -o "agent-continuity-v${VERSION}.sha256" \
  "https://github.com/KING-MOM/agent-continuity-layer/releases/download/v${VERSION}/agent-continuity-v${VERSION}.sha256"

# 3. Verify integrity (macOS uses shasum, Linux often has sha256sum)
shasum -a 256 -c "agent-continuity-v${VERSION}.sha256"
# or: sha256sum -c "agent-continuity-v${VERSION}.sha256"

# 4. Extract
tar -xzf "agent-continuity-v${VERSION}.tar.gz"

# 5. Inspect what install.sh would do (optional but encouraged)
less "agent-continuity-v${VERSION}/scripts/install.sh"

# 6. Run the install
"agent-continuity-v${VERSION}/scripts/install.sh" --from-tarball "agent-continuity-v${VERSION}.tar.gz"
```

After install:

```bash
agent-continuity --version
agent-continuity doctor --human
```

## Trade-off: bootstrap vs manual

The integrity check is identical: both paths download the same `.sha256` file and verify the same tarball against it.

What the manual path adds is **the chance to inspect each artifact before it runs on your machine**. You can read the bootstrap script. You can read `install.sh` inside the extracted tarball. You can grep the python sources for anything suspicious.

What `curl … | bash` loses is exactly that inspection window. If you trust the publisher of the repo (and the GitHub release infrastructure), the practical security level is the same as manual install with `shasum -c`. If you don't, the manual path lets you stop at any step.

Either way, the substrate is honest: the sha256 detects transport corruption, not publisher identity. An attacker who can rewrite the tarball over the same transport can rewrite the bootstrap script and `.sha256` too. Signed releases are a future trust milestone; we do not pretend otherwise.

## Verifying you built from source the same artifact GitHub serves

Starting with v0.1.7 the substrate ships **reproducible builds**: rebuilding the tarball at the same commit produces a byte-identical `.tar.gz`. That means you can independently verify GitHub is serving the same artifact the publisher built.

Recipe:

```bash
# 1. Clone at the release tag
git clone --depth 1 --branch v0.1.7 https://github.com/KING-MOM/agent-continuity-layer.git
cd agent-continuity-layer

# 2. Build locally
./scripts/release.sh build

# 3. Compute the local sha256
shasum -a 256 dist/agent-continuity-v0.1.7.tar.gz

# 4. Fetch GitHub's published sha256
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/download/v0.1.7/agent-continuity-v0.1.7.sha256

# 5. Compare. The two sha256 values MUST be identical.
```

If they differ, do not install. Report via GitHub Private Vulnerability Reporting (`SECURITY.md`).

How the determinism works:
- All tar entries get `SOURCE_DATE_EPOCH` as their mtime (derived from `git log -1 --format=%ct HEAD`).
- Owner/group set to uid=0, gid=0, empty uname/gname.
- File modes normalized to `0644` / `0755` based on git's stored mode.
- Gzip header has no embedded filename and no original mtime.

You can override `SOURCE_DATE_EPOCH` to test against an alternate epoch:

```bash
SOURCE_DATE_EPOCH=1700000000 ./scripts/release.sh build
```

But for matching a published release, leave it unset — the script will derive it from the tagged commit, which is what the publisher used.

## SBOM (Software Bill of Materials)

Starting with v0.1.9 each release ships a [CycloneDX 1.5](https://cyclonedx.org/) JSON SBOM alongside the tarball + sha256:

```
agent-continuity-v0.1.9.tar.gz
agent-continuity-v0.1.9.sha256
agent-continuity-v0.1.9.cdx.json    ← SBOM
bootstrap.sh
```

The SBOM declares:
- The application: name, version, MIT license, purl `pkg:github/KING-MOM/agent-continuity-layer@vX.Y.Z`
- Runtime dependencies: bash and python3 (≥ 3.9), both stdlib-scope. Zero PyPI / npm dependencies.
- Cryptographic binding: the main component's `hashes[].content` field carries the tarball's sha256, so the SBOM is verifiably tied to a specific artifact.
- Deterministic serial number: same version + commit → same `serialNumber`. The SBOM is reproducible byte-for-byte.

Consume with standard SBOM tooling:

```bash
# Fetch the SBOM
curl -fsSL -o sbom.cdx.json https://github.com/KING-MOM/agent-continuity-layer/releases/download/v0.1.9/agent-continuity-v0.1.9.cdx.json

# Inspect with any CycloneDX-aware tool, e.g. Syft, Trivy, Grype, Anchore
trivy sbom sbom.cdx.json
# or just jq:
jq '.metadata.component, .components' sbom.cdx.json
```

Why this matters: enterprise procurement / supply-chain reviews often require an SBOM in CycloneDX or SPDX format. The substrate provides CycloneDX 1.5 by default; SPDX conversion is one `cyclonedx-cli convert` away if your tooling specifically requires SPDX.

The SBOM is generated by `scripts/_sbom.py` from the substrate itself — no external SBOM-generation tools required. Reproducibility is preserved: rebuilding at the same commit produces a byte-identical SBOM (verified by the `t_sbom_present_and_valid` test in `scripts/release-smoke.sh`).

## After install: PATH

The install lands the CLI at `$HOME/.local/bin/agent-continuity` (or `$XDG_BIN_HOME/agent-continuity` if you've set that). If `~/.local/bin` is not on your `$PATH`, the install prints a note saying so.

Quick add for zsh/bash:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## After install: wiring local agents

The substrate is installed but no MCP client knows about it yet. To wire Claude Desktop, Cursor, Zed, and thin skills for Claude/Codex/OpenClaw in one step:

```bash
agent-continuity connect doctor       # preview, no writes
agent-continuity connect all --apply  # write configs, with backups
```

Per-host targets exist for fine-grained wiring:

```bash
agent-continuity connect claude-desktop --apply
agent-continuity connect cursor --apply
agent-continuity connect zed --apply
agent-continuity connect codex --apply
```

Details and config snippets per client: [docs/mcp-integration.md](mcp-integration.md) and [docs/connect.md](connect.md).

## Troubleshooting

- **`agent-continuity: command not found`** — `~/.local/bin` is not on your PATH. Either add it (above) or open a new shell after install.
- **`shasum: command not found`** — on Linux, use `sha256sum -c` instead. On macOS, `shasum` ships with the system.
- **Bootstrap fails at the GitHub API step** — the repo must be reachable from your network. Manual tarball install works through any HTTPS reachability.
- **Claude Desktop doesn't see the tools after `connect claude-desktop --apply`** — restart Claude Desktop. macOS GUI apps cache config aggressively.
- **GUI MCP clients fail to launch `agent-continuity`** — macOS app sandboxes often strip `~/.local/bin` from PATH. Use the absolute path in the MCP config (the `connect` writers handle this automatically, but if you wired manually, update to the resolved path from `which agent-continuity`).

## Uninstall

The substrate is single-user and lives entirely under your home dir.

```bash
rm "$HOME/.local/bin/agent-continuity"             # PATH shim
rm -rf "$HOME/.local/share/agent-continuity"        # install dirs + active symlink
# Optional: also remove memory
rm -rf "$HOME/.config/agent-continuity"             # trust policy, registry
rm -rf "$HOME/.local/state/agent-continuity"        # decisions log
rm -rf "$HOME/.cache/agent-continuity"              # worker queue
```

For Claude Desktop / Cursor / Zed, `connect` adds an `agent-continuity` entry to their config files; remove it manually by editing those configs (`connect` does not currently emit an `--unwire` flag).
