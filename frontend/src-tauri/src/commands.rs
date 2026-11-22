use std::{io::Write, path::Path, process::Command};

use tauri::State;

use crate::{
    fs_ops::{
        choose_save_destination, safe_suggested_name, stream_copy, validate_managed_path,
        write_atomically, ManagedFiles,
    },
    session::BackendSession,
};

#[tauri::command]
pub fn backend_session(session: State<'_, BackendSession>) -> BackendSession {
    session.inner().clone()
}

#[tauri::command]
pub fn reveal_path(path: String, managed_files: State<'_, ManagedFiles>) -> Result<(), String> {
    let candidate = validate_managed_path(&managed_files.root, Path::new(&path), false)?;
    Command::new("/usr/bin/open")
        .arg(&candidate)
        .status()
        .map_err(|error| error.to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| "无法在 Finder 中打开路径".into())
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

#[tauri::command]
pub async fn save_text_file(
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
