use std::path::PathBuf;
use std::process::Command;

/// Resolve the Python sidecar (api.py).
///
/// Dev: resolved relative to the crate, so it works regardless of the runtime
/// working directory. Production: override with APP_AUDIT_API to point at the
/// bundled PyInstaller binary / resource (see build-sidecar.sh).
fn api_script_path() -> PathBuf {
    if let Ok(p) = std::env::var("APP_AUDIT_API") {
        return PathBuf::from(p);
    }
    // CARGO_MANIFEST_DIR = <project>/desktop/src-tauri  →  <project>/api.py
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|p| p.parent())
        .map(|root| root.join("api.py"))
        .unwrap_or_else(|| PathBuf::from("api.py"))
}

/// Shell out to the Python sidecar and return its raw JSON string.
///
/// The frontend parses the `{ "ok": bool, ... }` envelope itself, so we pass
/// the sidecar's stdout through verbatim — including its structured errors.
#[tauri::command]
fn run_api(command: String, args: String) -> Result<String, String> {
    let python = std::env::var("APP_AUDIT_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let script = api_script_path();

    let output = Command::new(&python)
        .arg(&script)
        .arg(&command)
        .arg(&args)
        .output()
        .map_err(|e| format!("failed to launch sidecar ({python} {script:?}): {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    // api.py emits a JSON error envelope to stdout even on non-zero exit;
    // prefer it over a raw process error so the UI can show the real message.
    if !stdout.trim().is_empty() {
        return Ok(stdout);
    }
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        return Err(format!("sidecar exited {}: {stderr}", output.status));
    }
    Ok(stdout)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![run_api])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
