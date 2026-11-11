# 配置各平台账号密码 -> 写入 credentials.json（图形窗口，新电脑零依赖）
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfgPath = Join-Path $root "credentials.json"
$examplePath = Join-Path $root "credentials.example.json"
$src = if (Test-Path $cfgPath) { $cfgPath } else { $examplePath }
$data = $null
if (Test-Path $src) { $data = Get-Content $src -Raw -Encoding UTF8 | ConvertFrom-Json }

$platforms = @("抖音", "拼多多", "淘宝", "小红书")

$form = New-Object System.Windows.Forms.Form
$form.Text = "配置各平台账号密码"
$form.Size = New-Object System.Drawing.Size(470, 340)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "填写各平台登录账号与密码，点保存。仅存本机，用于脚本自动登录。"
$hint.Location = New-Object System.Drawing.Point(20, 12)
$hint.Size = New-Object System.Drawing.Size(430, 20)
$form.Controls.Add($hint)

$h1 = New-Object System.Windows.Forms.Label
$h1.Text = "账号"; $h1.Location = New-Object System.Drawing.Point(80, 38); $h1.Size = New-Object System.Drawing.Size(160, 18)
$form.Controls.Add($h1)
$h2 = New-Object System.Windows.Forms.Label
$h2.Text = "密码"; $h2.Location = New-Object System.Drawing.Point(255, 38); $h2.Size = New-Object System.Drawing.Size(150, 18)
$form.Controls.Add($h2)

$y = 60
$accBoxes = @{}; $pwdBoxes = @{}
foreach ($p in $platforms) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $p; $lbl.Location = New-Object System.Drawing.Point(20, ($y + 2)); $lbl.Size = New-Object System.Drawing.Size(55, 22)
    $form.Controls.Add($lbl)

    $acc = New-Object System.Windows.Forms.TextBox
    $acc.Location = New-Object System.Drawing.Point(80, $y); $acc.Size = New-Object System.Drawing.Size(165, 22)
    if ($data -and $data.$p) { $acc.Text = [string]$data.$p.account }
    $form.Controls.Add($acc); $accBoxes[$p] = $acc

    $pwd = New-Object System.Windows.Forms.TextBox
    $pwd.Location = New-Object System.Drawing.Point(255, $y); $pwd.Size = New-Object System.Drawing.Size(150, 22)
    $pwd.UseSystemPasswordChar = $true
    if ($data -and $data.$p) { $pwd.Text = [string]$data.$p.password }
    $form.Controls.Add($pwd); $pwdBoxes[$p] = $pwd

    $y += 34
}

$btnSave = New-Object System.Windows.Forms.Button
$btnSave.Text = "保存"; $btnSave.Location = New-Object System.Drawing.Point(255, ($y + 8)); $btnSave.Size = New-Object System.Drawing.Size(70, 28)
$btnSave.Add_Click({
        $out = [ordered]@{}
        $out["_说明"] = "各平台账号密码，脚本自动登录用。仅存本机、不分发。"
        foreach ($p in $platforms) {
            $out[$p] = [ordered]@{ account = $accBoxes[$p].Text.Trim(); password = $pwdBoxes[$p].Text }
        }
        $json = ($out | ConvertTo-Json -Depth 5)
        [IO.File]::WriteAllText($cfgPath, $json, [Text.UTF8Encoding]::new($false))
        [System.Windows.Forms.MessageBox]::Show("已保存到 credentials.json", "完成") | Out-Null
        $form.Close()
    })
$form.Controls.Add($btnSave)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "取消"; $btnCancel.Location = New-Object System.Drawing.Point(335, ($y + 8)); $btnCancel.Size = New-Object System.Drawing.Size(70, 28)
$btnCancel.Add_Click({ $form.Close() })
$form.Controls.Add($btnCancel)

[void]$form.ShowDialog()
