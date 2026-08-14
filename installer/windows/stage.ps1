# Stage the Windows installer payload (run before makensis).
#   powershell -File installer/windows/stage.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$stage = "$PSScriptRoot\stage"

Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\app\desktop" | Out-Null
New-Item -ItemType Directory -Force "$stage\bin" | Out-Null

# App payload
foreach ($item in @("pyproject.toml", "uv.lock", "src", "config", "assets",
                    "README.md", "README.ja.md", "LICENSE", "AGENT.md",
                    "THIRD_PARTY_LICENSES.md")) {
    Copy-Item -Recurse -Force "$root\$item" "$stage\app\"
}
Copy-Item "$root\desktop\bootstrap.py" "$stage\app\desktop\"

# Standalone uv
$uvZip = "$PSScriptRoot\uv.zip"
Invoke-WebRequest -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -OutFile $uvZip
Expand-Archive -Force $uvZip "$stage\bin"
Remove-Item $uvZip
Get-ChildItem -Recurse "$stage\bin" -Filter uv.exe |
    Select-Object -First 1 | Move-Item -Destination "$stage\bin\uv.exe" -Force -ErrorAction SilentlyContinue

Write-Host "staged payload at $stage"
