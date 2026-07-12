use std::{
  fs::{self, OpenOptions},
  io::{Read, Write},
  net::{SocketAddr, TcpStream},
  path::PathBuf,
  process::{Child, Command, Stdio},
  str::FromStr,
  sync::Mutex,
  thread,
  time::Duration,
};

use tauri::{Manager, RunEvent, WindowEvent};

#[cfg(unix)]
use std::os::unix::process::CommandExt;

struct BackendProcess {
  child: Mutex<Option<Child>>,
  process_group: Option<i32>,
  pid_file: PathBuf,
}

impl BackendProcess {
  fn stop(&self) {
    #[cfg(unix)]
    if let Some(group) = self.process_group {
      unsafe { libc::kill(-group, libc::SIGTERM); }
      thread::sleep(Duration::from_millis(350));
      unsafe { libc::kill(-group, libc::SIGKILL); }
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

fn backend_health_ok() -> bool {
  let address = SocketAddr::from_str("127.0.0.1:8000").expect("valid backend address");
  let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(300)) else {
    return false;
  };
  let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
  if stream.write_all(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err() {
    return false;
  }
  let mut response = String::new();
  stream.read_to_string(&mut response).is_ok() && response.contains("subtitle-factory-backend")
}

fn port_is_open() -> bool {
  let address = SocketAddr::from_str("127.0.0.1:8000").expect("valid backend address");
  TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok()
}

fn development_backend_dir() -> PathBuf {
  PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend")
}

#[cfg(unix)]
fn stop_stale_process_group(pid_file: &PathBuf) {
  let Ok(value) = fs::read_to_string(pid_file) else { return; };
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

fn start_backend(app: &tauri::App) -> Result<BackendProcess, String> {
  let app_data = app.path().app_data_dir().map_err(|error| error.to_string())?;
  fs::create_dir_all(&app_data).map_err(|error| error.to_string())?;
  let pid_file = app_data.join("backend.pid");
  stop_stale_process_group(&pid_file);

  if backend_health_ok() {
    log::info!("using an existing verified subtitle backend");
    return Ok(BackendProcess { child: Mutex::new(None), process_group: None, pid_file });
  }
  if port_is_open() {
    return Err("端口 8000 已被其他程序占用，字幕后端无法启动".into());
  }

  let mut command = if cfg!(debug_assertions) {
    let backend_dir = development_backend_dir();
    let configured = std::env::var_os("SUBTITLE_FACTORY_PYTHON").map(PathBuf::from);
    let venv_python = backend_dir.join(".venv/bin/python");
    let python = configured.filter(|path| path.exists()).unwrap_or(venv_python);
    if !python.exists() {
      return Err(format!("后端 Python 环境不存在：{}", python.display()));
    }
    let mut cmd = Command::new(python);
    cmd.current_dir(backend_dir)
      .args(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]);
    cmd
  } else {
    let resource_dir = app.path().resource_dir().map_err(|error| error.to_string())?;
    let binary_name = if cfg!(target_os = "windows") { "subtitle-backend.exe" } else { "subtitle-backend" };
    let backend_binary = resource_dir.join("backend-runtime").join(binary_name);
    if !backend_binary.exists() {
      return Err(format!("App 后端组件缺失：{}", backend_binary.display()));
    }
    Command::new(backend_binary)
  };

  let log_path = app_data.join("backend.log");
  let mut log_file = OpenOptions::new().create(true).append(true).open(&log_path)
    .map_err(|error| format!("无法创建后端日志：{error}"))?;
  let _ = writeln!(log_file, "\n===== App launching backend =====");
  let error_file = log_file.try_clone().map_err(|error| error.to_string())?;

  command
    .env("SUBTITLE_FACTORY_DATA_DIR", app_data.join("data"))
    .env("PYTHONUNBUFFERED", "1")
    .stdin(Stdio::null())
    .stdout(Stdio::from(log_file))
    .stderr(Stdio::from(error_file));
  #[cfg(unix)]
  command.process_group(0);

  let child = command.spawn().map_err(|error| format!("后端启动失败：{error}"))?;
  let group = child.id() as i32;
  fs::write(&pid_file, group.to_string()).map_err(|error| format!("无法写入后端 PID：{error}"))?;
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
    .plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())
    .setup(|app| {
      let backend = match start_backend(app) {
        Ok(backend) => backend,
        Err(error) => {
          log::error!("{error}");
          let app_data = app.path().app_data_dir().map_err(|value| value.to_string())?;
          let _ = fs::write(app_data.join("backend-startup-error.txt"), &error);
          BackendProcess { child: Mutex::new(None), process_group: None, pid_file: app_data.join("backend.pid") }
        }
      };
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
