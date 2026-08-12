use std::{
    fs::{self, File, OpenOptions},
    io::{self, Write},
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{Manager, RunEvent, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

const PROFESSIONAL_UI_MARKER: &str = "subtitle-factory-ui:professional-v2";
const LIBRARY_WORKSPACE_UI_MARKER: &str = "subtitle-factory-ui:library-workspace-v2";
const DIRECT_DISTRIBUTION_CHANNEL: &str = "direct";
const APP_STORE_DISTRIBUTION_CHANNEL: &str = "app_store";
const BACKEND_STARTUP_ERROR_FILE: &str = "backend-startup-error.txt";

#[cfg(unix)]
use std::os::unix::process::CommandExt;

struct BackendProcess {
    child: Mutex<Option<Child>>,
    process_group: Option<i32>,
    pid_file: PathBuf,
}

struct ManagedFiles {
    root: PathBuf,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendSession {
    base_url: String,
    token: String,
    port: u16,
    distribution_channel: String,
    youtube_enabled: bool,
    filesystem_automation_enabled: bool,
    external_runtime_paths_enabled: bool,
}

fn distribution_channel() -> &'static str {
    match option_env!("SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL") {
        Some(APP_STORE_DISTRIBUTION_CHANNEL) => APP_STORE_DISTRIBUTION_CHANNEL,
        _ => DIRECT_DISTRIBUTION_CHANNEL,
    }
}

impl BackendProcess {
    fn stop(&self) {
        #[cfg(unix)]
        if let Some(group) = self.process_group {
            unsafe {
                libc::kill(-group, libc::SIGTERM);
            }
            thread::sleep(Duration::from_millis(350));
            unsafe {
                libc::kill(-group, libc::SIGKILL);
            }
        }
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
            *guard = None;
        }
        let _ = fs::remove_file(&self.pid_file);
    }
}

fn create_backend_session() -> Result<BackendSession, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("无法分配本地后端端口：{error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| error.to_string())?
        .port();
    drop(listener);
    let channel = distribution_channel();
    let app_store = channel == APP_STORE_DISTRIBUTION_CHANNEL;
    Ok(BackendSession {
        base_url: format!("http://127.0.0.1:{port}"),
        token: format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()),
        port,
        distribution_channel: channel.to_string(),
        youtube_enabled: !app_store,
        filesystem_automation_enabled: !app_store,
        external_runtime_paths_enabled: !app_store,
    })
}

#[tauri::command]
fn backend_session(session: State<'_, BackendSession>) -> BackendSession {
    session.inner().clone()
}

#[tauri::command]
fn reveal_path(path: String, managed_files: State<'_, ManagedFiles>) -> Result<(), String> {
    let candidate = validate_managed_path(&managed_files.root, Path::new(&path), false)?;
    Command::new("/usr/bin/open")
        .arg(&candidate)
        .status()
        .map_err(|error| error.to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| "无法在 Finder 中打开路径".into())
}

