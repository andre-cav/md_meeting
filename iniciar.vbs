Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
If Not fs.FileExists(python) Then
  MsgBox "Execute instalar.ps1 primeiro. Consulte o README.md.", 48, "meeting.md"
Else
  shell.CurrentDirectory = folder
  shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & folder & "\app.py" & Chr(34), 1, False
End If
