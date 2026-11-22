use std::{
    net::TcpListener,
    path::{Path, PathBuf},
};

use serde::Serialize;
use uuid::Uuid;

pub const DIRECT_DISTRIBUTION_CHANNEL: &str = "direct";
pub const APP_STORE_DISTRIBUTION_CHANNEL: &str = "app_store";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendSession {
    pub base_url: String,
    pub token: String,
    pub port: u16,
    pub distribution_channel: String,
    pub youtube_enabled: bool,
    pub filesystem_automation_enabled: bool,
    pub external_runtime_paths_enabled: bool,
}

pub fn distribution_channel() -> &'static str {
    match option_env!("SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL") {
        Some(APP_STORE_DISTRIBUTION_CHANNEL) => APP_STORE_DISTRIBUTION_CHANNEL,
        _ => DIRECT_DISTRIBUTION_CHANNEL,
    }
}

pub fn create_backend_session() -> Result<BackendSession, String> {
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

pub fn backend_data_dir(app_data: &Path, session: &BackendSession) -> PathBuf {
    if session.distribution_channel == APP_STORE_DISTRIBUTION_CHANNEL {
        app_data.join("data")
    } else {
        std::env::var_os("SUBTITLE_FACTORY_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| app_data.join("data"))
    }
}
