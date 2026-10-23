; Windows 설치 파일(ibis_search-setup.exe)을 만든다. 먼저 pyinstaller ibis_search.spec로 dist\ibis_search를 만든 뒤:
;   iscc /DAppVersion=1.0.0 installer\ibis_search.iss   → installer\Output\ibis_search-setup.exe
; 설치 위치는 고를 수 있고(기본: 관리자 권한 없이 이 사용자의 프로그램 폴더), 바탕화면 바로가기는 체크로 정한다.
; 설정·대시보드는 %APPDATA%\ibis_search에 있어 지우거나 다시 설치해도 남는다.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{74FC866E-EB80-4BCB-B362-AB58D07EA6FE}
AppName=Ibis Search
AppVersion={#AppVersion}
DefaultDirName={autopf}\ibis_search
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SourceDir=..
OutputDir=installer\Output
OutputBaseFilename=ibis_search-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\ibis_search.exe
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 새 버전에서 빠진 파일이 남지 않게 이전 버전의 묶음 폴더를 먼저 지운다(설정은 %APPDATA%에 있어 지우지 않는다).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\ibis_search\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Ibis Search"; Filename: "{app}\ibis_search.exe"
Name: "{autodesktop}\Ibis Search"; Filename: "{app}\ibis_search.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ibis_search.exe"; Description: "{cm:LaunchProgram,Ibis Search}"; Flags: nowait postinstall skipifsilent
