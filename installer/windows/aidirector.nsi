; AI Director — Windows installer (NSIS).
; Built by CI (see .github/workflows/installers.yml), which stages the
; payload into installer\windows\stage\ first:
;   stage\app\...      project source, config, licenses
;   stage\bin\uv.exe   standalone uv
Unicode true
!include "MUI2.nsh"

Name "AI Director"
OutFile "..\..\dist\AIDirector-Setup.exe"
InstallDir "$LOCALAPPDATA\AIDirector"
RequestExecutionLevel user

!define MUI_ICON "aidirector.ico"
!define MUI_UNICON "aidirector.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "stage\app\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "English"

Section "AI Director"
  SetOutPath "$INSTDIR"
  File /r "stage\app"
  File /r "stage\bin"
  File "aidirector.ico"

  ; Launcher: uv sync is a fast no-op once the env exists; the first run
  ; downloads the Python environment (several GB).
  FileOpen $0 "$INSTDIR\AIDirector.cmd" w
  FileWrite $0 '@echo off$\r$\n'
  FileWrite $0 'cd /d "%~dp0app"$\r$\n'
  FileWrite $0 '"%~dp0bin\uv.exe" sync --frozen --no-dev --extra speech --extra vision --extra embedding --extra web || (pause & exit /b 1)$\r$\n'
  FileWrite $0 '"%~dp0bin\uv.exe" run --no-sync aidirector app$\r$\n'
  FileWrite $0 'if errorlevel 1 (echo AI Director exited with an error - see the messages above. & pause)$\r$\n'
  FileClose $0

  CreateDirectory "$SMPROGRAMS\AI Director"
  CreateShortcut "$SMPROGRAMS\AI Director\AI Director.lnk" "$INSTDIR\AIDirector.cmd" "" "$INSTDIR\aidirector.ico"
  CreateShortcut "$SMPROGRAMS\AI Director\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIDirector" \
      "DisplayName" "AI Director"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIDirector" \
      "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIDirector" \
      "DisplayIcon" "$INSTDIR\aidirector.ico"
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  RMDir /r "$SMPROGRAMS\AI Director"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIDirector"
SectionEnd
