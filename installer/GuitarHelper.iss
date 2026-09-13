; Inno Setup script for Guitar Helper.
; See docs/new feature specs/Packaging_and_Update_Strategy.md section 5.
;
; Per-user install by design: it needs no UAC elevation, and it keeps the app
; out of Program Files, which a standard user cannot write to. Nothing in the
; install directory is written at runtime except audio-separator's model dir,
; which is writable precisely because this is a per-user install. All user data
; lives in %LOCALAPPDATA%\GuitarHelper, which this installer must never touch.
;
; Build with:  ISCC /DAppVersion=1.0.0 installer\GuitarHelper.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "Guitar Helper"
#define AppExeName "GuitarHelper.exe"
#define AppPublisher "Aryan Kumar"
#define DataDirName "GuitarHelper"

[Setup]
; Never change AppId: it is the identity an upgrade matches on. A new AppId
; would install alongside the old build instead of replacing it.
AppId={{8F3A6C24-5B7E-4D91-9E2C-0A1F6B4D8E37}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\GuitarHelper
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GuitarHelper-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; GPLv3: the licence must accompany the binary. Showing it in the wizard and
; installing LICENSE/COPYRIGHT/THIRD-PARTY-NOTICES alongside the app is how
; that obligation is met.
LicenseFile=..\LICENSE
InfoBeforeFile=..\COPYRIGHT
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; The updater launches this with /SILENT; CLOSEAPPLICATIONS lets it replace an
; exe the outgoing build still holds open.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\GuitarHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Also at the top level, not only inside _internal: a licence the recipient
; cannot find has not really accompanied the program.
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\COPYRIGHT"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{group}\Licence and source"; Filename: "{app}\COPYRIGHT"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller's bundle is byte-identical to what was installed, but numba and
; Python leave __pycache__ behind; without this the app directory survives
; uninstall as an empty shell.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: dirifempty; Name: "{app}"

[Code]
var
  ComponentsPage: TWizardPage;
  ConsentCheck: TNewCheckBox;
  FfmpegWarning: TNewStaticText;

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}') + '\{#DataDirName}';
end;

function FfmpegOnPath(): Boolean;
begin
  Result := FileSearch('ffmpeg.exe', GetEnv('PATH')) <> '';
end;

procedure ConsentChanged(Sender: TObject);
begin
  WizardForm.NextButton.Enabled := ConsentCheck.Checked;
end;

procedure CreateComponentsPage();
var
  Memo: TNewMemo;
  Intro: TNewStaticText;
begin
  ComponentsPage := CreateCustomPage(
    wpWelcome,
    'Bundled components',
    'Review what will be installed on your computer.');

  Intro := TNewStaticText.Create(ComponentsPage);
  Intro.Parent := ComponentsPage.Surface;
  Intro.Left := 0;
  Intro.Top := 0;
  Intro.Width := ComponentsPage.SurfaceWidth;
  Intro.WordWrap := True;
  Intro.Caption :=
    'Guitar Helper is fully offline. Everything below is installed locally and ' +
    'nothing is uploaded. No Python installation is required.';
  Intro.Height := ScaleY(28);

  Memo := TNewMemo.Create(ComponentsPage);
  Memo.Parent := ComponentsPage.Surface;
  Memo.Left := 0;
  Memo.Top := Intro.Top + Intro.Height + ScaleY(6);
  Memo.Width := ComponentsPage.SurfaceWidth;
  Memo.Height := ComponentsPage.SurfaceHeight - Intro.Height - ScaleY(72);
  Memo.ReadOnly := True;
  Memo.ScrollBars := ssVertical;
  Memo.Text :=
    'APPLICATION' + #13#10 +
    '  Guitar Helper {#AppVersion} - desktop UI, audio playback, MIDI dispatch' + #13#10 +
    '  Embedded Python runtime and Qt (PySide6) user interface libraries' + #13#10 +
    '  libsndfile and PortAudio (audio decoding and output)' + #13#10 +
    '  python-rtmidi and mido (MIDI Program Change output)' + #13#10 +
    '' + #13#10 +
    'AUDIO ANALYSIS' + #13#10 +
    '  librosa, numba, scikit-learn, SciPy, NumPy' + #13#10 +
    '  Tone archetype calibration data (archetypes.json)' + #13#10 +
    '' + #13#10 +
    'GUITAR STEM SEPARATION (required - the app cannot analyse without it)' + #13#10 +
    '  PyTorch, CPU build - no GPU or driver needed' + #13#10 +
    '  ONNX Runtime and audio-separator' + #13#10 +
    '  htdemucs_6s neural network weights (~55 MB)' + #13#10 +
    '' + #13#10 +
    'DISK SPACE' + #13#10 +
    '  Roughly 850 MB installed, under your user profile.' + #13#10 +
    '  Separated guitar stems are cached in your data folder and grow over' + #13#10 +
    '  time; you can clear them at any point.' + #13#10 +
    '' + #13#10 +
    'WHERE THINGS GO' + #13#10 +
    '  Program:   ' + ExpandConstant('{localappdata}') + '\Programs\GuitarHelper' + #13#10 +
    '  Your data: ' + DataDir() + #13#10 +
    '             (library, stem cache, settings - kept when you uninstall)' + #13#10 +
    '' + #13#10 +
    'SEPARATE PREREQUISITE - FFmpeg' + #13#10 +
    '  Not bundled, and required before any song can be analysed.' + #13#10 +
    '  Install it with:  winget install --id Gyan.FFmpeg -e' + #13#10 +
    '  Guitar Helper will tell you if it is missing.' + #13#10 +
    '' + #13#10 +
    'LICENCE' + #13#10 +
    '  Guitar Helper is free software under the GNU General Public License' + #13#10 +
    '  version 3 or later, and comes with ABSOLUTELY NO WARRANTY.' + #13#10 +
    '  It bundles mutagen (GPL-2.0-or-later) and Qt (LGPL v3); the full' + #13#10 +
    '  component list is installed as THIRD-PARTY-NOTICES.md.' + #13#10 +
    '  You have the right to obtain the complete source code - see the' + #13#10 +
    '  COPYRIGHT file, installed alongside the application.';

  FfmpegWarning := TNewStaticText.Create(ComponentsPage);
  FfmpegWarning.Parent := ComponentsPage.Surface;
  FfmpegWarning.Left := 0;
  FfmpegWarning.Top := Memo.Top + Memo.Height + ScaleY(6);
  FfmpegWarning.Width := ComponentsPage.SurfaceWidth;
  FfmpegWarning.WordWrap := True;
  FfmpegWarning.Height := ScaleY(28);

  ConsentCheck := TNewCheckBox.Create(ComponentsPage);
  ConsentCheck.Parent := ComponentsPage.Surface;
  ConsentCheck.Left := 0;
  ConsentCheck.Top := FfmpegWarning.Top + FfmpegWarning.Height + ScaleY(4);
  ConsentCheck.Width := ComponentsPage.SurfaceWidth;
  ConsentCheck.Height := ScaleY(20);
  ConsentCheck.Caption := 'I have read the above and want to install these components';
  ConsentCheck.OnClick := @ConsentChanged;
end;

procedure InitializeWizard();
begin
  CreateComponentsPage();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (ComponentsPage <> nil) and (CurPageID = ComponentsPage.ID) then
  begin
    // Checked on entry rather than at startup: the user may have installed
    // ffmpeg in another window after launching this installer.
    if FfmpegOnPath() then
      FfmpegWarning.Caption := 'FFmpeg was found on your PATH.'
    else
      FfmpegWarning.Caption :=
        'FFmpeg was NOT found on your PATH. Guitar Helper will install, but no ' +
        'song can be analysed until you install FFmpeg (see above).';

    WizardForm.NextButton.Enabled := ConsentCheck.Checked;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // A silent install (how the updater invokes this) has nobody to consent, and
  // consent was already given when the app was first installed.
  Result := (ComponentsPage <> nil) and (PageID = ComponentsPage.ID) and WizardSilent();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  // The library database, stem cache and calibrated archetypes are the user's
  // work, not ours. Uninstall leaves them alone unless asked, and the prompt
  // defaults to No.
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := DataDir();
    if DirExists(Dir) then
    begin
      if SuppressibleMsgBox(
           'Also delete your library, analysed segments and cached guitar stems?'#13#10#13#10
           + Dir + #13#10#13#10
           + 'Choose No to keep them for a future reinstall.',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(Dir, True, True, True);
    end;
  end;
end;
