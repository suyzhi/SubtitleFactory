fn main() {
    println!("cargo:rerun-if-env-changed=SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL");
    tauri_build::build()
}
