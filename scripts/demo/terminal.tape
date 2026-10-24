# Terminal demo for the README, recorded with VHS:
# https://github.com/charmbracelet/vhs
#
# Record it with:
#
#     uv run python scripts/demo/make_terminal.py
#
# The runner creates an empty demo project, puts this repository's virtualenv
# on PATH so `mementic` resolves, and runs vhs from inside that project.
#
# PowerShell is used because VHS resolves `bash` to the Windows Subsystem for
# Linux on Windows. Change `Set Shell` to bash when recording on macOS or
# Linux.

Output terminal.gif
Require mementic

Set Shell "powershell"
Set FontFamily "Consolas"
Set FontSize 19
Set Width 1250
Set Height 900
Set Theme "GitHub Dark"
Set Padding 30
Set BorderRadius 12
Set WindowBar Rings
Set WindowBarSize 36
Set TypingSpeed 25ms
# Capture at 25 fps: VHS's default 50 fps outruns the frame grabber, and
# dropped frames shorten the recording so it plays faster than the tape.
Set Framerate 25

# Start from an empty screen: the shell's first prompt is printed while hidden.
# The prompt prints its own text because the prompt VHS installs writes its
# marker outside the string PSReadLine measures, and history prediction is
# switched off so no suggested command flashes before the typed one.
Hide
Type "function prompt { '> ' }"
Enter
Sleep 300ms
Type "Set-PSReadLineOption -PredictionSource None"
Enter
Sleep 300ms
Type "Clear-Host"
Enter
Sleep 800ms
Show

Sleep 600ms

Type "mementic init"
Enter
Sleep 1500ms

Type `mementic add decision "Use JWT Auth" --rationale "Tokens carry the session" --entity "src/auth.py"`
Enter
Sleep 1500ms

Type `mementic add constraint "Handlers must stay stateless" --entity "src/auth.py"`
Enter
Sleep 1500ms

Type "mementic query keywords jwt handlers"
Enter
Sleep 4s

Type `mementic deprecate use-jwt-auth --reason "Replaced by short lived tokens"`
Enter

Sleep 9s
