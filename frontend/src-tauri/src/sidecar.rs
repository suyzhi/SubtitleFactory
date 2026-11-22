use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::Manager;

use crate::session::BackendSession;

pub const BACKEND_STARTUP_ERROR_FILE: &str = "backend-startup-error.txt";
const BACKEND_SHUTDOWN_GRACE: Duration = Duration::from_secs(15);

#[cfg(unix)]
use std::os::unix::process::CommandExt;

#[cfg(unix)]
fn process_group_exists(group: i32) -> bool {
    unsafe { libc::kill(-group, 0) == 0 }
}

pub struct BackendProcess {
    pub child: Mutex<Option<Child>>,
    pub process_group: Option<i32>,
    pub pid_file: PathBuf,
}

impl BackendProcess {
    pub fn empty(pid_file: PathBuf) -> Self {
        Self {
            child: Mutex::new(None),
            process_group: None,
            pid_file,
        }
    }

    pub fn stop(&self) {
        // A previous panic must not turn restart into an unsafe database
        // replacement. Recover the owned child from a poisoned mutex so the
        // sidecar still receives its graceful shutdown window.
        let mut guard = self
            .child
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(child) = guard.as_mut() else {
            let _ = fs::remove_file(&self.pid_file);
            return;
        };

        #[cfg(unix)]
        if let Some(group) = self.process_group {
            unsafe {
                libc::kill(-group, libc::SIGTERM);
            }
            let deadline = Instant::now() + BACKEND_SHUTDOWN_GRACE;
            loop {
                match child.try_wait() {
                    Ok(status) if status.is_some() && !process_group_exists(group) => break,
                    Ok(_) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(50));
                    }
                    Ok(_) | Err(_) => {
                        unsafe {
                            libc::kill(-group, libc::SIGKILL);
                        }
                        let _ = child.kill();
                        break;
                    }
                }
            }
        }

        #[cfg(not(unix))]
        let _ = child.kill();

        let _ = child.wait();
        *guard = None;
        let _ = fs::remove_file(&self.pid_file);
    }
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

pub fn start_backend(
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
        let entry_point = backend_dir.join("sidecar_main.py");
        if !entry_point.is_file() {
            return Err(format!("后端入口不存在：{}", entry_point.display()));
        }
        let mut cmd = Command::new(python);
        cmd.current_dir(backend_dir).arg(entry_point);
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
        let qjs = runtime_dir.join("bin/qjs");
        let deno = runtime_dir.join("bin/deno");
        let has_js_runtime = qjs.is_file() || deno.is_file();
        if !ffmpeg.is_file() || !ffprobe.is_file() || (session.youtube_enabled && !has_js_runtime) {
            return Err(if session.youtube_enabled {
                "App 内置 FFmpeg/FFprobe/JS 运行时缺失，发布包不完整".into()
            } else {
                "App 内置 FFmpeg/FFprobe 缺失，发布包不完整".into()
            });
        }
        command
            .env("SUBTITLE_FACTORY_BUNDLED_FFMPEG", &ffmpeg)
            .env("SUBTITLE_FACTORY_BUNDLED_FFPROBE", &ffprobe)
            .env("SUBTITLE_FACTORY_RESOURCE_DIR", runtime_dir);
        if session.youtube_enabled {
            if qjs.is_file() {
                command.env("SUBTITLE_FACTORY_BUNDLED_QJS", &qjs);
            }
            if deno.is_file() {
                command.env("SUBTITLE_FACTORY_BUNDLED_DENO", &deno);
            }
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
        // Keep a private write end in the desktop process. The frozen sidecar
        // watches the read end and terminates its whole process group on EOF,
        // covering crashes and external signals that bypass Tauri RunEvent.
        .env("SUBTITLE_FACTORY_PARENT_WATCHDOG", "stdin")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
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

pub fn stop_managed_backend(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        state.stop();
    }
}

pub fn clear_stale_backend_startup_error(app_data: &Path) -> std::io::Result<()> {
    match fs::remove_file(app_data.join(BACKEND_STARTUP_ERROR_FILE)) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        clear_stale_backend_startup_error, BackendProcess, BACKEND_STARTUP_ERROR_FILE,
    };
    use std::{
        fs,
        process::{Command, Stdio},
        sync::Mutex,
        thread,
        time::Duration,
    };

    #[cfg(unix)]
    use std::os::unix::process::CommandExt;

    fn isolated_test_dir(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("subtitle-factory-{label}-{}", uuid::Uuid::new_v4()))
    }

    #[cfg(unix)]
    #[test]
    fn managed_backend_gets_a_graceful_shutdown_window() {
        let test_dir = isolated_test_dir("backend-stop-test");
        fs::create_dir_all(&test_dir).expect("create backend stop test directory");
        let marker = test_dir.join("graceful.txt");
        let pid_file = test_dir.join("backend.pid");
        let child = Command::new("/bin/sh")
            .args([
                "-c",
                "trap 'printf graceful > \"$1\"; exit 0' TERM; while :; do sleep 1; done",
                "backend-stop-test",
            ])
            .arg(&marker)
            .process_group(0)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn test process group");
        let group = child.id() as i32;
        fs::write(&pid_file, group.to_string()).expect("write test pid file");
        thread::sleep(Duration::from_millis(100));

        BackendProcess {
            child: Mutex::new(Some(child)),
            process_group: Some(group),
            pid_file: pid_file.clone(),
        }
        .stop();

        assert_eq!(
            fs::read_to_string(&marker).expect("graceful marker"),
            "graceful"
        );
        assert!(!pid_file.exists());
        fs::remove_dir_all(&test_dir).expect("remove backend stop test directory");
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
}
