param([Parameter(Mandatory=$true)][string]$Manifest, [string]$Language='eng+chi_sim')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
function Await($operation, [Type]$resultType) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task=$method.MakeGenericMethod($resultType).Invoke($null,@($operation))
    $task.GetAwaiter().GetResult()
}
# Run requested recognizers locally; the caller selects results by source coordinates.
$aliases=@{'eng'='en-US';'chi_sim'='zh-Hans-CN';'deu'='de-DE';'spa'='es-ES';'jpn'='ja-JP'}
$requested=@($Language.Split('+') | ForEach-Object { if($aliases.ContainsKey($_)){$aliases[$_]}else{$_} })
$available=@([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object {$_.LanguageTag})
foreach($tag in $requested){if($tag -notin $available){throw "OCR language is not installed in Windows: $tag. Available: $($available -join ', ')"}}
if($requested.Count -gt 1 -and (($requested | Sort-Object -Unique) -join ',') -ne 'en-US,zh-Hans-CN'){
    throw 'Choose one installed OCR language, or use eng+chi_sim for mixed English/Chinese pages.'
}
$engines=@{}
foreach($tag in $requested){
    $engines[$tag]=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language($tag)))
    if($null -eq $engines[$tag]){throw "Could not create OCR recognizer: $tag"}
}
$results=@(foreach($page in (Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json)){
    $stream=$null; $bitmap=$null
    try {
        $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($page.image)) ([Windows.Storage.StorageFile])
        $stream=Await ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $candidates=@(foreach($tag in $requested){
        $ocr=Await ($engines[$tag].RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines=@(foreach($line in $ocr.Lines){
            $words=@($line.Words)
            $left=($words | ForEach-Object {$_.BoundingRect.X} | Measure-Object -Minimum).Minimum
            $top=($words | ForEach-Object {$_.BoundingRect.Y} | Measure-Object -Minimum).Minimum
            $right=($words | ForEach-Object {$_.BoundingRect.X+$_.BoundingRect.Width} | Measure-Object -Maximum).Maximum
            $bottom=($words | ForEach-Object {$_.BoundingRect.Y+$_.BoundingRect.Height} | Measure-Object -Maximum).Maximum
            @{text=$line.Text; bbox=@($left,$top,$right,$bottom)}
        })
        @{language=$tag; lines=$lines}
        })
        @{number=$page.number; language=($requested -join '+'); candidates=$candidates}
    } catch { @{number=$page.number; error=$_.Exception.Message} }
    finally {if($bitmap){$bitmap.Dispose()};if($stream){$stream.Dispose()}}
})
ConvertTo-Json -InputObject $results -Depth 8 -Compress
