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
DefaultDirName={autopf}\LecturePipeline
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=Setup_LecturePipeline_v0.1.0
SetupIconFile=icon.ico
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
Name: "desktopicon"; Description: "建立桌面捷徑 (雙擊開啟視覺化操作介面，支援直接拖曳音檔整理)"; GroupDescription: "捷徑設定："; Flags: checkedonce
Name: "sendtomenu"; Description: "建立滑鼠右鍵「傳送到 (SendTo)」選單 (於任何音檔按右鍵即可送入自動整理)"; GroupDescription: "捷徑設定："; Flags: checkedonce
Name: "autostart"; Description: "開機自動於系統列啟動背景監控服務 (自動監控收件資料夾音檔)"; GroupDescription: "自動化設定："; Flags: unchecked

[Files]
Source: "dist\LecturePipeline\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\課堂錄音自動化系統"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\icon.ico"; Comment: "啟動課堂錄音自動化系統 (支援拖曳音檔整理)"
Name: "{group}\課堂錄音自動化系統"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
Name: "{userappdata}\Microsoft\Windows\SendTo\課堂錄音自動整理"; Filename: "{app}\{#MyAppExeName}"; Tasks: sendtomenu; IconFilename: "{app}\icon.ico"; Comment: "傳送至課堂錄音自動整理管線"
Name: "{userstartup}\課堂錄音自動化背景監控"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--mode web"; Tasks: autostart; IconFilename: "{app}\icon.ico"; Comment: "開機自動啟動課堂錄音背景監控"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--init-paths ""{code:GetInboxDir}"" ""{code:GetExportsDir}"" ""{code:GetCoursesDir}"""; Flags: runhidden waituntilidle
Filename: "{app}\{#MyAppExeName}"; Description: "立即啟動課堂錄音自動化系統"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\dist"

[Code]
var
  DataDirPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '設定系統分區資料夾',
    '請選擇各分區資料夾的存放位置（若不修改則使用預設值）',
    '系統會自動監控收件資料夾，並將整理好的筆記與專用播放器輸出至匯出資料夾。' + #13#10 +
    '專案原始碼在安裝完成後完全無須保留，日常運作僅依賴上述資料夾與執行檔。',
    False, '');

  DataDirPage.Add('錄音監控收件資料夾 (Inbox)：');
  DataDirPage.Add('筆記與播放器匯出資料夾 (Exports)：');
  DataDirPage.Add('科目專有名詞詞庫資料夾 (Courses)：');

  DataDirPage.Values[0] := ExpandConstant('{userdocs}\課堂錄音\收件匣');
  DataDirPage.Values[1] := ExpandConstant('{userdocs}\課堂錄音\筆記匯出');
  DataDirPage.Values[2] := ExpandConstant('{userdocs}\課堂錄音\科目詞庫');
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

procedure CurStepChanged(CurStep: TSetupStep);
var
  InboxDir, ExportsDir, CoursesDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    InboxDir := DataDirPage.Values[0];
    ExportsDir := DataDirPage.Values[1];
    CoursesDir := DataDirPage.Values[2];

    ForceDirectories(InboxDir);
    ForceDirectories(ExportsDir);
    ForceDirectories(CoursesDir);
  end;
end;
