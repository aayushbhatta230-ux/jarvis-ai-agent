<#
.SYNOPSIS
  Install or update JARVIS: Python environment, dependencies, local AI models, voice models and shortcuts.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -AutoStart     # also start JARVIS at sign-in
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipModels    # skip the ~6 GB model downloads
#>
param([switch]$AutoStart, [switch]$SkipModels, [switch]$NoShortcuts)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "== JARVIS installer ($Root)" -ForegroundColor Cyan

# 1. Python virtual environment
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    py -3.13 -m venv .venv 2>$null; if (-not $?) { python -m venv .venv }
}
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements.txt

# 2. Voice models (wake word, VAD, speaker recognition, Whisper)
$Models = Join-Path $Root "data\models"
New-Item -ItemType Directory -Force $Models | Out-Null
$ProgressPreference = "SilentlyContinue"
$files = @{
  "melspectrogram.onnx" = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx";
  "embedding_model.onnx" = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx";
  "hey_jarvis_v0.1.onnx" = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx";
  "silero_vad.onnx" = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx";
  "wespeaker_en_voxceleb_resnet34.onnx" = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx"
}
foreach ($name in $files.Keys) {
    $dest = Join-Path $Models $name
    if (-not (Test-Path $dest)) { Write-Host "Downloading $name"; Invoke-WebRequest $files[$name] -OutFile $dest }
}
& $Py -c "from jarvis.voice.stt import WhisperSTT; from jarvis.core import paths; WhisperSTT(paths.models_dir(), 'base.en'); print('Whisper base.en ready')"

# 3. Local language / vision / embedding models (Ollama)
if (-not $SkipModels) {
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
    if (Test-Path $ollama) {
        foreach ($m in "qwen3:4b-instruct", "gemma3:4b", "nomic-embed-text", "llama3.2") { & $ollama pull $m }
    } else {
        Write-Warning "Ollama not found. Install it from https://ollama.com and re-run, or configure a cloud provider in Settings."
    }
}

# 4. Shortcuts (Start Menu + Desktop), optional auto-start
if (-not $NoShortcuts) {
    $Pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $shell = New-Object -ComObject WScript.Shell
    $targets = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))
    if ($AutoStart) { $targets += [Environment]::GetFolderPath("Startup") }
    foreach ($dir in $targets) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "JARVIS.lnk"))
        $lnk.TargetPath = $Pyw
        $lnk.Arguments = "-m jarvis"
        $lnk.WorkingDirectory = $Root
        $lnk.Description = "JARVIS desktop AI assistant"
        $lnk.Save()
        Write-Host "Shortcut: $dir\JARVIS.lnk"
    }
}

# 5. Self-test
& $Py -m jarvis --selftest
Write-Host "Done. Start JARVIS from the Start Menu / Desktop shortcut, or: .venv\Scripts\python.exe -m jarvis" -ForegroundColor Green
