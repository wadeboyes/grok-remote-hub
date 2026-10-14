"""Sideload app updates served by the hub.

The phone and tablet read ``data/app-updates/<channel>/update.json`` and the
APK next to it. Those files stay out of git. ``/health`` is still the only
route that skips ``hub_token``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from hub.config import PROJECT_ROOT

CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,32}$")
APK_NAME_RE = re.compile(r"^GrokRemote-[0-9A-Za-z._+-]+\.apk$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_VERSION_CODE_ID = 0x0101021B
_VERSION_NAME_ID = 0x0101021C
_TYPE_STRING = 0x03


class UpdateError(Exception):
    """A publish or manifest problem the caller can show."""


class UpdateManifestError(UpdateError):
    """``update.json`` exists but is not a usable manifest."""


def updates_root(project_root: Path | None = None) -> Path:
    root = PROJECT_ROOT if project_root is None else project_root
    return root / "data" / "app-updates"


def sanitize_channel(channel: str | None) -> str | None:
    text = (channel or "").strip().lower()
    if not CHANNEL_RE.fullmatch(text):
        return None
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(root: Path, channel: str) -> dict | None:
    """Return the channel manifest, or None when nothing is published.

    Raises UpdateManifestError when the file exists but is not valid.
    """
    name = sanitize_channel(channel)
    if name is None:
        return None
    path = root / name / "update.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateManifestError("update manifest is unreadable") from exc
    return _validate_manifest(payload, name)


def resolve_update_apk(root: Path, channel: str, filename: str) -> tuple[str, Path | None]:
    """Locate the published APK for a channel.

    Status is ``ok``, ``missing``, ``bad_manifest``, or ``checksum``.
    """
    if sanitize_channel(channel) is None or not _safe_apk_name(filename):
        return "missing", None
    try:
        manifest = read_manifest(root, channel)
    except UpdateManifestError:
        return "bad_manifest", None
    if manifest is None or filename != manifest["apk"]:
        return "missing", None
    directory = (root / sanitize_channel(channel)).resolve()
    candidate = (directory / filename).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return "missing", None
    if not candidate.is_file():
        return "missing", None
    if sha256_file(candidate) != manifest["sha256"]:
        return "checksum", None
    return "ok", candidate


def publish_apk(apk: Path, channel: str, project_root: Path | None = None) -> dict:
    """Copy an APK into the channel directory and write ``update.json``."""
    name = sanitize_channel(channel)
    if name is None:
        raise UpdateError("channel must match [a-z0-9][a-z0-9_-]{0,32}")
    apk_path = Path(apk)
    if not apk_path.is_file():
        raise UpdateError(f"apk not found: {apk_path}")
    version_code, version_name = read_apk_version(apk_path)
    filename = f"GrokRemote-{version_name}.apk"
    if not _safe_apk_name(filename):
        raise UpdateError(f"versionName {version_name!r} cannot be used in an apk file name")
    dest_dir = updates_root(project_root) / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    partial = dest_dir / f"{filename}.partial"
    shutil.copyfile(apk_path, partial)
    os.replace(partial, dest)
    manifest = {
        "channel": name,
        "versionCode": version_code,
        "versionName": version_name,
        "apk": filename,
        "sha256": sha256_file(dest),
        "publishedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _validate_manifest(manifest, name)
    blob = json.dumps(manifest, indent=2) + "\n"
    tmp_manifest = dest_dir / "update.json.tmp"
    tmp_manifest.write_text(blob, encoding="utf-8")
    os.replace(tmp_manifest, dest_dir / "update.json")
    for old in dest_dir.glob("*.apk"):
        if old.name != filename:
            old.unlink()
    for leftover in dest_dir.glob("*.partial"):
        leftover.unlink()
    return manifest


def read_apk_version(apk: Path) -> tuple[int, str]:
    """Read versionCode and versionName from an APK."""
    errors: list[str] = []
    try:
        return _version_from_binary_manifest(apk)
    except UpdateError as exc:
        errors.append(str(exc))
    aapt_version = _version_from_aapt(apk)
    if aapt_version is not None:
        return aapt_version
    meta_version = _version_from_output_metadata(apk)
    if meta_version is not None:
        return meta_version
    detail = errors[0] if errors else "could not read the apk version"
    raise UpdateError(detail)


def _safe_apk_name(filename: str | None) -> bool:
    text = filename or ""
    if ".." in text or "/" in text or "\\" in text:
        return False
    return APK_NAME_RE.fullmatch(text) is not None


def _validate_manifest(payload: object, channel: str) -> dict:
    if not isinstance(payload, dict):
        raise UpdateManifestError("update manifest is unreadable")
    apk = payload.get("apk")
    sha = str(payload.get("sha256") or "").strip().lower()
    version_name = str(payload.get("versionName") or "").strip()
    published = str(payload.get("publishedAt") or "").strip()
    try:
        version_code = int(payload.get("versionCode"))
    except (TypeError, ValueError) as exc:
        raise UpdateManifestError("update manifest is unreadable") from exc
    if payload.get("channel") != channel:
        raise UpdateManifestError("update manifest is unreadable")
    if version_code < 1 or not version_name or not published:
        raise UpdateManifestError("update manifest is unreadable")
    if not _safe_apk_name(str(apk)) or not SHA256_RE.fullmatch(sha):
        raise UpdateManifestError("update manifest is unreadable")
    return {
        "channel": channel,
        "versionCode": version_code,
        "versionName": version_name,
        "apk": str(apk),
        "sha256": sha,
        "publishedAt": published,
    }


def _version_from_binary_manifest(apk: Path) -> tuple[int, str]:
    try:
        with zipfile.ZipFile(apk) as archive:
            data = archive.read("AndroidManifest.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise UpdateError("apk has no readable AndroidManifest.xml") from exc
    return parse_binary_manifest_version(data)


def parse_binary_manifest_version(data: bytes) -> tuple[int, str]:
    """Pull android:versionCode and android:versionName from a binary manifest."""
    if len(data) < 8 or _u32(data, 0) != 0x00080003:
        raise UpdateError("apk manifest is not Android binary xml")
    file_size = _u32(data, 4)
    limit = min(len(data), file_size) if file_size else len(data)
    offset = 8
    strings: list[str] = []
    resmap: list[int] = []
    version_code: int | None = None
    version_name: str | None = None
    while offset + 8 <= limit:
        chunk_type = _u16(data, offset)
        header_size = _u16(data, offset + 2)
        chunk_size = _u32(data, offset + 4)
        if header_size < 8 or chunk_size < header_size or offset + chunk_size > len(data):
            break
        if chunk_type == 0x0001:
            strings = _parse_string_pool(data, offset, header_size, chunk_size)
        elif chunk_type == 0x0180:
            count = (chunk_size - header_size) // 4
            resmap = [_u32(data, offset + header_size + index * 4) for index in range(count)]
        elif chunk_type == 0x0102:
            found_code, found_name, is_manifest = _read_start_tag(
                data, offset, header_size, chunk_size, strings, resmap
            )
            if found_code is not None:
                version_code = found_code
            if found_name:
                version_name = found_name
            if is_manifest and version_code is not None and version_name:
                return version_code, version_name
        offset += chunk_size
    if version_code is None or not version_name:
        raise UpdateError("apk manifest is missing versionCode or versionName")
    return version_code, version_name


def _read_start_tag(
    data: bytes,
    offset: int,
    header_size: int,
    chunk_size: int,
    strings: list[str],
    resmap: list[int],
) -> tuple[int | None, str | None, bool]:
    ext = offset + header_size
    if ext + 20 > offset + chunk_size:
        return None, None, False
    name_idx = _u32(data, ext + 4)
    attr_start = _u16(data, ext + 8)
    attr_size = _u16(data, ext + 10) or 20
    attr_count = _u16(data, ext + 12)
    element = _string_at(strings, name_idx)
    is_manifest = element == "manifest"
    if not is_manifest:
        return None, None, False
    # attributeStart is bytes from the attribute extension, not from the chunk start.
    attr_base = ext + (attr_start if attr_start else 20)
    if attr_base >= offset + chunk_size:
        return None, None, True
    version_code: int | None = None
    version_name: str | None = None
    for index in range(attr_count):
        attr = attr_base + index * attr_size
        if attr + 20 > len(data):
            break
        attr_name_idx = _u32(data, attr + 4)
        raw_value = _u32(data, attr + 8)
        data_type = data[attr + 15]
        data_value = _u32(data, attr + 16)
        res_id = resmap[attr_name_idx] if 0 <= attr_name_idx < len(resmap) else 0
        attr_name = _string_at(strings, attr_name_idx)
        if res_id == _VERSION_CODE_ID or attr_name == "versionCode":
            version_code = int(data_value)
        elif res_id == _VERSION_NAME_ID or attr_name == "versionName":
            if data_type == _TYPE_STRING:
                version_name = _string_at(strings, data_value) or _string_at(strings, raw_value)
            else:
                version_name = _string_at(strings, raw_value)
    return version_code, version_name, True


def _parse_string_pool(data: bytes, offset: int, header_size: int, chunk_size: int) -> list[str]:
    if header_size < 28 or offset + header_size > len(data):
        return []
    string_count = _u32(data, offset + 8)
    flags = _u32(data, offset + 16)
    strings_start = _u32(data, offset + 20)
    utf8 = (flags & (1 << 8)) != 0
    if string_count > 100000:
        return []
    offsets_at = offset + header_size
    pool_base = offset + strings_start
    if pool_base < offset or pool_base > offset + chunk_size:
        return []
    strings: list[str] = []
    for index in range(string_count):
        at = offsets_at + index * 4
        if at + 4 > len(data):
            break
        rel = _u32(data, at)
        strings.append(_read_pool_string(data, pool_base + rel, utf8, offset + chunk_size))
    return strings


def _read_pool_string(data: bytes, pos: int, utf8: bool, end: int) -> str:
    if pos < 0 or pos >= end or pos >= len(data):
        return ""
    if utf8:
        char_len, pos = _utf8_len(data, pos, end)
        byte_len, pos = _utf8_len(data, pos, end)
        if char_len < 0 or byte_len < 0 or pos + byte_len > len(data):
            return ""
        return data[pos : pos + byte_len].decode("utf-8", "replace")
    char_len, pos = _utf16_len(data, pos, end)
    if char_len < 0:
        return ""
    byte_len = char_len * 2
    if pos + byte_len > len(data):
        return ""
    return data[pos : pos + byte_len].decode("utf-16-le", "replace")


def _utf8_len(data: bytes, pos: int, end: int) -> tuple[int, int]:
    if pos >= end or pos >= len(data):
        return -1, pos
    first = data[pos]
    if first & 0x80:
        if pos + 1 >= len(data):
            return -1, pos
        return ((first & 0x7F) << 8) | data[pos + 1], pos + 2
    return first, pos + 1


def _utf16_len(data: bytes, pos: int, end: int) -> tuple[int, int]:
    if pos + 2 > len(data) or pos + 2 > end:
        return -1, pos
    first = _u16(data, pos)
    if first & 0x8000:
        if pos + 4 > len(data):
            return -1, pos
        return ((first & 0x7FFF) << 16) | _u16(data, pos + 2), pos + 4
    return first, pos + 2


def _string_at(strings: list[str], index: int) -> str:
    if index < 0 or index >= len(strings) or index == 0xFFFFFFFF:
        return ""
    return strings[index]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _version_from_aapt(apk: Path) -> tuple[int, str] | None:
    for tool in _aapt_bins():
        try:
            proc = subprocess.run(
                [str(tool), "dump", "badging", str(apk)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(
            r"package: name='[^']+' versionCode='(\d+)' versionName='([^']*)'",
            proc.stdout or "",
        )
        if match and match.group(2):
            return int(match.group(1)), match.group(2)
    return None


def _aapt_bins() -> list[Path]:
    found: list[Path] = []
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    build_tools = Path(sdk) / "build-tools" if sdk else None
    if build_tools is not None and build_tools.is_dir():
        for child in sorted(build_tools.iterdir(), reverse=True):
            for name in ("aapt", "aapt.exe"):
                candidate = child / name
                if candidate.is_file():
                    found.append(candidate)
    which = shutil.which("aapt")
    if which:
        found.append(Path(which))
    return found


def _version_from_output_metadata(apk: Path) -> tuple[int, str] | None:
    meta = apk.with_name("output-metadata.json")
    if not meta.is_file():
        return None
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return None
    for element in elements:
        if not isinstance(element, dict):
            continue
        output = str(element.get("outputFile") or "")
        if output and output != apk.name:
            continue
        try:
            version_code = int(element.get("versionCode"))
        except (TypeError, ValueError):
            continue
        version_name = str(element.get("versionName") or "").strip()
        if version_code >= 1 and version_name:
            return version_code, version_name
    return None
