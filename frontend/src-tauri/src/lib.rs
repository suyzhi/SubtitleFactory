use std::{
    fs::{self, OpenOptions},
    io::Write,
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{Manager, RunEvent, State, WindowEvent};
use uuid::Uuid;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

struct BackendProcess {
    child: Mutex<Option<Child>>,
    process_group: Option<i32>,
    pid_file: PathBuf,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendSession {
    base_url: String,
    token: String,
    port: u16,
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
    Ok(BackendSession {
        base_url: format!("http://127.0.0.1:{port}"),
        token: format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()),
        port,
    })
}

#[tauri::command]
fn backend_session(session: State<'_, BackendSession>) -> BackendSession {
    session.inner().clone()
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let candidate = PathBuf::from(path);
    if !candidate.exists() {
        return Err("路径不存在".into());
    }
    Command::new("/usr/bin/open")
        .arg(&candidate)
        .status()
        .map_err(|error| error.to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| "无法在 Finder 中打开路径".into())
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

fn start_backend(app: &tauri::App, session: &BackendSession) -> Result<BackendProcess, String> {
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
        if !ffmpeg.is_file() || !ffprobe.is_file() {
            return Err("App 内置 FFmpeg/FFprobe 缺失，发布包不完整".into());
        }
        command
            .env("SUBTITLE_FACTORY_BUNDLED_FFMPEG", &ffmpeg)
            .env("SUBTITLE_FACTORY_BUNDLED_FFPROBE", &ffprobe)
            .env("SUBTITLE_FACTORY_RESOURCE_DIR", runtime_dir);
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
        .env("SUBTITLE_FACTORY_DATA_DIR", app_data.join("data"))
        .env("SUBTITLE_FACTORY_APP_VERSION", env!("CARGO_PKG_VERSION"))
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![backend_session, reveal_path])
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .setup(|app| {
            let session = create_backend_session()?;
            let backend = match start_backend(app, &session) {
                Ok(backend) => backend,
                Err(error) => {
                    log::error!("{error}");
                    let app_data = app
                        .path()
                        .app_data_dir()
                        .map_err(|value| value.to_string())?;
                    let _ = fs::write(app_data.join("backend-startup-error.txt"), &error);
                    BackendProcess {
                        child: Mutex::new(None),
                        process_group: None,
                        pid_file: app_data.join("backend.pid"),
                    }
                }
            };
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
