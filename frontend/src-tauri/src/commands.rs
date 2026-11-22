use std::path::Path;

use tauri::State;

use crate::{
    fs_ops::{
        choose_save_destination, safe_suggested_name, stream_copy, validate_managed_path,
        ManagedFiles,
    },
    session::BackendSession,
};

#[tauri::command]
pub fn backend_session(session: State<'_, BackendSession>) -> BackendSession {
    session.inner().clone()
}

#[tauri::command]
pub async fn save_managed_file(
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
