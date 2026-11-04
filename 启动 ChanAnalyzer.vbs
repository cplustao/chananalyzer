Option Explicit

Dim fileSystem, shell, projectRoot, launcherPath, command
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcherPath = fileSystem.BuildPath(projectRoot, "scripts\start-local-hidden.ps1")

If Not fileSystem.FileExists(launcherPath) Then
    shell.Popup "Launcher script not found: " & launcherPath, 0, "ChanAnalyzer startup failed", 16
    WScript.Quit 1
End If

command = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & launcherPath & Chr(34)
shell.Run command, 0, False
