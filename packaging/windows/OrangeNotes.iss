#ifndef BundleDir
  #error Pass the absolute PyInstaller onedir path through /DBundleDir.
#endif
#ifndef ProductVersion
  #error Pass the three-part Orange Notes version through /DProductVersion.
#endif

#define InstallId "OrangeNotes.Desktop.Windows"
#define AppExe "OrangeNotes.exe"

[Setup]
AppId={#InstallId}
AppName=Orange Notes
AppVersion={#ProductVersion}
AppPublisher=Orange Notes
AppPublisherURL=https://github.com/kirill548/OrangeNotes
DefaultDirName={autopf}\Orange Notes
AppendDefaultDirName=yes
AllowRootDirectory=no
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousPrivileges=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupMutex=OrangeNotes.Desktop.Windows.Setup
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile={#BundleDir}\_internal\app\assets\app.ico
OutputBaseFilename=OrangeNotes-Windows-AMD64-Setup
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
en.DesktopIcon=Create a desktop shortcut
ru.DesktopIcon=Создать ярлык на рабочем столе
en.ForeignFolder=This folder already contains files with the same names as Orange Notes program files. Choose a separate folder to keep those files intact.
ru.ForeignFolder=В этой папке уже есть файлы с теми же именами, что у файлов Orange Notes. Выберите отдельную папку, чтобы сохранить существующие файлы.
en.FolderUnavailable=No write access to "%1".%n%nChoose a folder you can write to on this drive or another one.
ru.FolderUnavailable=Нет доступа на запись в папку «%1».%n%nВыберите доступную папку на этом или другом диске.
en.AdminOption=For a protected folder, close Setup and reopen it with "Install for all users" to request administrator access.
ru.AdminOption=Для защищённой папки перезапустите мастер, выберите «Установить для всех пользователей» и подтвердите запрос Windows.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[Dirs]
Name: "{app}\ai"; Flags: uninsneveruninstall
Name: "{app}\stt"; Flags: uninsneveruninstall

[Files]
Source: "{#BundleDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\edition.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BundleDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Orange Notes"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; AppUserModelID: "OrangeNotes.Desktop"
Name: "{autodesktop}\Orange Notes"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; AppUserModelID: "OrangeNotes.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,Orange Notes}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function HasSwitch(const Value: String): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function InitializeSetup(): Boolean;
begin
  Result := not HasSwitch('/FORCECLOSEAPPLICATIONS');
  if WizardSilent and not HasSwitch('/NOCLOSEAPPLICATIONS') then
    Result := False;
  if not Result then
    Log('Force closing applications is disabled. Silent setup requires /NOCLOSEAPPLICATIONS.');
end;

function Exists(const FileName: String): Boolean;
begin
  Result := FileExists(FileName) or DirExists(FileName);
end;

function CreateFileW(FileName: String; Access, ShareMode: LongWord;
  Security: LongWord; Creation, Flags: LongWord; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';

function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

const
  GenericWrite = $40000000;
  CreateNew = 1;
  DeleteOnClose = $04000000;

function CanWriteFolder(const Folder: String; FolderExists: Boolean): Boolean;
var
  Probe, FileProbe: String;
  Handle: THandle;
begin
  Result := False;
  Probe := GenerateUniqueName(AddBackslash(Folder), '.tmp');
  if not CreateDir(Probe) then
    Exit;
  try
    if FolderExists then
      FileProbe := GenerateUniqueName(AddBackslash(Folder), '.tmp')
    else
      FileProbe := AddBackslash(Probe) + 'write-test.tmp';
    Handle := CreateFileW(FileProbe, GenericWrite, 0, 0, CreateNew, DeleteOnClose, 0);
    if Handle <> THandle(-1) then
      Result := CloseHandle(Handle);
  finally
    if not RemoveDir(Probe) then
      Log('Could not remove the empty destination probe: ' + Probe);
  end;
end;

function DestinationError(const Folder: String): String;
var
  Parent, NextParent: String;
begin
  Result := FmtMessage(CustomMessage('FolderUnavailable'), [Folder]);
  if not IsAdminInstallMode then
    Result := Result + #13#10#13#10 + CustomMessage('AdminOption');
  Parent := RemoveBackslashUnlessRoot(Folder);
  while not DirExists(Parent) do
  begin
    if FileExists(Parent) then
      Exit;
    NextParent := ExtractFileDir(Parent);
    if (NextParent = '') or (NextParent = Parent) then
      Exit;
    Parent := NextParent;
  end;
  if CanWriteFolder(Parent, DirExists(Folder)) then
    Result := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  Problem := DestinationError(WizardDirValue);
  if Problem <> '' then
  begin
    Log('Destination check rejected the installation folder: ' + WizardDirValue);
    SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Folder, InstalledFolder, Base: String;
begin
  Folder := WizardDirValue;
  Result := DestinationError(Folder);
  if Result <> '' then
  begin
    Log('Destination check rejected the installation folder: ' + Folder);
    Exit;
  end;
  if RegQueryStringValue(HKA, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#InstallId}_is1',
    'InstallLocation', InstalledFolder) then
  begin
    if CompareText(RemoveBackslashUnlessRoot(Folder), RemoveBackslashUnlessRoot(InstalledFolder)) = 0 then
      Exit;
  end;
  Base := AddBackslash(Folder);
  if Exists(Base + '{#AppExe}') or Exists(Base + '_internal') or Exists(Base + 'licenses') or
     Exists(Base + 'edition.json') or Exists(Base + 'README.txt') then
    Result := CustomMessage('ForeignFolder');
end;
