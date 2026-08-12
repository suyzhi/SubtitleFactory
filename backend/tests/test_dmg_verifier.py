import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-dmg-app.py"
SPEC = importlib.util.spec_from_file_location("verify_dmg_app", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dmg_verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dmg_verifier
SPEC.loader.exec_module(dmg_verifier)


def test_manifest_records_files_directories_and_relative_links(tmp_path: Path):
    root = tmp_path / "Sample.app"
    resources = root / "Contents/Resources"
    resources.mkdir(parents=True)
    payload = resources / "payload.bin"
    payload.write_bytes(b"subtitle-factory")
    os.symlink("payload.bin", resources / "payload-current.bin")

    manifest = dmg_verifier.build_manifest(root)

    assert manifest["Contents"].kind == "directory"
    assert manifest["Contents/Resources/payload.bin"].kind == "file"
    assert manifest["Contents/Resources/payload.bin"].size == len(b"subtitle-factory")
    assert manifest["Contents/Resources/payload-current.bin"].kind == "symlink"
    assert manifest["Contents/Resources/payload-current.bin"].digest_or_target == "payload.bin"


def test_manifest_detects_same_size_content_changes(tmp_path: Path):
    left = tmp_path / "left.app"
    right = tmp_path / "right.app"
    left.mkdir()
    right.mkdir()
    (left / "payload").write_bytes(b"abc")
    (right / "payload").write_bytes(b"xyz")

    assert dmg_verifier.build_manifest(left) != dmg_verifier.build_manifest(right)
