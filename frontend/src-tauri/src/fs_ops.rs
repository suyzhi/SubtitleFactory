use std::{
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
};

use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

pub struct ManagedFiles {
    pub root: PathBuf,
}

pub fn validate_managed_path(
    root: &Path,
    candidate: &Path,
    require_file: bool,
) -> Result<PathBuf, String> {
    let canonical_root = root
        .canonicalize()
        .map_err(|_| "应用管理的数据目录不存在".to_string())?;
    let canonical_candidate = candidate
        .canonicalize()
        .map_err(|_| "要交付的文件不存在".to_string())?;
    if !canonical_candidate.starts_with(&canonical_root) {
        return Err("拒绝访问应用管理目录之外的文件".into());
    }
    if require_file && !canonical_candidate.is_file() {
        return Err("要交付的内容不是文件".into());
    }
    Ok(canonical_candidate)
}

pub fn safe_suggested_name(suggested_name: &str, source: Option<&Path>) -> Result<String, String> {
    const MAX_FILENAME_BYTES: usize = 240;

    fn truncate_utf8(value: &str, max_bytes: usize) -> &str {
        if value.len() <= max_bytes {
            return value;
        }
        let mut boundary = max_bytes;
        while !value.is_char_boundary(boundary) {
            boundary -= 1;
        }
        &value[..boundary]
    }

    fn sanitize(value: &str) -> Option<String> {
        let mut result = String::with_capacity(value.len());
        let mut replacing = false;
        for character in value.trim().chars() {
            if character == '/' || character == '\\' || character == ':' || character.is_control() {
                if !replacing {
                    result.push('-');
                    replacing = true;
                }
            } else {
                result.push(character);
                replacing = false;
            }
        }
        let result = result.trim().trim_matches('.').trim();
        if result.is_empty() || result == "." || result == ".." {
            return None;
        }
        if result.len() <= MAX_FILENAME_BYTES {
            return Some(result.to_string());
        }
        let suffix = result
            .rfind('.')
            .filter(|index| *index > 0 && result.len() - index <= 32)
            .map(|index| &result[index..])
            .unwrap_or("");
        let stem_bytes = MAX_FILENAME_BYTES.saturating_sub(suffix.len());
        let stem = truncate_utf8(&result[..result.len() - suffix.len()], stem_bytes).trim_end();
        (!stem.is_empty()).then(|| format!("{stem}{suffix}"))
    }

    sanitize(suggested_name)
        .or_else(|| {
            source
                .and_then(Path::file_name)
                .and_then(|value| value.to_str())
                .and_then(sanitize)
        })
        .ok_or_else(|| "建议的文件名无效".into())
}

pub fn choose_save_destination(
    app: &tauri::AppHandle,
    suggested_name: &str,
) -> Result<Option<PathBuf>, String> {
    let mut dialog = app
        .dialog()
        .file()
        .set_title("导出文件")
        .set_file_name(suggested_name);
    if let Some(extension) = Path::new(suggested_name)
        .extension()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
    {
        dialog = dialog.add_filter("交付文件", &[extension]);
    }
    if let Some(window) = app.get_webview_window("main") {
        dialog = dialog.set_parent(&window);
    }
    dialog
        .blocking_save_file()
        .map(|value| {
            value
                .into_path()
                .map(|path| normalize_repeated_save_extension(path, suggested_name))
                .map_err(|error| error.to_string())
        })
        .transpose()
}

pub fn normalize_repeated_save_extension(
    mut destination: PathBuf,
    suggested_name: &str,
) -> PathBuf {
    let Some(extension) = Path::new(suggested_name)
        .extension()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
    else {
        return destination;
    };
    let Some(file_name) = destination
        .file_name()
        .and_then(|value| value.to_str())
        .map(str::to_owned)
    else {
        return destination;
    };
    let suffix = format!(".{extension}");
    let repeated_suffix = format!("{suffix}{suffix}");
    if file_name
        .to_ascii_lowercase()
        .ends_with(&repeated_suffix.to_ascii_lowercase())
    {
        let normalized = destination.with_file_name(&file_name[..file_name.len() - suffix.len()]);
        // The native panel confirmed the literal returned path. Never turn that
        // confirmation into an unconfirmed overwrite of a different file.
        if !normalized.exists() {
            destination = normalized;
        }
    }
    destination
}

pub fn stream_copy(source: &Path, destination: &Path) -> Result<u64, String> {
    if source == destination {
        return source
            .metadata()
            .map(|metadata| metadata.len())
            .map_err(|error| error.to_string());
    }
    let mut input = File::open(source).map_err(|error| format!("无法读取交付文件：{error}"))?;
    write_atomically(destination, |output| io::copy(&mut input, output))
        .map_err(|error| format!("复制交付文件失败：{error}"))
}

pub fn write_atomically<F>(destination: &Path, writer: F) -> Result<u64, String>
where
    F: FnOnce(&mut File) -> io::Result<u64>,
{
    let parent = destination
        .parent()
        .ok_or_else(|| "目标文件没有可写目录".to_string())?;
    destination
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "目标文件名无效".to_string())?;
    let temporary = parent.join(format!(
        ".subtitle-factory-{}.part",
        Uuid::new_v4().simple()
    ));
    let mut output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .map_err(|error| format!("无法创建临时交付文件：{error}"))?;
    let written = match writer(&mut output).and_then(|bytes| {
        output.sync_all()?;
        Ok(bytes)
    }) {
        Ok(bytes) => bytes,
        Err(error) => {
            drop(output);
            let _ = fs::remove_file(&temporary);
            return Err(format!("无法完成临时文件写入：{error}"));
        }
    };
    drop(output);
    if let Err(error) = fs::rename(&temporary, destination) {
        let _ = fs::remove_file(&temporary);
        return Err(format!("无法原子替换目标文件：{error}"));
    }
    Ok(written)
}

