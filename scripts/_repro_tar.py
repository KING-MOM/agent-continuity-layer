#!/usr/bin/env python3
"""_repro_tar.py — M15.1 deterministic tar.gz builder.

Reads a file list from stdin (one path per line, relative to source-dir),
produces a tarball whose sha256 is reproducible across:
  - macOS (BSD tar lacks --sort, --mtime semantics differ)
  - GNU/Linux (different defaults)
  - re-runs on the same machine (no fs-time leak, no gzip-name leak)

Why Python + tarfile instead of `tar --sort=name --mtime=...`:
  - Cross-platform: BSD tar and GNU tar differ enough that scripting a
    "works everywhere" invocation is fragile. Python's tarfile + gzip
    give explicit control over every byte that goes into the archive.
  - The substrate already requires python3 stdlib; zero new dependency.

Reproducibility recipe (honoring https://reproducible-builds.org):
  - All entries timestamped with SOURCE_DATE_EPOCH (env, or --epoch).
  - All entries owned by uid=0, gid=0, with empty uname/gname.
  - Mode normalized to 0644 for non-executable, 0755 for executable
    (decided per-file by the caller, passed via the file-list format).
  - File list is sorted by the caller before being piped in; we do
    NOT re-sort (operator decides canonical order).
  - gzip header has no filename, no comment, no original mtime.

File list format (stdin, one entry per line):
    {mode_octal}\\t{path_relative_to_source}
e.g.:
    100644\\tREADME.md
    100755\\tscripts/install.sh
The mode octal is git's stored mode (`git ls-files -s` column 1).
Only the executable bit is used; the rest is normalized.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import pathlib
import sys
import tarfile


def _normalize_mode(git_mode: str) -> int:
    """Map git's stored mode to a normalized tar mode.

    git uses six-digit octal: 100644 for non-executable regular file,
    100755 for executable. We honor the executable bit and discard
    everything else (setuid/setgid never appear in a sane git tree,
    and we wouldn't want to ship them anyway)."""
    try:
        m = int(git_mode, 8)
    except ValueError:
        return 0o644
    # Anything with any executable bit set → 0755; otherwise → 0644.
    if m & 0o111:
        return 0o755
    return 0o644


def build(
    source_dir: pathlib.Path,
    output_path: pathlib.Path,
    prefix: str,
    file_entries: list[tuple[str, str]],
    epoch: int,
) -> None:
    """Build the deterministic tar.gz from file_entries.

    file_entries: list of (mode_octal, relative_path) tuples in the
    EXACT order they should appear in the archive. Caller is
    responsible for sorting; we preserve order.
    """
    # Write the inner tar stream into a BytesIO buffer first so we can
    # then gzip it with explicit, deterministic gzip headers. Doing the
    # gzip step ourselves is the only way to suppress the filename and
    # mtime in the gzip header (tarfile's `w:gz` mode embeds them).
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        # Add a directory entry for the prefix so the archive cleanly
        # extracts into one top-level folder. Reproducible-builds
        # convention: directories also get the canonical metadata.
        dir_info = tarfile.TarInfo(name=prefix)
        dir_info.type = tarfile.DIRTYPE
        dir_info.mode = 0o755
        dir_info.mtime = epoch
        dir_info.uid = 0
        dir_info.gid = 0
        dir_info.uname = ""
        dir_info.gname = ""
        tar.addfile(dir_info)

        for mode_octal, rel_path in file_entries:
            abs_path = source_dir / rel_path
            if not abs_path.is_file():
                # Symlinks / submodules etc. — out of scope for v0.1.x
                # substrate. Skip with a stderr note so the operator
                # notices if the file list ever contains something
                # unexpected.
                print(
                    f"warn: skipping non-file entry: {rel_path}",
                    file=sys.stderr,
                )
                continue

            data = abs_path.read_bytes()
            info = tarfile.TarInfo(name=f"{prefix}/{rel_path}")
            info.size = len(data)
            info.mode = _normalize_mode(mode_octal)
            info.mtime = epoch
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))

    tar_bytes = tar_buf.getvalue()

    # Now gzip with explicit empty filename and mtime=0. GzipFile with
    # filename="" and a constructor that suppresses os.path.basename
    # leakage: easiest path is to open the underlying file ourselves
    # and pass it to GzipFile, then GzipFile won't auto-embed any name.
    with output_path.open("wb") as raw:
        with gzip.GzipFile(
            filename="",  # do not embed a filename in the gzip header
            mode="wb",
            fileobj=raw,
            mtime=0,  # do not embed an original mtime in the gzip header
            compresslevel=9,  # max compression; deterministic given the same input
        ) as gz:
            gz.write(tar_bytes)


def parse_file_list(text: str) -> list[tuple[str, str]]:
    """Parse the tab-separated file list (one entry per line)."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line:
            continue
        if "\t" not in line:
            raise ValueError(f"bad file-list line (no tab): {line!r}")
        mode_octal, rel_path = line.split("\t", 1)
        if not rel_path:
            raise ValueError(f"bad file-list line (empty path): {line!r}")
        entries.append((mode_octal.strip(), rel_path.strip()))
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "M15.1 deterministic tar.gz builder. Reads a tab-separated "
            "file list on stdin and produces a reproducible tarball."
        )
    )
    ap.add_argument(
        "--source-dir",
        required=True,
        help="root directory paths in the file list are relative to",
    )
    ap.add_argument(
        "--output",
        required=True,
        help="path to write the output .tar.gz",
    )
    ap.add_argument(
        "--prefix",
        required=True,
        help=(
            "top-level directory inside the archive "
            "(e.g. 'agent-continuity-v0.1.7')"
        ),
    )
    ap.add_argument(
        "--epoch",
        type=int,
        default=None,
        help=(
            "SOURCE_DATE_EPOCH (UNIX seconds) for all entry mtimes. "
            "Falls back to the SOURCE_DATE_EPOCH env var; finally to 0."
        ),
    )
    args = ap.parse_args()

    epoch = args.epoch
    if epoch is None:
        env_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        if env_epoch:
            try:
                epoch = int(env_epoch)
            except ValueError:
                print(
                    f"error: SOURCE_DATE_EPOCH not an integer: {env_epoch!r}",
                    file=sys.stderr,
                )
                return 1
    if epoch is None:
        epoch = 0

    source_dir = pathlib.Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        print(f"error: source-dir not a directory: {source_dir}", file=sys.stderr)
        return 1

    output_path = pathlib.Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        entries = parse_file_list(sys.stdin.read())
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not entries:
        print("error: no file entries on stdin", file=sys.stderr)
        return 1

    build(
        source_dir=source_dir,
        output_path=output_path,
        prefix=args.prefix,
        file_entries=entries,
        epoch=epoch,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
