; Windows 설치 파일(DataLoupe-setup.exe)을 만든다. 먼저 pyinstaller DataLoupe.spec로 dist\DataLoupe를 만든 뒤:
;   iscc /DAppVersion=1.0.0 installer\DataLoupe.iss   → installer\Output\DataLoupe-setup.exe
; 설치 위치는 고를 수 있고(기본: 관리자 권한 없이 이 사용자의 프로그램 폴더), 바탕화면 바로가기는 체크로 정한다.
; 설정·대시보드는 %APPDATA%\DataLoupe에 있어 지우거나 다시 설치해도 남는다.
; 설치할 때 기능(쿼리·분석·대시보드·수집·연동)을 고른다(기본 전부, 하나는 골라야 함). 고른 기능은 {app}\features.ini에 적혀 앱이 숨길 기능을 안다.
; 앱의 [설정]에서 기능을 바꾸면 앱이 같은 버전의 setup.exe를 /SILENT /COMPONENTS=… /DIR=… /RELAUNCH 로 실행한다(/RELAUNCH가 있을 때만 설치 뒤 앱을 다시 연다).
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
SetupIconFile=installer\DataLoupe.ico
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Types]
Name: "full"; Description: "모든 기능"
Name: "custom"; Description: "사용자 지정"; Flags: iscustom

[Components]
Name: "query"; Description: "쿼리 (DB·파일 탐색, SQL, 지표, 차트)"; Types: full
Name: "prep"; Description: "분석 (전처리, 분포·상관·비교·추이)"; Types: full
Name: "dashboard"; Description: "대시보드 (차트·KPI 모아 보기)"; Types: full
Name: "collect"; Description: "수집 (웹·파일 받기, DB·Kafka 적재)"; Types: full
Name: "integrations"; Description: "연동 (Slack·Notion·GA4·LLM)"; Types: full

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
; 기능 전용 파일(Kafka 적재용 confluent_kafka)은 수집을 고른 때만 설치하고, 나머지는 늘 설치한다.
Source: "dist\DataLoupe\*"; DestDir: "{app}"; Excludes: "\_internal\confluent_kafka,\_internal\confluent_kafka.libs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\DataLoupe\_internal\confluent_kafka\*"; DestDir: "{app}\_internal\confluent_kafka"; Components: collect; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\DataLoupe\_internal\confluent_kafka.libs\*"; DestDir: "{app}\_internal\confluent_kafka.libs"; Components: collect; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
; 앱이 읽는 설치 정보(core/features.py): 버전과 고른 기능
Filename: "{app}\features.ini"; Section: "app"; Key: "version"; String: "{#AppVersion}"
Filename: "{app}\features.ini"; Section: "app"; Key: "features"; String: "{code:SelectedFeatures}"

[Icons]
Name: "{autoprograms}\Data Loupe"; Filename: "{app}\DataLoupe.exe"
Name: "{autodesktop}\Data Loupe"; Filename: "{app}\DataLoupe.exe"; Tasks: desktopicon

[Run]
; 옮길 예약이 있을 때만(예전 폴더의 schedules.json, 또는 새 폴더에 있고 옮김 표식이 없을 때), 원래 사용자 권한으로. 업그레이드로 예전 ibis_search.exe가 지워지므로, 예전 이름으로 등록한 예약 작업을 설치가 끝날 때 새 exe로 옮긴다(앱을 열기 전에도 예약이 돌게). 조용한 설치에서도 돈다.
Filename: "{app}\DataLoupe.exe"; Parameters: "--move-tasks"; Flags: runhidden waituntilterminated runasoriginaluser; Check: HasTasksToMove
Filename: "{app}\DataLoupe.exe"; Description: "{cm:LaunchProgram,Data Loupe}"; Flags: nowait postinstall skipifsilent
; 앱의 [설정]이 /RELAUNCH를 달아 조용히 실행했을 때만 설치가 끝난 뒤 앱을 다시 연다.
Filename: "{app}\DataLoupe.exe"; Flags: nowait runasoriginaluser; Check: HasRelaunch

[UninstallDelete]
Type: files; Name: "{app}\features.ini"

[UninstallRun]
; 앱에서 등록한 Windows 예약 작업(작업 스케줄러의 \DataLoupe\ 폴더 안)만 지운다. 그 폴더 밖의 작업은 건드리지 않는다. 없으면 아무 일도 하지 않는다.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""Get-ScheduledTask -TaskPath '\DataLoupe\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDataLoupeTasks"

[Code]
function SelectedFeatures(Param: String): String;
var
  Ids: array[0..4] of String;
  I: Integer;
begin
  Ids[0] := 'query'; Ids[1] := 'prep'; Ids[2] := 'dashboard'; Ids[3] := 'collect'; Ids[4] := 'integrations';
  Result := '';
  for I := 0 to 4 do
    if WizardIsComponentSelected(Ids[I]) then
    begin
      if Result <> '' then Result := Result + ',';
      Result := Result + Ids[I];
    end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectComponents) and (SelectedFeatures('') = '') then
  begin
    MsgBox('기능을 하나 이상 고르세요.', mbError, MB_OK);
    Result := False;
  end;
end;

function HasRelaunch: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then Result := True;
end;

function HasTasksToMove: Boolean;
begin
  Result := FileExists(ExpandConstant('{userappdata}\ibis_search\schedules.json')) or
    (FileExists(ExpandConstant('{userappdata}\DataLoupe\schedules.json')) and
     not FileExists(ExpandConstant('{userappdata}\DataLoupe\old_tasks_moved.json')));
end;
