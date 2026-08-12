#!/usr/bin/env python3
"""Validate public App Store metadata and private submission prerequisites."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA = ROOT / "app-store/metadata.zh-Hans.json"
TAURI_CONFIG = ROOT / "frontend/src-tauri/tauri.conf.json"
INFO_PLIST = ROOT / "frontend/src-tauri/Info.plist"
PRIVACY_MANIFEST = ROOT / "frontend/src-tauri/PrivacyInfo.xcprivacy"
EXPECTED_OWNER_ENVIRONMENTS = {
    "APP_STORE_SUPPORT_URL",
    "APP_STORE_COPYRIGHT",
    "APP_STORE_SKU",
    "APP_STORE_REVIEW_CONTACT_NAME",
    "APP_STORE_REVIEW_CONTACT_EMAIL",
    "APP_STORE_REVIEW_CONTACT_PHONE",
    "APP_STORE_AGE_RATING_CONFIRMED",
    "APP_STORE_CONTENT_RIGHTS_CONFIRMED",
    "APP_STORE_PRIVACY_ANSWERS_CONFIRMED",
    "APP_STORE_PRICE_AND_AVAILABILITY_CONFIRMED",
}
CONFIRMATION_ENVIRONMENTS = {
    "APP_STORE_AGE_RATING_CONFIRMED",
    "APP_STORE_CONTENT_RIGHTS_CONFIRMED",
    "APP_STORE_PRIVACY_ANSWERS_CONFIRMED",
    "APP_STORE_PRICE_AND_AVAILABILITY_CONFIRMED",
}


def utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def normalized_privacy_entry(entry: dict[str, object]) -> tuple[object, ...]:
    return (
        entry.get("NSPrivacyCollectedDataType"),
        entry.get("NSPrivacyCollectedDataTypeLinked"),
        entry.get("NSPrivacyCollectedDataTypeTracking"),
        tuple(sorted(entry.get("NSPrivacyCollectedDataTypePurposes", []))),
    )


def expected_privacy_entry(entry: dict[str, object]) -> tuple[object, ...]:
    return (
        entry.get("identifier"),
        entry.get("linked"),
        entry.get("tracking"),
        tuple(sorted(entry.get("purposes", []))),
    )


def validate_public_metadata(metadata_path: Path) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        tauri = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        with INFO_PLIST.open("rb") as handle:
            info = plistlib.load(handle)
        with PRIVACY_MANIFEST.open("rb") as handle:
            privacy_manifest = plistlib.load(handle)
    except (OSError, json.JSONDecodeError, plistlib.InvalidFileException) as error:
        return [f"无法读取 App Store 元数据源：{error}"], {}

    try:
        app = metadata["app"]
        localization = metadata["localization"]
        review = metadata["review"]
        privacy = metadata["privacy_manifest"]
        owner_inputs = metadata["owner_inputs"]
    except (KeyError, TypeError) as error:
        return [f"App Store 元数据结构缺少字段：{error}"], metadata

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(metadata.get("schema_version") == 1, "App Store 元数据 schema_version 必须是 1")

    name = app.get("name")
    subtitle = app.get("subtitle")
    bundle_id = app.get("bundle_id")
    version = app.get("version")
    privacy_url = app.get("privacy_policy_url")
    require(isinstance(name, str) and 2 <= len(name) <= 30, "App 名称必须为 2 至 30 个字符")
    require(isinstance(subtitle, str) and len(subtitle) <= 30, "副标题不得超过 30 个字符")
    require(bundle_id == tauri.get("identifier"), "元数据 Bundle ID 与 Tauri 配置不一致")
    require(version == tauri.get("version"), "元数据版本号与 Tauri 配置不一致")
    require(name == tauri.get("productName"), "元数据 App 名称与 Tauri productName 不一致")
    require(app.get("primary_language") == "zh-Hans", "首发主语言必须明确为 zh-Hans")
    require(
        app.get("primary_category") == tauri.get("bundle", {}).get("category") == "Video",
        "元数据与 App bundle 的主类别必须同时为 Video",
    )
    require(isinstance(privacy_url, str) and is_https_url(privacy_url), "隐私政策 URL 必须是完整 HTTPS 地址")
    require(
        isinstance(privacy_url, str) and privacy_url.endswith("/docs/PRIVACY.md") and (ROOT / "docs/PRIVACY.md").is_file(),
        "隐私政策 URL 必须对应仓库中受版本控制的 docs/PRIVACY.md",
    )
    require(info.get("ITSAppUsesNonExemptEncryption") is False, "Info.plist 必须声明不使用非豁免加密")

    require(localization.get("locale") == app.get("primary_language"), "本地化语言与主语言不一致")
    description = localization.get("description")
    require(isinstance(description, str) and 0 < len(description) <= 4000, "App 描述必须为 1 至 4000 个字符")
    if isinstance(description, str):
        require(not re.search(r"<\s*/?\s*[A-Za-z][^>]*>", description), "App 描述必须是纯文本，不能包含 HTML")
        require("Final Cut Pro" not in description, "App 描述不得借用标杆产品名称进行营销")

    keywords = localization.get("keywords")
    require(isinstance(keywords, list) and bool(keywords), "关键词必须是非空数组")
    if isinstance(keywords, list):
        require(all(isinstance(item, str) for item in keywords), "每个关键词都必须是字符串")
        string_keywords = [item for item in keywords if isinstance(item, str)]
        require(len(string_keywords) == len(set(string_keywords)), "关键词不得重复")
        require(all(item == item.strip() and "," not in item for item in string_keywords), "关键词不得含空白边界或逗号")
        require(all(len(item) > 2 for item in string_keywords), "每个关键词必须超过 2 个字符")
        require(utf8_size(",".join(string_keywords)) <= 100, "逗号连接后的关键词不得超过 100 UTF-8 bytes")
        if isinstance(name, str):
            require(all(name not in item for item in string_keywords), "关键词不应重复 App 名称")
        require(all("Final Cut Pro" not in item for item in string_keywords), "关键词不得包含其他 App 名称")

    promotional_text = localization.get("promotional_text")
    require(
        promotional_text is None or (isinstance(promotional_text, str) and len(promotional_text) <= 170),
        "促销文本为空或不超过 170 个字符",
    )
    marketing_url = localization.get("marketing_url")
    require(marketing_url is None or (isinstance(marketing_url, str) and is_https_url(marketing_url)), "营销 URL 为空或完整 HTTPS 地址")

    review_notes = review.get("notes")
    require(review.get("sign_in_required") is False, "当前版本必须明确无需登录")
    require(isinstance(review_notes, str) and 0 < utf8_size(review_notes) <= 4000, "审核备注必须为 1 至 4000 UTF-8 bytes")
    if isinstance(review_notes, str):
        for required_phrase in ("No account is required", "Mac App Store build", "Fun-Realtime-ASR", "never silently"):
            require(required_phrase in review_notes, f"审核备注缺少关键说明：{required_phrase}")

    require(privacy_manifest.get("NSPrivacyTracking") is privacy.get("tracking"), "隐私清单的跟踪声明与元数据不一致")
    require(
        privacy_manifest.get("NSPrivacyTrackingDomains") == privacy.get("tracking_domains"),
        "隐私清单的跟踪域名与元数据不一致",
    )
    actual_entries = sorted(
        (normalized_privacy_entry(item) for item in privacy_manifest.get("NSPrivacyCollectedDataTypes", [])),
        key=repr,
    )
    expected_entries = sorted(
        (expected_privacy_entry(item) for item in privacy.get("collected_data_types", [])),
        key=repr,
    )
    require(actual_entries == expected_entries, "隐私清单的数据类型、用途、关联或跟踪声明与元数据不一致")

    owner_environments = {
        item.get("environment")
        for item in owner_inputs
        if isinstance(item, dict)
    } if isinstance(owner_inputs, list) else set()
    require(owner_environments == EXPECTED_OWNER_ENVIRONMENTS, "账号持有人字段清单不完整或包含未知字段")
    return errors, metadata


def validate_owner_fields(metadata: dict[str, object]) -> list[str]:
    errors: list[str] = []
    owner_inputs = metadata.get("owner_inputs", [])
    required = [
        item.get("environment")
        for item in owner_inputs
        if isinstance(item, dict) and isinstance(item.get("environment"), str)
    ]
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        errors.append("缺少账号持有人字段：" + ", ".join(missing))
        return errors

    support_url = os.environ["APP_STORE_SUPPORT_URL"].strip()
    privacy_url = metadata.get("app", {}).get("privacy_policy_url") if isinstance(metadata.get("app"), dict) else None
    if not is_https_url(support_url) or support_url == privacy_url:
        errors.append("APP_STORE_SUPPORT_URL 必须是独立的完整 HTTPS 支持页面")

    copyright_value = os.environ["APP_STORE_COPYRIGHT"].strip()
    copyright_match = re.fullmatch(r"(20\d{2})\s+(.\S.*)", copyright_value)
    if not copyright_match:
        errors.append("APP_STORE_COPYRIGHT 必须使用“年份 法定权利人”格式，且不要包含版权符号")
    else:
        with INFO_PLIST.open("rb") as handle:
            bundle_copyright = str(plistlib.load(handle).get("NSHumanReadableCopyright", ""))
        if copyright_match.group(2).casefold() not in bundle_copyright.casefold():
            errors.append("Info.plist 的 NSHumanReadableCopyright 与 APP_STORE_COPYRIGHT 法定权利人不一致")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", os.environ["APP_STORE_SKU"].strip()):
        errors.append("APP_STORE_SKU 只能使用字母、数字、连字符、句点和下划线，且不能以符号开头")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", os.environ["APP_STORE_REVIEW_CONTACT_EMAIL"].strip()):
        errors.append("APP_STORE_REVIEW_CONTACT_EMAIL 格式无效")
    phone_digits = re.sub(r"\D", "", os.environ["APP_STORE_REVIEW_CONTACT_PHONE"])
    if len(phone_digits) < 7:
        errors.append("APP_STORE_REVIEW_CONTACT_PHONE 必须包含可联系的完整号码")
    if len(os.environ["APP_STORE_REVIEW_CONTACT_NAME"].strip()) < 2:
        errors.append("APP_STORE_REVIEW_CONTACT_NAME 过短")
    for name in sorted(CONFIRMATION_ENVIRONMENTS):
        if os.environ[name].strip().lower() != "true":
            errors.append(f"{name} 必须由账号持有人明确设为 true")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--require-owner-fields", action="store_true")
    arguments = parser.parse_args()

    errors, metadata = validate_public_metadata(arguments.metadata)
    if not errors and arguments.require_owner_fields:
        errors.extend(validate_owner_fields(metadata))
    if errors:
        raise SystemExit("App Store 元数据验证失败：\n- " + "\n- ".join(errors))

    localization = metadata["localization"]
    keywords = localization["keywords"]
    print(
        "App Store 公共元数据通过："
        f"描述 {len(localization['description'])}/4000 字符、"
        f"关键词 {utf8_size(','.join(keywords))}/100 bytes、"
        f"审核备注 {utf8_size(metadata['review']['notes'])}/4000 bytes"
    )
    if arguments.require_owner_fields:
        print("App Store 账号持有人字段通过。")
    else:
        unresolved = ", ".join(item["environment"] for item in metadata["owner_inputs"])
        print(f"正式提交仍需账号持有人通过环境变量提供：{unresolved}")


if __name__ == "__main__":
    main()
