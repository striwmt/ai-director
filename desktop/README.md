# AI Director — desktop

Two ways to run AI Director as a desktop app on Windows / Linux.

## 1. App window via `aidirector app` (works today)

`aidirector app` opens a **standalone app window** (Chromium app mode —
Edge on Windows is always available; Chrome/Chromium/Brave elsewhere).
Closing the window shuts the backend down. Falls back to a normal browser
tab when no Chromium-family browser exists.

```bash
aidirector app                # auto: app window if possible
aidirector app --no-window    # force browser tab
```

### One-command setup on a fresh machine

`bootstrap.py` needs only a system Python 3.9+ (no pip, no venv knowledge):

```bash
git clone <repo> && cd ai-director
python desktop/bootstrap.py           # Windows: py desktop\bootstrap.py
```

It downloads `uv` if missing, builds the locked environment with the
local-model extras (first run downloads several GB), and launches
`aidirector app`. Also needed on PATH: `ffmpeg`/`ffprobe`
(Windows: `winget install Gyan.FFmpeg`), and either `llama-server`
(`winget install ggml.llamacpp`) or set `models.director.provider:
llama-server` with `extra.binary` pointing at the executable — AI Director
then manages the server process itself.

## 2. Installers (`installer/`)

**Linux AppImage** — built and verified on this repo's dev machine:

```bash
python scripts/generate_third_party_licenses.py   # license bundle (run in the synced env)
installer/appimage/build.sh                       # → dist/AIDirector-<ver>-x86_64.AppImage
```

The AppImage (~21 MB) carries the project, a static `uv`, and
`THIRD_PARTY_LICENSES.md`. On first run it installs itself into
`~/.local/share/aidirector`, builds the locked Python environment (several
GB download on a fresh machine), and launches the app window. `ffmpeg`
must be on PATH; the Director LLM can be self-managed via
`provider: llama-server`.

**Windows setup.exe** — `installer/windows/` holds the NSIS script and the
payload stager; `.github/workflows/installers.yml` builds
`AIDirector-Setup.exe` on a Windows runner (user-level install, Start-Menu
shortcut, uninstaller; bundles `uv.exe`, no admin rights needed). Tag a
release (`v*`) or run the workflow manually to get both installers plus
the license bundle as artifacts.

Licensing note: neither installer bundles ffmpeg, CUDA libraries, vendor
LUTs, or Windows fonts — models and Python packages are fetched at first
run from their original sources (see `THIRD_PARTY_LICENSES.md`).

## 3. Tauri shell (`desktop/tauri`) — native webview builds

A Tauri v2 project that bundles `bootstrap.py` + the Python project as
resources, runs the environment setup on first start, launches the backend
sidecar, and shows its UI in a native webview. Produces MSI/NSIS
installers on Windows and AppImage/deb on Linux.

**Status: template.** It cannot be compiled on a machine without the Tauri
prerequisites (this repo's dev machine lacks `webkit2gtk-4.1` devel
packages), so treat it as the starting point and build via CI or a
prepared machine:

```bash
# Linux prerequisites (see https://tauri.app/start/prerequisites/):
#   openSUSE: sudo zypper in webkit2gtk3-devel libopenssl-devel curl wget file
#   Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev build-essential \
#       curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
# Windows: VS Build Tools (C++), WebView2 runtime (preinstalled on Win 11)

cargo install tauri-cli --version '^2'
cd desktop/tauri
cargo tauri build
```

`.github/workflows/desktop.yml` builds installers for both OSes on tags.

### Runtime requirements of the installed app

The shell still expects `python3` and (after first run) network access for
model downloads; `ffmpeg` and `llama-server` follow the same rules as
above. Fully self-contained bundles (embedding Python + ffmpeg +
llama-server binaries) are the next step — wire them into
`tauri.conf.json` `bundle.resources` and point `bootstrap.py` /
`models.director.extra.binary` at the bundled paths.
