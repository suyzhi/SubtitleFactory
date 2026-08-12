import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-app-store-metadata.py"
SPEC = importlib.util.spec_from_file_location("verify_app_store_metadata", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
metadata_verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata_verifier)


def load_metadata() -> dict:
    return json.loads((ROOT / "app-store/metadata.zh-Hans.json").read_text(encoding="utf-8"))


def write_metadata(tmp_path: Path, metadata: dict) -> Path:
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return path


def test_public_metadata_matches_bundle_and_privacy_manifest():
    errors, _ = metadata_verifier.validate_public_metadata(metadata_verifier.DEFAULT_METADATA)

    assert errors == []


def test_keyword_byte_limit_is_measured_as_utf8(tmp_path: Path):
    metadata = load_metadata()
    metadata["localization"]["keywords"] = ["字幕" * 17]

    errors, _ = metadata_verifier.validate_public_metadata(write_metadata(tmp_path, metadata))

    assert "逗号连接后的关键词不得超过 100 UTF-8 bytes" in errors


def test_privacy_manifest_drift_is_rejected(tmp_path: Path):
    metadata = load_metadata()
    metadata["privacy_manifest"]["collected_data_types"] = []

    errors, _ = metadata_verifier.validate_public_metadata(write_metadata(tmp_path, metadata))

    assert "隐私清单的数据类型、用途、关联或跟踪声明与元数据不一致" in errors


def test_minimum_macos_version_drift_is_rejected(tmp_path: Path):
    metadata = load_metadata()
    metadata["app"]["minimum_macos_version"] = "12.0"

    errors, _ = metadata_verifier.validate_public_metadata(write_metadata(tmp_path, metadata))

    assert "元数据与 App bundle 的最低 macOS 版本必须同时为 14.0" in errors


def test_strict_owner_gate_lists_missing_fields(monkeypatch):
    metadata = load_metadata()
    for name in metadata_verifier.EXPECTED_OWNER_ENVIRONMENTS:
        monkeypatch.delenv(name, raising=False)

    errors = metadata_verifier.validate_owner_fields(metadata)

    assert len(errors) == 1
    assert "缺少账号持有人字段" in errors[0]
    for name in metadata_verifier.EXPECTED_OWNER_ENVIRONMENTS:
        assert name in errors[0]


def test_strict_owner_gate_accepts_well_formed_private_inputs(monkeypatch):
    values = {
        "APP_STORE_SUPPORT_URL": "https://support.example.com/subtitle-factory",
        "APP_STORE_COPYRIGHT": "2026 Subtitle Factory",
        "APP_STORE_SKU": "subtitlefactory-mac",
        "APP_STORE_REVIEW_CONTACT_NAME": "Review Contact",
        "APP_STORE_REVIEW_CONTACT_EMAIL": "reviewer@example.com",
        "APP_STORE_REVIEW_CONTACT_PHONE": "+1 555 555 0100",
        "APP_STORE_AGE_RATING_CONFIRMED": "true",
        "APP_STORE_CONTENT_RIGHTS_CONFIRMED": "true",
        "APP_STORE_PRIVACY_ANSWERS_CONFIRMED": "true",
        "APP_STORE_PRICE_AND_AVAILABILITY_CONFIRMED": "true",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert metadata_verifier.validate_owner_fields(load_metadata()) == []