fn backend_data_dir(app_data: &Path, session: &BackendSession) -> PathBuf {
    if session.distribution_channel == APP_STORE_DISTRIBUTION_CHANNEL {
        app_data.join("data")
    } else {
        std::env::var_os("SUBTITLE_FACTORY_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| app_data.join("data"))
    }
}

fn validate_managed_path(
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

fn safe_suggested_name(suggested_name: &str, source: Option<&Path>) -> Result<String, String> {
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

fn choose_save_destination(
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
        .map(|value| value.into_path().map_err(|error| error.to_string()))
        .transpose()
}

fn stream_copy(source: &Path, destination: &Path) -> Result<u64, String> {
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

fn write_atomically<F>(destination: &Path, writer: F) -> Result<u64, String>
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

#[tauri::command]
async fn save_managed_file(
    app: tauri::AppHandle,
    managed_files: State<'_, ManagedFiles>,
    source_path: String,
    suggested_name: String,
) -> Result<Option<String>, String> {
    let source = validate_managed_path(&managed_files.root, Path::new(&source_path), true)?;
    let suggested_name = safe_suggested_name(&suggested_name, Some(&source))?;
    let Some(destination) = choose_save_destination(&app, &suggested_name)? else {
        return Ok(None);
    };
    let destination_for_result = destination.clone();
    tauri::async_runtime::spawn_blocking(move || stream_copy(&source, &destination))
        .await
        .map_err(|error| format!("交付任务意外结束：{error}"))??;
    Ok(Some(destination_for_result.to_string_lossy().into_owned()))
}

#[tauri::command]
async fn save_text_file(
    app: tauri::AppHandle,
    suggested_name: String,
    contents: String,
) -> Result<Option<String>, String> {
    if contents.len() > 10 * 1024 * 1024 {
        return Err("文本交付内容超过 10 MB".into());
    }
    let suggested_name = safe_suggested_name(&suggested_name, None)?;
    let Some(destination) = choose_save_destination(&app, &suggested_name)? else {
        return Ok(None);
    };
    let destination_for_result = destination.clone();
    tauri::async_runtime::spawn_blocking(move || {
        write_atomically(&destination, |output| {
            output.write_all(contents.as_bytes())?;
            Ok(contents.len() as u64)
        })
    })
    .await
    .map_err(|error| format!("交付任务意外结束：{error}"))??;
    Ok(Some(destination_for_result.to_string_lossy().into_owned()))
}

fn development_backend_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend")
}

#[cfg(unix)]
fn stop_stale_process_group(pid_file: &PathBuf) {
    let Ok(value) = fs::read_to_string(pid_file) else {
        return;
    };
    let Ok(group) = value.trim().parse::<i32>() else {
        let _ = fs::remove_file(pid_file);
        return;
    };
    if group > 1 {
        unsafe {
            if libc::kill(-group, 0) == 0 {
                libc::kill(-group, libc::SIGTERM);
                thread::sleep(Duration::from_millis(300));
                libc::kill(-group, libc::SIGKILL);
            }
        }
    }
    let _ = fs::remove_file(pid_file);
}

#[cfg(not(unix))]
fn stop_stale_process_group(pid_file: &PathBuf) {
    let _ = fs::remove_file(pid_file);
}

fn start_backend(
    app: &tauri::App,
    session: &BackendSession,
    backend_data: &Path,
) -> Result<BackendProcess, String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    fs::create_dir_all(&app_data).map_err(|error| error.to_string())?;
    let pid_file = app_data.join("backend.pid");
    stop_stale_process_group(&pid_file);

    let packaged_runtime = if cfg!(debug_assertions) {
        None
    } else {
        Some(
            app.path()
                .resource_dir()
                .map_err(|error| error.to_string())?
                .join("backend-runtime"),
        )
    };

    let mut command = if cfg!(debug_assertions) {
        let backend_dir = development_backend_dir();
        let configured = std::env::var_os("SUBTITLE_FACTORY_PYTHON").map(PathBuf::from);
        let venv_python = backend_dir.join(".venv/bin/python");
        let python = configured
            .filter(|path| path.exists())
            .unwrap_or(venv_python);
        if !python.exists() {
            return Err(format!("后端 Python 环境不存在：{}", python.display()));
        }
        let mut cmd = Command::new(python);
        cmd.current_dir(backend_dir)
            .args([
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
            ])
            .arg(session.port.to_string());
        cmd
    } else {
        let binary_name = if cfg!(target_os = "windows") {
            "subtitle-backend.exe"
        } else {
            "subtitle-backend"
        };
        let backend_binary = packaged_runtime
            .as_ref()
            .expect("release runtime directory")
            .join(binary_name);
        if !backend_binary.exists() {
            return Err(format!("App 后端组件缺失：{}", backend_binary.display()));
        }
        Command::new(backend_binary)
    };

    if let Some(runtime_dir) = packaged_runtime.as_ref() {
        let ffmpeg = runtime_dir.join("bin/ffmpeg");
        let ffprobe = runtime_dir.join("bin/ffprobe");
        let deno = runtime_dir.join("bin/deno");
        if !ffmpeg.is_file() || !ffprobe.is_file() || (session.youtube_enabled && !deno.is_file()) {
            return Err(if session.youtube_enabled {
                "App 内置 FFmpeg/FFprobe/Deno 缺失，发布包不完整".into()
            } else {
                "App 内置 FFmpeg/FFprobe 缺失，发布包不完整".into()
            });
        }
        command
            .env("SUBTITLE_FACTORY_BUNDLED_FFMPEG", &ffmpeg)
            .env("SUBTITLE_FACTORY_BUNDLED_FFPROBE", &ffprobe)
            .env("SUBTITLE_FACTORY_RESOURCE_DIR", runtime_dir);
        if session.youtube_enabled {
            command.env("SUBTITLE_FACTORY_BUNDLED_DENO", &deno);
        }
    }

    let log_path = app_data.join("backend.log");
    let mut log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("无法创建后端日志：{error}"))?;
    let _ = writeln!(log_file, "\n===== App launching backend =====");
    let error_file = log_file.try_clone().map_err(|error| error.to_string())?;

    command
        .env("SUBTITLE_FACTORY_DATA_DIR", backend_data)
        .env("SUBTITLE_FACTORY_APP_VERSION", env!("CARGO_PKG_VERSION"))
        .env(
            "SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL",
            &session.distribution_channel,
        )
        .env("SUBTITLE_FACTORY_PORT", session.port.to_string())
        .env("SUBTITLE_FACTORY_API_TOKEN", &session.token)
        .env(
            "SUBTITLE_FACTORY_ALLOWED_ORIGINS",
            "tauri://localhost,http://tauri.localhost,http://localhost:5173,http://127.0.0.1:5173",
        )
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(error_file));
    #[cfg(unix)]
    command.process_group(0);

    let child = command
        .spawn()
        .map_err(|error| format!("后端启动失败：{error}"))?;
    let group = child.id() as i32;
    fs::write(&pid_file, group.to_string())
        .map_err(|error| format!("无法写入后端 PID：{error}"))?;
    Ok(BackendProcess {
        child: Mutex::new(Some(child)),
        process_group: Some(group),
        pid_file,
    })
}

