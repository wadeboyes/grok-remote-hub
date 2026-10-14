"""Hub-hosted sideload updates. The APK stays under data/ and the token stays on."""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from hub.app_update import (
    UpdateError,
    UpdateManifestError,
    parse_binary_manifest_version,
    publish_apk,
    read_apk_version,
    read_manifest,
    resolve_update_apk,
)

FIXTURE = Path("tests/fixtures/app_update_manifest.bin")


def _apk_from_manifest(dest: Path, manifest: bytes) -> Path:
    with zipfile.ZipFile(dest, "w") as archive:
        archive.writestr("AndroidManifest.xml", manifest)
    return dest


def _utf8_manifest(version_code: int, version_name: str) -> bytes:
    """Minimal aapt-style binary manifest with a UTF-8 string pool."""
    strings = ["manifest", "versionCode", "versionName", version_name]

    def encode(text: str) -> bytes:
        raw = text.encode("utf-8")
        return bytes((len(text), len(raw))) + raw + b"\x00"

    encoded = [encode(text) for text in strings]
    offsets: list[int] = []
    cursor = 0
    for blob in encoded:
        offsets.append(cursor)
        cursor += len(blob)
    string_data = b"".join(encoded)
    header_size = 28
    offsets_blob = b"".join(struct.pack("<I", item) for item in offsets)
    strings_start = header_size + len(offsets_blob)
    chunk_size = strings_start + len(string_data)
    pad = (4 - (chunk_size % 4)) % 4
    chunk_size += pad
    pool = struct.pack("<HHI", 0x0001, header_size, chunk_size)
    pool += struct.pack("<IIIII", len(strings), 0, 0x100, strings_start, 0)
    pool += offsets_blob + string_data + (b"\x00" * pad)

    def attr(name_index: int, raw: int, data_type: int, data_value: int) -> bytes:
        return struct.pack("<IIIHBBI", 0xFFFFFFFF, name_index, raw, 8, 0, data_type, data_value)

    body = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 2, 0, 0, 0)
    body += attr(1, 0xFFFFFFFF, 0x10, version_code)
    body += attr(2, 3, 0x03, 3)
    node_size = 16 + len(body)
    node = struct.pack("<HHIII", 0x0102, 16, node_size, 1, 0xFFFFFFFF) + body
    payload = pool + node
    return struct.pack("<HHI", 0x0003, 8, 8 + len(payload)) + payload


def test_fixture_manifest_version() -> None:
    data = FIXTURE.read_bytes()
    assert parse_binary_manifest_version(data) == (2, "1.0.1")


def test_utf8_manifest_version() -> None:
    data = _utf8_manifest(7, "2.4.0")
    assert parse_binary_manifest_version(data) == (7, "2.4.0")


def test_publish_writes_manifest_and_drops_older_apk(tmp_path: Path) -> None:
    first = _apk_from_manifest(tmp_path / "first.apk", _utf8_manifest(2, "1.0.1"))
    project = tmp_path / "proj"
    manifest = publish_apk(first, "stable", project)
    folder = project / "data" / "app-updates" / "stable"
    stored = folder / "GrokRemote-1.0.1.apk"
    assert stored.is_file()
    assert manifest["versionCode"] == 2
    assert manifest["versionName"] == "1.0.1"
    assert manifest["apk"] == "GrokRemote-1.0.1.apk"
    assert manifest["sha256"] == hashlib.sha256(stored.read_bytes()).hexdigest()
    assert read_manifest(folder.parent, "stable")["versionCode"] == 2

    newer = _apk_from_manifest(tmp_path / "newer.apk", _utf8_manifest(3, "1.0.2"))
    publish_apk(newer, "Stable", project)
    assert (folder / "GrokRemote-1.0.2.apk").is_file()
    assert not stored.exists()
    current = json.loads((folder / "update.json").read_text(encoding="utf-8"))
    assert current["versionCode"] == 3
    assert current["channel"] == "stable"


def test_resolve_rejects_traversal_and_checksum_mismatch(tmp_path: Path) -> None:
    apk = _apk_from_manifest(tmp_path / "app.apk", FIXTURE.read_bytes())
    project = tmp_path / "proj"
    publish_apk(apk, "stable", project)
    root = project / "data" / "app-updates"
    status, path = resolve_update_apk(root, "stable", "GrokRemote-1.0.1.apk")
    assert status == "ok"
    assert path is not None and path.is_file()
    assert resolve_update_apk(root, "../stable", "GrokRemote-1.0.1.apk")[0] == "missing"
    assert resolve_update_apk(root, "stable", "../GrokRemote-1.0.1.apk")[0] == "missing"
    assert resolve_update_apk(root, "stable", "GrokRemote-9.9.9.apk")[0] == "missing"
    path.write_bytes(path.read_bytes() + b"x")
    assert resolve_update_apk(root, "stable", "GrokRemote-1.0.1.apk")[0] == "checksum"


def test_bad_channel_and_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(UpdateError):
        publish_apk(tmp_path / "missing.apk", "stable", tmp_path)
    with pytest.raises(UpdateError):
        publish_apk(FIXTURE, "Not A Channel", tmp_path)
    assert read_manifest(tmp_path, "stable") is None
    broken = tmp_path / "stable"
    broken.mkdir()
    (broken / "update.json").write_text("{", encoding="utf-8")
    with pytest.raises(UpdateManifestError):
        read_manifest(tmp_path, "stable")


def test_output_metadata_fallback(tmp_path: Path) -> None:
    apk = tmp_path / "app-release.apk"
    apk.write_bytes(b"not a zip")
    meta = tmp_path / "output-metadata.json"
    meta.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "outputFile": "app-release.apk",
                        "versionCode": 4,
                        "versionName": "1.2.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert read_apk_version(apk) == (4, "1.2.0")


def test_app_update_routes_stay_behind_hub_token() -> None:
    server = Path("hub/server.py").read_text(encoding="utf-8")
    start = server.find("async def _auth_middleware")
    end = server.find("async def _on_startup")
    middleware = server[start:end]
    assert middleware.count('request.path == "/health"') == 1
    assert "/app" not in middleware
    assert "/api/build" not in middleware
    update_at = server.find('add_get("/app/update.json"')
    apk_at = server.find('add_get("/app/{filename}"')
    static_at = server.find('add_static("/", static_dir')
    assert 0 < update_at < apk_at < static_at
    supervisor = Path("hub/agent_supervisor.py").read_text(encoding="utf-8")
    assert "--always-approve" in supervisor
