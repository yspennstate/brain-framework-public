Option Explicit

Dim sh, fso, appDir, serverPath, localAppData, systemDrive, pythonw, candidates, candidate, cmd

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
serverPath = fso.BuildPath(appDir, "server.py")
localAppData = sh.ExpandEnvironmentStrings("%LocalAppData%")
systemDrive = sh.ExpandEnvironmentStrings("%SystemDrive%")
pythonw = ""

candidates = Array( _
    fso.BuildPath(systemDrive, "Python314\pythonw.exe"), _
    fso.BuildPath(systemDrive, "Python313\pythonw.exe"), _
    fso.BuildPath(systemDrive, "Python312\pythonw.exe"), _
    fso.BuildPath(localAppData, "Programs\Python\Python314\pythonw.exe"), _
    fso.BuildPath(localAppData, "Programs\Python\Python313\pythonw.exe"), _
    fso.BuildPath(localAppData, "Programs\Python\Python312\pythonw.exe"), _
    fso.BuildPath(localAppData, "Programs\Python\Python311\pythonw.exe") _
)

For Each candidate In candidates
    If fso.FileExists(candidate) Then
        pythonw = candidate
        Exit For
    End If
Next

If pythonw = "" Then
    pythonw = "pythonw.exe"
End If

sh.CurrentDirectory = appDir
cmd = """" & pythonw & """ """ & serverPath & """ --open"
sh.Run cmd, 0, False
