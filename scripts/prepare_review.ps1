param(
    [Parameter(Mandatory = $true)][string]$ReviewDir,
    [Parameter(Mandatory = $true)][string]$MarkdownPath,
    [string]$Pages,
    [ValidateRange(1,1000)][int]$MaxPages = 12,
    [switch]$NoSourceImages,
    [switch]$Screenshot,
    [switch]$BrowserNoSandbox
)
$ErrorActionPreference = 'Stop'
$pythonPath = 'D:\CodexTools\markitdown\.venv\Scripts\python.exe'
$reviewPath = (Resolve-Path -LiteralPath $ReviewDir).Path
$argsList = @('-X','utf8',(Join-Path $PSScriptRoot 'prepare_review.py'),$reviewPath,$MarkdownPath,'--max-pages',"$MaxPages")
if ($Pages) { $argsList += @('--pages',$Pages) }
if ($NoSourceImages) { $argsList += '--no-source-images' }
& $pythonPath @argsList
if ($LASTEXITCODE -ne 0) { throw 'Source review preparation failed.' }
if (-not (Get-Command ConvertFrom-Markdown -ErrorAction SilentlyContinue)) {
    throw 'Use PowerShell 7 for the bundled Markdown renderer. Source evidence remains available.'
}
$prefix = @'
<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src file: data:; style-src 'unsafe-inline'">
<style>body{font:18px/1.5 Georgia;margin:35px;max-width:1000px}h1,h2,h3,h4{font-family:Arial}blockquote{border-left:3px solid #aaa;padding-left:18px;margin-left:0}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:6px}pre{white-space:pre-wrap}</style>
'@
foreach ($pair in @(@('preview-safe.md','markdown-preview.html'),@('preview-sample.md','markdown-sample.html'))) {
    $converted = ConvertFrom-Markdown -LiteralPath (Join-Path $reviewPath $pair[0])
    [IO.File]::WriteAllText((Join-Path $reviewPath $pair[1]),$prefix+$converted.Html,[Text.UTF8Encoding]::new($false))
}
if ($Screenshot) {
    $browserPath = @('C:\Program Files\Google\Chrome\Application\chrome.exe','C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $browserPath) { throw 'No local Chrome or Edge executable found; inspect markdown-preview.html.' }
    $profile = Join-Path $reviewPath ('browser-profile-'+[Guid]::NewGuid().ToString('N'))
    $shot = Join-Path $reviewPath 'markdown-preview.png'
    if (Test-Path -LiteralPath $shot) { Remove-Item -LiteralPath $shot }
    $uri = [Uri]::new((Join-Path $reviewPath 'markdown-sample.html')).AbsoluteUri
    $startInfo = [Diagnostics.ProcessStartInfo]::new($browserPath)
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardError = $true
    $browserArgs = @('--headless','--disable-gpu','--disable-software-rasterizer','--disable-javascript','--disable-background-networking','--no-first-run','--no-default-browser-check',"--user-data-dir=$profile","--screenshot=$shot",'--window-size=1200,2400',$uri)
    if ($BrowserNoSandbox) { $browserArgs += '--no-sandbox' }
    foreach ($arg in $browserArgs) { $startInfo.ArgumentList.Add($arg) }
    $process = [Diagnostics.Process]::Start($startInfo)
    $errorTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(15000)) {
        $process.Kill($true)
        throw 'Isolated preview browser exceeded 15 seconds; HTML and source evidence remain available.'
    }
    $browserLog = $errorTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText((Join-Path $reviewPath 'browser-preview.log'),$browserLog)
    if (-not (Test-Path -LiteralPath $shot)) { throw 'Browser screenshot failed; inspect markdown-preview.html. Source conversion and review.json were not changed.' }
}
Write-Output "Review package ready: $reviewPath"
