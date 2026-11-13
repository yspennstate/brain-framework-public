' doc_expand_launcher.vbs - hidden launcher for the hourly document expansion: doc_expand.py --new gives every memory
' that has none its three plain-words questions, written to the store bm25.py reads
' (~/.claude/brain_graph/doc_expansion/expansions.jsonl); the next index refresh picks them up.
' Schedule it hourly: wscript.exe //B "%USERPROFILE%\.claude\brain_graph\bin\doc_expand_launcher.vbs"
' 0 = SW_HIDE (no window, not even a flash); True = wait so the task's no-overlap policy works.
' Python is the first python on PATH unless BRAIN_PYTHON names one.
Dim sh : Set sh = CreateObject("WScript.Shell")
Dim fs : Set fs = CreateObject("Scripting.FileSystemObject")
Dim home : home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
Dim bin : bin = home & "\.claude\brain_graph\bin"
Dim store : store = home & "\.claude\brain_graph\doc_expansion"
Dim py : py = sh.ExpandEnvironmentStrings("%BRAIN_PYTHON%")
If py = "%BRAIN_PYTHON%" Or py = "" Then py = "python"

Sub MakeDirs(p)
    If Not fs.FolderExists(p) Then
        MakeDirs fs.GetParentFolderName(p)
        fs.CreateFolder p
    End If
End Sub

MakeDirs store
sh.CurrentDirectory = bin
WScript.Quit sh.Run("cmd /c """"" & py & """ -u doc_expand.py --new --out """ & store & "\expansions.jsonl"" --max 400 --workers 2 >> """ & store & "\expand.log"" 2>&1""", 0, True)