#[cfg(test)]
mod tests {
    use super::{
        normalize_repeated_save_extension, safe_suggested_name, stream_copy, validate_managed_path,
        write_atomically,
    };
    use std::{fs, io, io::Write, path::Path};

    fn isolated_test_dir(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("subtitle-factory-{label}-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn managed_file_validation_rejects_outside_and_symlink_escape() {
        let test_dir = isolated_test_dir("managed-file-test");
        let root = test_dir.join("managed");
        let outside = test_dir.join("outside.txt");
        fs::create_dir_all(&root).expect("create managed root");
        fs::write(root.join("inside.txt"), b"inside").expect("write managed file");
        fs::write(&outside, b"outside").expect("write outside file");

        assert!(validate_managed_path(&root, &root.join("inside.txt"), true).is_ok());
        assert!(validate_managed_path(&root, &outside, true).is_err());

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&outside, root.join("escape.txt"))
                .expect("create escape symlink");
            assert!(validate_managed_path(&root, &root.join("escape.txt"), true).is_err());
        }

        fs::remove_dir_all(&test_dir).expect("remove managed file test directory");
    }

    #[test]
    fn suggested_names_are_flat_and_have_a_safe_fallback() {
        let sanitized =
            safe_suggested_name("../项目\\成片\n.mp4", None).expect("sanitize suggested filename");
        assert!(!sanitized.contains('/'));
        assert!(!sanitized.contains('\\'));
        assert!(!sanitized.chars().any(char::is_control));
        assert!(sanitized.ends_with(".mp4"));
        assert!(!sanitized.contains(':'));

        let long_name = format!("{}.mp4", "超长项目名称".repeat(80));
        let truncated = safe_suggested_name(&long_name, None).expect("truncate long filename");
        assert!(truncated.len() <= 240);
        assert!(truncated.ends_with(".mp4"));

        assert_eq!(
            safe_suggested_name("   ", Some(Path::new("/managed/export.srt")))
                .expect("use source filename"),
            "export.srt"
        );
        assert!(safe_suggested_name("\n", None).is_err());
    }

    #[test]
    fn native_save_destination_does_not_duplicate_the_expected_extension() {
        assert_eq!(
            normalize_repeated_save_extension(
                "/tmp/qa-content-pack.zip.zip".into(),
                "content-pack.zip",
            ),
            Path::new("/tmp/qa-content-pack.zip")
        );
        assert_eq!(
            normalize_repeated_save_extension(
                "/tmp/QA-CONTENT-PACK.ZIP.ZIP".into(),
                "content-pack.zip",
            ),
            Path::new("/tmp/QA-CONTENT-PACK.ZIP")
        );
        assert_eq!(
            normalize_repeated_save_extension(
                "/tmp/archive.tar.zip".into(),
                "content-pack.zip",
            ),
            Path::new("/tmp/archive.tar.zip")
        );

        let test_dir = isolated_test_dir("save-extension-test");
        fs::create_dir_all(&test_dir).expect("create save extension test directory");
        let existing = test_dir.join("existing.zip");
        fs::write(&existing, b"keep").expect("write existing intended destination");
        assert_eq!(
            normalize_repeated_save_extension(
                test_dir.join("existing.zip.zip"),
                "content-pack.zip",
            ),
            test_dir.join("existing.zip.zip")
        );
        fs::remove_dir_all(&test_dir).expect("remove save extension test directory");
    }

    #[test]
    fn stream_copy_preserves_exact_bytes() {
        let test_dir = isolated_test_dir("stream-copy-test");
        fs::create_dir_all(&test_dir).expect("create stream copy directory");
        let source = test_dir.join("source.bin");
        let destination = test_dir.join("destination.bin");
        let payload: Vec<u8> = (0..131_071).map(|index| (index % 251) as u8).collect();
        fs::write(&source, &payload).expect("write source payload");

        assert_eq!(
            stream_copy(&source, &destination).expect("stream copy succeeds"),
            payload.len() as u64
        );
        assert_eq!(
            fs::read(&destination).expect("read copied payload"),
            payload
        );
        assert_eq!(
            stream_copy(&source, &source).expect("same source is a no-op"),
            payload.len() as u64
        );

        fs::remove_dir_all(&test_dir).expect("remove stream copy directory");
    }

    #[test]
    fn failed_atomic_write_preserves_an_existing_destination() {
        let test_dir = isolated_test_dir("atomic-write-test");
        fs::create_dir_all(&test_dir).expect("create atomic write directory");
        let destination = test_dir.join("existing.txt");
        fs::write(&destination, b"original").expect("write original destination");

        let result = write_atomically(&destination, |output| {
            output.write_all(b"partial")?;
            Err(io::Error::other("synthetic interruption"))
        });
        assert!(result.is_err());
        assert_eq!(
            fs::read(&destination).expect("read preserved destination"),
            b"original"
        );
        assert_eq!(
            fs::read_dir(&test_dir)
                .expect("list atomic write directory")
                .count(),
            1
        );

        fs::remove_dir_all(&test_dir).expect("remove atomic write directory");
    }
}
