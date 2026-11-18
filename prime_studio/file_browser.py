"""Read-only indexes and bounded text previews for explicitly registered files.

An index grants no solver or editor permission.  It contains only model-card
assets or files declared by an identity-matched draft manifest; no folder scan
or program execution is used to discover or open files.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .core import load_profile


DEFAULT_PREVIEW_BYTES = 131072
MAX_PREVIEW_BYTES = 1048576
MAX_METADATA_BYTES = 4194304
HASH = re.compile(r"[0-9a-fA-F]{64}")
BINARY_SUFFIXES = {
    ".dll", ".exe", ".obj", ".o", ".mod", ".pdb", ".lib", ".a", ".pyc",
    ".odb", ".npz", ".npy", ".dlt", ".zip", ".7z", ".gz", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".ico",
    ".xlsx", ".xls", ".docx", ".pptx", ".mp4", ".mp3", ".wav",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def _signature(index: dict[str, Any]) -> str:
    value = {key: item for key, item in index.items() if key != "_signature"}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _profile(profile: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    """Validate the card itself without hiding missing or changed asset rows."""
    if not isinstance(profile, dict) or not profile.get("_profile_path"):
        raise ValueError("Load the registered model card before browsing files")
    current = load_profile(Path(profile["_profile_path"]))
    public = {key: value for key, value in profile.items() if not key.startswith("_")}
    fresh = {key: value for key, value in current.items() if not key.startswith("_")}
    if (profile.get("_profile_sha256") != current["_profile_sha256"] or public != fresh
            or profile.get("_loaded_signature") != current["_loaded_signature"]):
        raise ValueError("Model card changed; reload it before browsing files")
    if not isinstance(profile.get("parent_root"), str) or not profile["parent_root"].strip():
        raise ValueError("Model card has no parent directory")
    files = profile.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Model card has no explicitly registered files")
    if not all(isinstance(name, str) for name in files):
        raise ValueError("Registered file names must be strings")
    return Path(profile["parent_root"]).resolve(), files


def _relative(name: str) -> str:
    if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid registered file name")
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if (normalized.startswith(("/", "@")) or ":" in normalized
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError("Registered file path is absolute or escapes its root")
    return PurePosixPath(normalized).as_posix()


def _inside(root: Path, name: str) -> Path:
    target = (root / _relative(name)).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("Registered file is a link outside its root")
    return target


def _role(relative: str, profile: dict[str, Any]) -> str:
    def same(value: Any) -> bool:
        return isinstance(value, str) and relative.casefold() == value.replace("\\", "/").casefold()
    if same(profile.get("source")):
        return "Fortran 源码"
    if same(profile.get("inp")):
        return "主 INP 输入"
    if relative == "draft_manifest.json":
        return "草稿文件清单"
    if relative == "statecontract.json":
        return "版本与范围记录"
    if relative == "changes.diff":
        return "父本／草稿差异"
    if relative.startswith("provenance/"):
        return "父版本证据"
    if relative in profile.get("includes", []):
        return "已登记依赖／外接文件"
    return "草稿登记工件"


def _item(root: Path, relative: str, expected: Any, role: str, *,
          external: Path | None = None) -> dict[str, Any]:
    row = {"relative": relative, "path": "", "role": role, "exists": False,
           "integrity": "unsafe", "status": "路径或登记无效", "size": None,
           "kind": "text", "sha256": expected, "openable": False,
           "external": external is not None}
    try:
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ValueError("登记 SHA-256 无效")
        row["sha256"] = expected.lower()
        if external is not None:
            row["registered_path"] = str(external.absolute())
        path = external.resolve() if external is not None else _inside(root, relative)
        row["path"] = str(path)
        row["kind"] = "binary" if path.suffix.casefold() in BINARY_SUFFIXES else "text"
        if not path.is_file():
            row.update(integrity="missing", status="文件缺失")
            return row
        row.update(exists=True, size=path.stat().st_size)
        if _sha(path) != expected.lower():
            row.update(integrity="mismatch", status="哈希不匹配")
            return row
        row.update(integrity="matched", status="已核对", openable=True)
    except (OSError, ValueError, RuntimeError) as error:
        row["status"] = "不可访问：" + str(error)
    return row


def _finish(kind: str, root: Path, profile: dict[str, Any], items: list[dict[str, Any]],
            guards: list[dict[str, str]], *, label: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = [f"{row['relative']}：{row['status']}" for row in items if row["integrity"] != "matched"]
    value = {"kind": kind, "root": str(root), "label": label, "items": items,
             "issues": issues, "matched": not issues, "execution_enabled": False,
             "profile_path": profile["_profile_path"], "profile_sha256": profile["_profile_sha256"],
             "_guards": guards,
             "scope": "Read-only file browsing; not solver, material or runtime qualification."}
    if extra:
        value.update(extra)
    value["_signature"] = _signature(value)
    return value


def model_index(profile: dict[str, Any]) -> dict[str, Any]:
    """List explicit registered assets, retaining missing and unsafe entries."""
    root, files = _profile(profile)
    items, seen = [], {}
    for raw_name, expected in files.items():
        try:
            name = _relative(raw_name)
        except ValueError:
            name = raw_name
        role = _role(name, profile)
        row = _item(root, name, expected, role)
        key = name.replace("\\", "/").casefold()
        if key in seen:
            row.update(integrity="unsafe", status="登记路径大小写冲突", openable=False)
            seen[key].update(integrity="unsafe", status="登记路径大小写冲突", openable=False)
        seen[key] = row
        items.append(row)
    initializer = profile.get("initializer", {})
    for name, label in (("dll", "初始化 DLL（只显示信息）"), ("material", "初始化材料文件")):
        asset = initializer.get(name) if isinstance(initializer, dict) else None
        if asset is not None:
            if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
                items.append(_item(root, "@initializer/" + name, None, label))
                continue
            path = Path(asset["path"])
            if not path.is_absolute():
                items.append(_item(root, "@initializer/" + name, None, label))
                continue
            items.append(_item(root, "@initializer/" + name, asset.get("sha256"), label, external=path))
    guards = [{"path": profile["_profile_path"], "sha256": profile["_profile_sha256"]}]
    return _finish("model", root, profile, items, guards, label="原模型（只读）")


def _json(path: Path) -> Any:
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("Draft identity metadata is too large")
    def reject(value: str) -> None:
        raise ValueError("Non-finite draft metadata: " + value)
    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            reject(value)
        return result
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate draft metadata key: " + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject,
                      parse_float=finite, object_pairs_hook=pairs)


def _hashes(values: Any) -> dict[str, str]:
    if not isinstance(values, dict) or not values:
        raise ValueError("Draft has no registered file hashes")
    result, folded = {}, set()
    for name, value in values.items():
        normalized = _relative(name)
        if normalized.casefold() in folded or not isinstance(value, str) or not HASH.fullmatch(value):
            raise ValueError("Invalid or duplicate draft file hash: " + str(name))
        folded.add(normalized.casefold())
        result[normalized] = value.lower()
    return result


def _same_path(value: Any, expected: Path) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and Path(value).is_absolute()
            and Path(value).resolve() == expected)


def draft_index(profile: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Associate a generated draft with the exact loaded model-card identity.

    Identity metadata must be intact before it can authorize artifact paths.
    Ordinary missing or changed artifacts remain visible as blocked tree rows.
    """
    parent, files = _profile(profile)
    root = Path(directory).resolve()
    if not root.is_dir() or root == parent:
        raise ValueError("Select a generated draft directory, separate from its parent")
    manifest_path = _inside(root, "draft_manifest.json")
    manifest_sha = _sha(manifest_path)
    manifest = _json(manifest_path)
    if (not isinstance(manifest, dict) or manifest.get("status") != "DRAFT_GENERATED"
            or manifest.get("execution_enabled") is not False):
        raise ValueError("Selected directory is not a generated, execution-disabled draft")
    if (not _same_path(manifest.get("case"), root)
            or not _same_path(manifest.get("immutable_parent"), parent)
            or manifest.get("model_card_sha256") != profile["_profile_sha256"]):
        raise ValueError("Draft model card, parent or directory identity differs from the current model")
    artifacts = _hashes(manifest.get("artifacts_sha256"))
    if "statecontract.json" not in artifacts:
        raise ValueError("Draft is missing its hashed statecontract identity")
    state_path = _inside(root, "statecontract.json")
    if _sha(state_path) != artifacts["statecontract.json"]:
        raise ValueError("Draft statecontract identity was changed")
    state = _json(state_path)
    parent_hashes = _hashes(files)
    if (not isinstance(state, dict) or state.get("execution_enabled") is not False
            or not _same_path(state.get("immutable_parent"), parent)
            or _hashes(state.get("parent_sha256")) != parent_hashes):
        raise ValueError("Draft parent file hashes differ from the registered model")
    required = {"provenance/parent_statecontract.json" if name == "statecontract.json" else name
                for name in parent_hashes}
    source = _relative(profile["source"])
    if not required.issubset(artifacts) or artifacts.get(source) != parent_hashes.get(source):
        raise ValueError("Draft source or registered dependency coverage differs from the current model")
    items = [_item(root, name, expected, _role(name, profile)) for name, expected in artifacts.items()]
    items.append(_item(root, "draft_manifest.json", manifest_sha, "草稿文件清单"))
    if _sha(manifest_path) != manifest_sha or _sha(state_path) != artifacts["statecontract.json"]:
        raise ValueError("Draft identity changed while indexing")
    guards = [{"path": profile["_profile_path"], "sha256": profile["_profile_sha256"]},
              {"path": str(manifest_path), "sha256": manifest_sha},
              {"path": str(state_path), "sha256": artifacts["statecontract.json"]}]
    source_matched = any(row["relative"] == source and row["integrity"] == "matched" for row in items)
    return _finish("draft", root, profile, items, guards, label="草稿：" + root.name,
                   extra={"manifest": str(manifest_path), "runtime_result": manifest.get("runtime_result", "not_run"),
                          "immutable_parent": str(parent), "source_byte_identical": source_matched})


