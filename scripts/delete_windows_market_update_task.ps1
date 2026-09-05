param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$TaskName = "Taiwan50 Market Foundation Daily Update"

if ($WhatIf) {
    Write-Host "WhatIf: would unregister scheduled task '$TaskName'"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered scheduled task '$TaskName'"
