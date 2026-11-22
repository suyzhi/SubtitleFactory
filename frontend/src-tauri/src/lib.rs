mod commands;
mod fs_ops;
mod session;
mod sidecar;

use std::fs;

use tauri::{Manager, RunEvent, WindowEvent};

use crate::{
    commands::{backend_session, restart_app, reveal_path, save_managed_file, save_text_file},
    fs_ops::ManagedFiles,
    session::{backend_data_dir, create_backend_session},
    sidecar::{
        clear_stale_backend_startup_error, start_backend, stop_managed_backend, BackendProcess,
        BACKEND_STARTUP_ERROR_FILE,
    },
};

const PROFESSIONAL_UI_MARKER: &str = "subtitle-factory-ui:professional-v2";
const LIBRARY_WORKSPACE_UI_MARKER: &str = "subtitle-factory-ui:library-workspace-v2";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            backend_session,
            restart_app,
            reveal_path,
            save_managed_file,
            save_text_file,
        ])
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
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
                    BackendProcess::empty(app_data.join("backend.pid"))
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
