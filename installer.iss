; Inno Setup Script for Lecture Digest Sync (課堂錄音自動化 ASR 與重點筆記系統)
#define MyAppName "課堂錄音自動化 ASR 與重點筆記系統"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Senrinel"
#define MyAppURL "https://github.com/Senrinel/lecture-digest-sync"
#define MyAppExeName "LecturePipeline.exe"

[Setup]
AppId={{C78A4D2E-2A4B-4E38-9D22-C8F1E291B012}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppMutex=LecturePipelineSingleInstanceMutex
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=yes
DefaultDirName={autopf}\LecturePipeline
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=Setup_LecturePipeline_v0.1.0
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Messages]
default.BeveledLabel=課堂錄音自動化 ASR 與重點筆記系統

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑 (開啟操作介面，支援拖曳音檔)"; GroupDescription: "捷徑設定："; Flags: checkedonce
Name: "sendtomenu"; Description: "建立右鍵「傳送到 (SendTo)」選單 (音檔右鍵傳送整理)"; GroupDescription: "捷徑設定："; Flags: checkedonce
Name: "autostart"; Description: "開機自動啟動背景監控服務"; GroupDescription: "自動化設定："; Flags: checkedonce

[Files]
Source: "dist\LecturePipeline\*"; DestDir: "{app}"; Excludes: "config.yaml,token.json,.env,.env.example,credentials.json"; Flags: ignoreversion recursesubdirs createallsubdirs restartreplace
Source: "dist\LecturePipeline\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "dist\LecturePipeline\.env.example"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\LecturePipeline\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "dist\LecturePipeline\credentials.json"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist

[Icons]
Name: "{autodesktop}\課堂錄音自動化系統"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\icon.ico"; Comment: "啟動課堂錄音系統 (支援拖曳音檔)"
Name: "{group}\課堂錄音自動化系統"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
Name: "{userappdata}\Microsoft\Windows\SendTo\課堂錄音自動整理"; Filename: "{app}\{#MyAppExeName}"; Tasks: sendtomenu; IconFilename: "{app}\icon.ico"; Comment: "傳送至課堂錄音整理程式"
Name: "{userstartup}\課堂錄音自動化背景監控"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--mode web"; Tasks: autostart; IconFilename: "{app}\icon.ico"; Comment: "開機自動啟動課堂錄音背景監控"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--init-paths ""{code:GetInboxDir}"" ""{code:GetExportsDir}"" ""{code:GetCoursesDir}"" ""{code:GetRecordArchiveDir}"""; Check: ShouldInitPaths; StatusMsg: "正在初始化資料夾並檢查執行環境..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Parameters: "--repair"; Check: ShouldRepairOnly; StatusMsg: "正在檢測與自動修復既有環境設定..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "立即啟動課堂錄音系統（初次安裝可透過介面精靈設定 API 金鑰）"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\dist"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  HadExistingConfig: Boolean;

function ShouldInitPaths: Boolean;
begin
  Result := not HadExistingConfig;
end;

function ShouldRepairOnly: Boolean;
begin
  Result := HadExistingConfig;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  HadExistingConfig := FileExists(ExpandConstant('{app}\config.yaml'));
  // 自動結束背景駐留的 LecturePipeline.exe，防止覆蓋更新或自我修復時發生檔案鎖定錯誤
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(400);
  Result := '';
end;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '設定系統分區資料夾',
    '請選擇各分區資料夾的存放位置（若不修改則使用預設值）',
    '系統會自動檢查並建立下列目錄；若為重新覆蓋安裝，既有筆記、設定檔 (config.yaml) 與登入憑證 (token.json) 均會完整保留，損毀設定檔則會自動備份並重建。' + #13#10 +
    '提示：本程式已內建獨立版 FFmpeg 音訊引擎，完成安裝並填入 Gemini API 金鑰即可使用。',
    False, '');

  DataDirPage.Add('錄音監控收件資料夾 (Inbox)：');
  DataDirPage.Add('筆記與播放器匯出資料夾 (Exports)：');
  DataDirPage.Add('科目專有名詞詞庫資料夾 (Courses)：');
  DataDirPage.Add('課堂錄音長期封存資料夾 (Record Archive)：');

  DataDirPage.Values[0] := ExpandConstant('{userdocs}\課堂錄音\收件匣');
  DataDirPage.Values[1] := ExpandConstant('{userdocs}\課堂錄音\筆記匯出');
  DataDirPage.Values[2] := ExpandConstant('{userdocs}\課堂錄音\科目詞庫');
  DataDirPage.Values[3] := ExpandConstant('{userdocs}\課堂錄音\錄音封存');
end;

function GetInboxDir(Param: String): String;
begin
  Result := DataDirPage.Values[0];
end;

function GetExportsDir(Param: String): String;
begin
  Result := DataDirPage.Values[1];
end;

function GetCoursesDir(Param: String): String;
begin
  Result := DataDirPage.Values[2];
end;

function GetRecordArchiveDir(Param: String): String;
begin
  Result := DataDirPage.Values[3];
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  InboxDir, ExportsDir, CoursesDir, ArchiveDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    InboxDir := DataDirPage.Values[0];
    ExportsDir := DataDirPage.Values[1];
    CoursesDir := DataDirPage.Values[2];
    ArchiveDir := DataDirPage.Values[3];

    ForceDirectories(InboxDir);
    ForceDirectories(ExportsDir);
    ForceDirectories(CoursesDir);
    ForceDirectories(ArchiveDir);
  end;
end;
