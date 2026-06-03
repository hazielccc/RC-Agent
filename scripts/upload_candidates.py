#!/usr/bin/env python3
"""List and mark files for incremental make upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".webp",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".txt",
    ".md",
}

MANIFEST_PATH = Path("rag_anything_storage/make_upload_manifest.json")
DOC_STATUS_PATH = Path("rag_anything_storage/kv_store_doc_status.json")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {
        "sha256": file_sha256(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def is_system_file(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    return (
        name.startswith("._")
        or name == ".DS_Store"
        or name.startswith("~$")
        or "__MACOSX" in parts
    )


def iter_supported_files(roots: list[Path]):
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix.lower() in SUPPORTED_EXTENSIONS
                and not is_system_file(path)
            ):
                yield path


def read_text_length(path: Path) -> int | None:
    if path.suffix.lower() not in {".txt", ".md"}:
        return None
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return len(raw.decode(encoding).strip())
        except UnicodeDecodeError:
            continue
    return None


def has_legacy_processed_status(path: Path, doc_status: dict) -> bool:
    text_length = read_text_length(path)
    if text_length is None:
        return False

    for value in doc_status.values():
        if value.get("status") != "processed":
            continue
        if Path(str(value.get("file_path", ""))).name != path.name:
            continue
        if value.get("content_length") == text_length:
            return True
    return False


def should_upload(path: Path, manifest: dict, doc_status: dict) -> bool:
    resolved = str(path.resolve())
    current = fingerprint(path)
    previous = manifest.get(resolved)
    if previous and all(previous.get(key) == current[key] for key in current):
        return False
    return not has_legacy_processed_status(path, doc_status)


def list_candidates(args: argparse.Namespace) -> int:
    roots = [Path(root) for root in args.roots]
    if args.force:
        for path in iter_supported_files(roots):
            print(path.as_posix())
        return 0

    manifest = load_json(MANIFEST_PATH)
    doc_status = load_json(DOC_STATUS_PATH)
    for path in iter_supported_files(roots):
        if should_upload(path, manifest, doc_status):
            print(path.as_posix())
    return 0


def mark_uploaded(args: argparse.Namespace) -> int:
    path = Path(args.file).resolve()
    if not path.exists() or not path.is_file():
        print(f"文件不存在: {args.file}", file=sys.stderr)
        return 1

    manifest = load_json(MANIFEST_PATH)
    manifest[str(path)] = {
        **fingerprint(path),
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(MANIFEST_PATH, manifest)
    return 0


def unmark_uploaded(args: argparse.Namespace) -> int:
    path = Path(args.file).resolve()
    manifest = load_json(MANIFEST_PATH)
    manifest.pop(str(path), None)
    save_json(MANIFEST_PATH, manifest)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--force", action="store_true", help="忽略指纹缓存，列出全部支持文件")
    list_parser.add_argument("roots", nargs="+")
    list_parser.set_defaults(func=list_candidates)

    mark_parser = subparsers.add_parser("mark")
    mark_parser.add_argument("file")
    mark_parser.set_defaults(func=mark_uploaded)

    unmark_parser = subparsers.add_parser("unmark")
    unmark_parser.add_argument("file")
    unmark_parser.set_defaults(func=unmark_uploaded)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
