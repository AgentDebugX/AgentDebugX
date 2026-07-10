"""Download and unzip the optional Claude 50-step input trajectories.

Default source:
https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs/blob/main/claude-sonnet-4-5-20250929_50steps.zip
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_BLOB_URL = (
    "https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs/"
    "blob/main/claude-sonnet-4-5-20250929_50steps.zip"
)
DEFAULT_OUTPUT_DIR = Path("results/input_trajectory")
DEFAULT_ARCHIVE_NAME = "claude-sonnet-4-5-20250929_50steps.zip"


def huggingface_blob_to_resolve_url(url: str) -> str:
    """Convert a Hugging Face file UI URL into a direct download URL."""
    return url.replace("/blob/", "/resolve/")


def _is_within_directory(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _windows_long_path(path: Path) -> str:
    """Return a Windows extended-length path when needed."""
    resolved = str(Path(path).resolve(strict=False))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _zip_member_target(output_dir: Path, member_name: str) -> Path:
    """Map a zip member name to a safe local target path."""
    raw_target = output_dir / member_name
    if not _is_within_directory(output_dir, raw_target):
        raise ValueError(f"Unsafe zip member path: {member_name}")

    parts = [p for p in member_name.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"Unsafe zip member path: {member_name}")

    if os.name == "nt":
        illegal = ':<>|"?*'
        table = str.maketrans(illegal, "_" * len(illegal))
        parts = [p.translate(table).rstrip(".") for p in parts]

    target = output_dir.joinpath(*parts) if parts else output_dir
    if not _is_within_directory(output_dir, target):
        raise ValueError(f"Unsafe zip member path: {member_name}")
    return target


def safe_extract_zip(archive_path: Path, output_dir: Path) -> list[Path]:
    """Extract a zip file while rejecting members that escape output_dir."""
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(archive_path) as zf:
        for member in zf.infolist():
            target = _zip_member_target(output_dir, member.filename)
            if member.is_dir():
                os.makedirs(_windows_long_path(target), exist_ok=True)
                continue

            os.makedirs(_windows_long_path(target.parent), exist_ok=True)
            with zf.open(member) as src, open(_windows_long_path(target), "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            extracted.append(target)
    return extracted


def download_file(url: str, archive_path: Path, *, force: bool = False) -> Path:
    """Stream url to archive_path without loading the archive into memory."""
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_path.exists() and not force:
        print(f"Archive already exists, skipping download: {archive_path}")
        return archive_path

    part_path = archive_path.with_suffix(archive_path.suffix + ".part")
    if part_path.exists():
        part_path.unlink()

    request = urllib.request.Request(
        huggingface_blob_to_resolve_url(url),
        headers={"User-Agent": "CUA-Agent-Debugger-downloader/0.1"},
    )

    try:
        with urllib.request.urlopen(request) as response, part_path.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            copied = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                copied += len(chunk)
                if total:
                    pct = copied * 100 / total
                    print(f"\rDownloading {pct:5.1f}% ({copied / (1024 ** 3):.2f} GiB)", end="")
            if total:
                print()
    except urllib.error.URLError as exc:
        if part_path.exists():
            part_path.unlink()
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc

    part_path.replace(archive_path)
    return archive_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and unzip optional CUA-Agent-Debugger input trajectories."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_BLOB_URL,
        help="Hugging Face blob or resolve URL for the trajectory zip.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where the zip contents will be extracted.",
    )
    parser.add_argument(
        "--archive-path",
        type=Path,
        default=None,
        help="Where to store the downloaded zip. Defaults to <output-dir>/<zip-name>.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the archive already exists.",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Only download the zip; do not unzip it.",
    )
    parser.add_argument(
        "--delete-zip",
        action="store_true",
        help="Delete the zip after a successful extraction.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    archive_path = args.archive_path or (args.output_dir / DEFAULT_ARCHIVE_NAME)

    print(f"Source: {huggingface_blob_to_resolve_url(args.url)}")
    print(f"Archive: {archive_path}")
    print(f"Output directory: {args.output_dir}")
    print("Note: the default archive is about 5.38 GB before extraction.")

    try:
        archive_path = download_file(args.url, archive_path, force=args.force)
        if not args.no_extract:
            print(f"Extracting to {args.output_dir} ...")
            extracted = safe_extract_zip(archive_path, args.output_dir)
            print(f"Extracted {len(extracted)} files.")
        if args.delete_zip and archive_path.exists():
            archive_path.unlink()
            print(f"Deleted archive: {archive_path}")
    except (RuntimeError, ValueError, zipfile.BadZipFile, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
