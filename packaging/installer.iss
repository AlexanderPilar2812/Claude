; Instalador de EPACAL (Inno Setup 6)
;   iscc packaging\installer.iss          -> Output\EPACAL-Setup-<versión>.exe
; No necesita permisos de administrador: se instala para el usuario actual.

#define AppName "EPACAL"
#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif

[Setup]
AppId={{6B1F6C2E-3C8A-4E7B-9A51-EPACAL000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=EPACAL
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\Output
OutputBaseFilename=EPACAL-Setup-{#AppVersion}
SetupIconFile=epacal.ico
UninstallDisplayIcon={app}\EPACAL.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\EPACAL\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; ejemplos y plugins en Documentos\EPACAL (carpeta del usuario, con permisos de escritura)
Source: "..\casos\ZP02EC\config.yaml"; DestDir: "{userdocs}\EPACAL\ejemplos\ZP02EC"; Flags: onlyifdoesntexist
Source: "..\casos\ZP02EC\Mod_Teo_EC.inp"; DestDir: "{userdocs}\EPACAL\ejemplos\ZP02EC"; Flags: onlyifdoesntexist
Source: "..\casos\ZP02EC\*.dat"; DestDir: "{userdocs}\EPACAL\ejemplos\ZP02EC"; Flags: onlyifdoesntexist
Source: "..\examples\caso_tanque\config.yaml"; DestDir: "{userdocs}\EPACAL\ejemplos\caso_tanque"; Flags: onlyifdoesntexist
Source: "..\examples\caso_tanque\*.inp"; DestDir: "{userdocs}\EPACAL\ejemplos\caso_tanque"; Flags: onlyifdoesntexist
Source: "..\examples\caso_tanque\*.dat"; DestDir: "{userdocs}\EPACAL\ejemplos\caso_tanque"; Flags: onlyifdoesntexist
Source: "..\examples\caso_tanque\verdad.json"; DestDir: "{userdocs}\EPACAL\ejemplos\caso_tanque"; Flags: onlyifdoesntexist
Source: "..\plugins\ejemplo_plugin.py"; DestDir: "{userdocs}\EPACAL\plugins"; DestName: "ejemplo_plugin.py.txt"; Flags: onlyifdoesntexist
Source: "..\INSTALACION.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EPACAL"; Filename: "{app}\EPACAL.exe"
Name: "{group}\Ejemplos y plugins"; Filename: "{userdocs}\EPACAL"
Name: "{group}\Desinstalar EPACAL"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EPACAL"; Filename: "{app}\EPACAL.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EPACAL.exe"; Description: "{cm:LaunchProgram,EPACAL}"; Flags: nowait postinstall skipifsilent
