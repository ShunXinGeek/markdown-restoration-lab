param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$OutputPath,
    [ValidateSet('auto','geometry','marker')][string]$Engine = 'auto',
    [string]$ReviewDir,
    [string]$OcrLanguage = 'eng+chi_sim',
    [switch]$NoOcr,
    [switch]$LegalNumbering,
    [switch]$PagedText,
    [switch]$Chat,
    [switch]$Force
)

$pythonPath = 'D:\CodexTools\markitdown\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "MarkItDown Python environment is missing: $pythonPath"
}

$env:HF_HOME = 'D:\CodexTools\pdf-layout\cache\huggingface'
$env:FONT_PATH = 'D:\CodexTools\pdf-layout\cache\fonts\GoNotoCurrent-Regular.ttf'
$env:XDG_CACHE_HOME = 'D:\CodexTools\pdf-layout\cache'
$env:TEMP = 'D:\CodexTools\pdf-layout\tmp'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_OFFLINE = '1'
$arguments = @((Join-Path $PSScriptRoot 'convert.py'), $InputPath)
if ($OutputPath) { $arguments += $OutputPath }
$arguments += @('--engine', $Engine, '--ocr-language', $OcrLanguage)
if ($ReviewDir) { $arguments += @('--review-dir', $ReviewDir) }
if ($NoOcr) { $arguments += '--no-ocr' }
if ($LegalNumbering) { $arguments += '--legal-numbering' }
if ($PagedText) { $arguments += '--paged-text' }
if ($Chat) { $arguments += '--chat' }
if ($Force) { $arguments += '--force' }
& $pythonPath -X utf8 @arguments
if ($LASTEXITCODE -eq 2) {
    Write-Warning 'Output is partial, empty, or failed audit. Read the JSON report before delivery.'
} elseif ($LASTEXITCODE -ne 0) {
    throw "MarkItDown conversion failed with exit code $LASTEXITCODE"
}
