; Windows 설치 파일(DataLoupe-setup.exe)을 만든다. 먼저 pyinstaller DataLoupe.spec로 dist\DataLoupe를 만든 뒤:
;   iscc /DAppVersion=1.0.0 installer\DataLoupe.iss   → installer\Output\DataLoupe-setup.exe
; 설치 위치는 고를 수 있고(기본: 관리자 권한 없이 이 사용자의 프로그램 폴더), 바탕화면 바로가기는 체크로 정한다.
; 설정·대시보드는 %APPDATA%\DataLoupe에 있어 지우거나 다시 설치해도 남는다.
; AppId는 예전 이름(ibis_search) 때와 같아 예전 설치 위에 덮어 설치된다.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{74FC866E-EB80-4BCB-B362-AB58D07EA6FE}
AppName=Data Loupe
AppVersion={#AppVersion}
DefaultDirName={autopf}\DataLoupe
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SourceDir=..
OutputDir=installer\Output
OutputBaseFilename=DataLoupe-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\DataLoupe.exe
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 새 버전에서 빠진 파일이 남지 않게 이전 버전의 묶음 폴더를 먼저 지운다(설정은 %APPDATA%에 있어 지우지 않는다).
Type: filesandordirs; Name: "{app}\_internal"
; 예전 이름(ibis_search)으로 설치했던 실행 파일과 바로 가기
Type: files; Name: "{app}\ibis_search.exe"
Type: files; Name: "{autoprograms}\Ibis Search.lnk"
Type: files; Name: "{autodesktop}\Ibis Search.lnk"

[Files]
Source: "dist\DataLoupe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Data Loupe"; Filename: "{app}\DataLoupe.exe"
Name: "{autodesktop}\Data Loupe"; Filename: "{app}\DataLoupe.exe"; Tasks: desktopicon

[Run]
; 옮길 예약이 있을 때만(예전 폴더의 schedules.json, 또는 새 폴더에 있고 옮김 표식이 없을 때), 원래 사용자 권한으로. 업그레이드로 예전 ibis_search.exe가 지워지므로, 예전 이름으로 등록한 예약 작업을 설치가 끝날 때 새 exe로 옮긴다(앱을 열기 전에도 예약이 돌게). 조용한 설치에서도 돈다.
Filename: "{app}\DataLoupe.exe"; Parameters: "--move-tasks"; Flags: runhidden waituntilterminated runasoriginaluser; Check: HasTasksToMove
Filename: "{app}\DataLoupe.exe"; Description: "{cm:LaunchProgram,Data Loupe}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 앱에서 등록한 Windows 예약 작업(작업 스케줄러의 \DataLoupe\ 폴더 안)만 지운다. 그 폴더 밖의 작업은 건드리지 않는다. 없으면 아무 일도 하지 않는다.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""Get-ScheduledTask -TaskPath '\DataLoupe\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDataLoupeTasks"

[Code]
function HasTasksToMove: Boolean;
begin
  Result := FileExists(ExpandConstant('{userappdata}\ibis_search\schedules.json')) or
    (FileExists(ExpandConstant('{userappdata}\DataLoupe\schedules.json')) and
     not FileExists(ExpandConstant('{userappdata}\DataLoupe\old_tasks_moved.json')));
end;