def resolve_file(index: dict[str, Any], relative: str) -> Path:
    """Return only an intact indexed file; callers must never execute it."""
    if not isinstance(index, dict) or index.get("_signature") != _signature(index):
        raise ValueError("File index changed; refresh the file list")
    for guard in index.get("_guards", []):
        path = Path(guard["path"])
        if not path.is_file() or _sha(path) != guard["sha256"]:
            raise ValueError("File-list identity changed; refresh the file list")
    matches = [row for row in index["items"] if row["relative"] == relative]
    if len(matches) != 1:
        raise ValueError("Select one explicitly indexed file")
    row = matches[0]
    if row["integrity"] != "matched" or row.get("openable") is not True:
        raise ValueError("Cannot open a missing, changed or unsafe registered file")
    root = Path(index["root"])
    if root.resolve() != root:
        raise ValueError("Model directory was redirected; refresh the file list")
    path = Path(row["path"])
    if row.get("external") and Path(row["registered_path"]).resolve() != path:
        raise ValueError("Registered initializer path was redirected; refresh the file list")
    current = path.resolve() if row.get("external") else _inside(root, relative)
    if current != path or not current.is_file() or _sha(current) != row["sha256"]:
        raise ValueError("Selected file changed or moved; refresh the file list")
    return current


def read_text_preview(index: dict[str, Any], relative: str,
                      max_bytes: int = DEFAULT_PREVIEW_BYTES) -> dict[str, Any]:
    """Read a bounded text prefix without launching an editor or any program."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("Text preview size must be a positive integer")
    limit = min(max_bytes, MAX_PREVIEW_BYTES)
    path = resolve_file(index, relative)
    row = next(item for item in index["items"] if item["relative"] == relative)
    result = {"relative": relative, "path": str(path), "size": path.stat().st_size,
              "text": "", "encoding": None, "truncated": False, "limit_bytes": limit}
    if row["kind"] == "binary":
        return {**result, "status": "BINARY_UNAVAILABLE", "reason": "二进制文件仅显示身份与位置，不执行或作为文本打开。"}
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    truncated = len(data) > limit
    prefix = data[:limit]
    if b"\x00" not in prefix:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = codecs.getincrementaldecoder(encoding)(errors="strict").decode(prefix, final=not truncated)
                resolve_file(index, relative)
                return {**result, "status": "TEXT_PREVIEW", "text": text,
                        "encoding": encoding, "truncated": truncated}
            except UnicodeDecodeError:
                continue
    resolve_file(index, relative)
    return {**result, "status": "BINARY_UNAVAILABLE", "reason": "文件包含二进制内容或不支持的文本编码，未打开。"}
