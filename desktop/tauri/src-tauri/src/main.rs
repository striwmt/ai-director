// AI Director desktop shell (Tauri v2).
//
// Flow:
//   1. locate the bundled resources (bootstrap.py + the Python project)
//   2. run `python bootstrap.py --no-launch` on first start (uv env setup),
//      streaming progress into a small setup window
//   3. spawn `uv run aidirector app --no-browser --port 0` as a child,
//      parse the "AI Director UI: http://..." line from stdout
//   4. open the main webview window on that URL; kill the child on exit
//
// Build (needs the Tauri prerequisites for your OS — see desktop/README.md):
//   cd desktop/tauri && cargo tauri build

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

struct Backend(Mutex<Option<Child>>);

fn python() -> &'static str {
    if cfg!(windows) { "python" } else { "python3" }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let resources = app
                .path()
                .resource_dir()
                .expect("resource dir")
                .join("app");
            let bootstrap = app
                .path()
                .resource_dir()
                .expect("resource dir")
                .join("bootstrap.py");

            // 1+2: environment setup (idempotent; slow only on first run).
            let status = Command::new(python())
                .arg(&bootstrap)
                .arg("--no-launch")
                .current_dir(&resources)
                .status()
                .expect("failed to run bootstrap.py — is Python 3 installed?");
            if !status.success() {
                panic!("environment setup failed (see console output)");
            }

            // 3: launch the backend and read the URL it prints.
            let mut child = Command::new("uv")
                .args(["run", "--no-sync", "aidirector", "app",
                       "--no-browser", "--no-window", "--port", "0"])
                .current_dir(&resources)
                .stdout(Stdio::piped())
                .spawn()
                .expect("failed to launch aidirector");

            let stdout = child.stdout.take().expect("stdout");
            let mut url = String::new();
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("AI Director UI: ") {
                    url = rest.trim().to_string();
                    break;
                }
            }
            assert!(!url.is_empty(), "backend did not report a URL");
            *app.state::<Backend>().0.lock().unwrap() = Some(child);

            // 4: main window.
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(url.parse().expect("valid URL")),
            )
            .title("AI Director")
            .inner_size(1380.0, 920.0)
            .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(mut child) = window
                    .state::<Backend>()
                    .0
                    .lock()
                    .unwrap()
                    .take()
                {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AI Director");
}