fn stop_managed_backend(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        state.stop();
    }
}

fn clear_stale_backend_startup_error(app_data: &Path) -> std::io::Result<()> {
    match fs::remove_file(app_data.join(BACKEND_STARTUP_ERROR_FILE)) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            backend_session,
            reveal_path,
            save_managed_file,
            save_text_file,
        ])
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .setup(|app| {
            log::info!("release UI marker: {PROFESSIONAL_UI_MARKER}");
            log::info!("release UI layout marker: {LIBRARY_WORKSPACE_UI_MARKER}");
            let session = create_backend_session()?;
            let app_data = app
                .path()
                .app_data_dir()
                .map_err(|value| value.to_string())?;
            let managed_root = backend_data_dir(&app_data, &session);
            let backend = match start_backend(app, &session, &managed_root) {
                Ok(backend) => {
                    if let Err(error) = clear_stale_backend_startup_error(&app_data) {
                        log::warn!("无法清理过期的后端启动错误：{error}");
                    }
                    backend
                }
                Err(error) => {
                    log::error!("{error}");
                    let _ = fs::write(app_data.join(BACKEND_STARTUP_ERROR_FILE), &error);
                    BackendProcess {
                        child: Mutex::new(None),
                        process_group: None,
                        pid_file: app_data.join("backend.pid"),
                    }
                }
            };
            app.manage(ManagedFiles { root: managed_root });
            app.manage(session);
            app.manage(backend);
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                stop_managed_backend(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_managed_backend(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{
        clear_stale_backend_startup_error, safe_suggested_name, stream_copy, validate_managed_path,
        write_atomically, BACKEND_STARTUP_ERROR_FILE,
    };
    use std::{fs, io, io::Write, path::Path};

    fn isolated_test_dir(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("subtitle-factory-{label}-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn successful_start_clears_stale_backend_error() {
        let test_dir = isolated_test_dir("startup-error-test");
        fs::create_dir_all(&test_dir).expect("create isolated test directory");
        let error_file = test_dir.join(BACKEND_STARTUP_ERROR_FILE);
        fs::write(&error_file, "stale failure").expect("write stale error");

        clear_stale_backend_startup_error(&test_dir).expect("clear stale error");

        assert!(!error_file.exists());
        clear_stale_backend_startup_error(&test_dir).expect("missing file is already clean");
        fs::remove_dir(&test_dir).expect("remove isolated test directory");
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
