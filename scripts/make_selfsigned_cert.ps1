<#
.SYNOPSIS
  코드 서명용 self-signed 인증서를 만들고 지문(thumbprint)과 배포용 .cer 를 출력한다.

.DESCRIPTION
  CurrentUser\My 저장소에 코드 서명 인증서를 생성한다. 빌드 시 이 지문을
  YKE_SIGN_THUMBPRINT 로 지정하면 scripts/sign.py(및 build.py)가 자동으로 서명한다.

  self-signed 인증서의 한계: SmartScreen 경고는 사라지지 않는다. 이 인증서를
  "신뢰할 수 있는 루트 인증 기관" + "신뢰할 수 있는 게시자"에 설치한 PC 에서만
  게시자 이름이 신뢰되어 보인다. 본인 PC·인증서를 나눈 소수용이다. 넓은 배포에는
  정식 CA 인증서나 SignPath Foundation(오픈소스 무료) 같은 옵션이 필요하다.

.PARAMETER Subject
  인증서 주체(게시자 이름으로 표시됨). 기본값 아래.

.PARAMETER Years
  유효 기간(년). 기본 5.

.PARAMETER OutDir
  .cer(및 -PfxPassword 지정 시 .pfx) 저장 폴더. 기본: 현재 폴더.

.PARAMETER PfxPassword
  지정하면 .pfx(개인키 포함)도 export 한다(CI 등 저장소를 못 쓰는 환경용). 미지정 시
  저장소 인증서 + 지문만으로 서명한다(권장, 비밀번호 노출 없음).

.EXAMPLE
  pwsh -File scripts/make_selfsigned_cert.ps1
  # 인증서 생성 후 출력된 지문을 환경 변수로 지정하고 빌드:
  $env:YKE_SIGN_THUMBPRINT = "<출력된 지문>"
  python scripts/build.py
#>
[CmdletBinding()]
param(
    [string]$Subject = "CN=YouTube Knowledge Extractor (self-signed)",
    [int]$Years = 5,
    [string]$OutDir = ".",
    [string]$PfxPassword = ""
)

$ErrorActionPreference = "Stop"

$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -KeyExportPolicy Exportable `
    -KeyUsage DigitalSignature `
    -KeyAlgorithm RSA -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears($Years)

$outFull = (Resolve-Path -LiteralPath $OutDir).Path
$cerPath = Join-Path $outFull "yke-codesign.cer"
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null

$pfxPath = $null
if ($PfxPassword -ne "") {
    $pfxPath = Join-Path $outFull "yke-codesign.pfx"
    $sec = ConvertTo-SecureString $PfxPassword -AsPlainText -Force
    Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $sec | Out-Null
}

Write-Output ""
Write-Output "=== self-signed 코드 서명 인증서 생성 완료 ==="
Write-Output ("주체(게시자)  : {0}" -f $Subject)
Write-Output ("지문(Thumbprint): {0}" -f $cert.Thumbprint)
Write-Output ("만료           : {0:yyyy-MM-dd}" -f $cert.NotAfter)
Write-Output ("배포용 공개 인증서: {0}" -f $cerPath)
if ($pfxPath) { Write-Output ("서명용 PFX     : {0}" -f $pfxPath) }
Write-Output ""
Write-Output "--- 빌드 시 서명하려면(이 저장소에서) ---"
Write-Output ("  `$env:YKE_SIGN_THUMBPRINT = '{0}'" -f $cert.Thumbprint)
Write-Output "  python scripts/build.py            # (또는 --gpu)"
Write-Output ""
Write-Output "--- 다른 PC 에서 이 서명을 '신뢰'시키려면(그 PC에서 관리자 권한) ---"
Write-Output "  yke-codesign.cer 를 다음 두 저장소에 설치:"
Write-Output "  Import-Certificate -FilePath yke-codesign.cer -CertStoreLocation Cert:\LocalMachine\Root"
Write-Output "  Import-Certificate -FilePath yke-codesign.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher"
Write-Output ""
Write-Output "주의: 위 신뢰 설치를 하지 않은 PC 에서는 여전히 '알 수 없는 게시자'로 보이고"
Write-Output "      SmartScreen 경고도 그대로입니다(self-signed 의 본질적 한계)."
