[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Bucket = '<github-user>-ai-brain-backup-<aws-account-primary>',
    [string]$Region = 'us-east-1',
    [string]$Profile = '',
    [ValidateSet('GOVERNANCE', 'COMPLIANCE')][string]$LockMode = 'GOVERNANCE',
    [ValidateRange(1, 3650)][int]$LockDays = 30
)

$ErrorActionPreference = 'Stop'
$env:AWS_PAGER = ''
if ($Profile) { $env:AWS_PROFILE = $Profile }
$state = "$env:USERPROFILE\.brain-resilience"
New-Item -ItemType Directory -Force -Path $state | Out-Null
function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

$bucketCount = aws s3api list-buckets --query "length(Buckets[?Name=='$Bucket'])" --output text
if ($LASTEXITCODE -ne 0) { throw 'Could not inventory S3 buckets' }
if ([int]$bucketCount -eq 0) {
    if (-not $PSCmdlet.ShouldProcess("s3://$Bucket", 'Create Object-Lock-enabled backup bucket')) { return }
    if ($Region -eq 'us-east-1') {
        aws s3api create-bucket --bucket $Bucket --region $Region --object-lock-enabled-for-bucket | Out-Null
    } else {
        aws s3api create-bucket --bucket $Bucket --region $Region --object-lock-enabled-for-bucket --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'S3 bucket creation failed' }
}

$public = @{
    BlockPublicAcls = $true
    IgnorePublicAcls = $true
    BlockPublicPolicy = $true
    RestrictPublicBuckets = $true
} | ConvertTo-Json -Compress
$publicPath = Join-Path $state 'public-access-block.json'
Write-Utf8NoBom $publicPath $public
aws s3api put-public-access-block --bucket $Bucket --region $Region --public-access-block-configuration "file://$publicPath"
if ($LASTEXITCODE -ne 0) { throw 'Could not block public access' }

$ownership = @{ Rules = @(@{ ObjectOwnership = 'BucketOwnerEnforced' }) } | ConvertTo-Json -Depth 5 -Compress
$ownershipPath = Join-Path $state 'ownership.json'
Write-Utf8NoBom $ownershipPath $ownership
aws s3api put-bucket-ownership-controls --bucket $Bucket --region $Region --ownership-controls "file://$ownershipPath"
if ($LASTEXITCODE -ne 0) { throw 'Could not enforce bucket ownership' }

$encryption = @{
    Rules = @(@{ ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = 'AES256' }; BucketKeyEnabled = $false })
} | ConvertTo-Json -Depth 6 -Compress
$encryptionPath = Join-Path $state 'encryption.json'
Write-Utf8NoBom $encryptionPath $encryption
aws s3api put-bucket-encryption --bucket $Bucket --region $Region --server-side-encryption-configuration "file://$encryptionPath"
if ($LASTEXITCODE -ne 0) { throw 'Could not enable default encryption' }

aws s3api put-bucket-versioning --bucket $Bucket --region $Region --versioning-configuration 'Status=Enabled'
if ($LASTEXITCODE -ne 0) { throw 'Could not enable versioning' }

$lock = @{
    ObjectLockEnabled = 'Enabled'
    Rule = @{ DefaultRetention = @{ Mode = $LockMode; Days = $LockDays } }
} | ConvertTo-Json -Depth 6 -Compress
$lockPath = Join-Path $state 'object-lock.json'
Write-Utf8NoBom $lockPath $lock
aws s3api put-object-lock-configuration --bucket $Bucket --region $Region --object-lock-configuration "file://$lockPath"
if ($LASTEXITCODE -ne 0) { throw 'Could not configure Object Lock' }

$lifecycle = @{
    Rules = @(
        @{
            ID = 'AbortIncompleteUploads'
            Status = 'Enabled'
            Filter = @{ Prefix = '' }
            AbortIncompleteMultipartUpload = @{ DaysAfterInitiation = 7 }
        },
        @{
            ID = 'ArchiveOldVersions'
            Status = 'Enabled'
            Filter = @{ Prefix = '' }
            NoncurrentVersionTransitions = @(@{ NoncurrentDays = 30; StorageClass = 'GLACIER_IR' })
            NoncurrentVersionExpiration = @{ NoncurrentDays = 180; NewerNoncurrentVersions = 10 }
        }
    )
} | ConvertTo-Json -Depth 8
$lifecyclePath = Join-Path $state 'lifecycle.json'
Write-Utf8NoBom $lifecyclePath $lifecycle
aws s3api put-bucket-lifecycle-configuration --bucket $Bucket --region $Region --lifecycle-configuration "file://$lifecyclePath"
if ($LASTEXITCODE -ne 0) { throw 'Could not configure lifecycle' }

$account = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or -not $account) { throw 'Could not identify AWS account' }
$policy = @{
    Version = '2012-10-17'
    Statement = @(@{
        Sid = 'DenyInsecureTransport'
        Effect = 'Deny'
        Principal = '*'
        Action = 's3:*'
        Resource = @("arn:aws:s3:::$Bucket", "arn:aws:s3:::$Bucket/*")
        Condition = @{ Bool = @{ 'aws:SecureTransport' = 'false' } }
    })
} | ConvertTo-Json -Depth 8
$policyPath = Join-Path $state 'bucket-policy.json'
Write-Utf8NoBom $policyPath $policy
aws s3api put-bucket-policy --bucket $Bucket --region $Region --policy "file://$policyPath"
if ($LASTEXITCODE -ne 0) { throw 'Could not require TLS' }

aws s3api put-bucket-tagging --bucket $Bucket --region $Region --tagging 'TagSet=[{Key=Purpose,Value=AI-Brain-Disaster-Recovery},{Key=DataClass,Value=Private-Vault},{Key=ManagedBy,Value=BrainResilience}]'
if ($LASTEXITCODE -ne 0) { throw 'Could not tag bucket' }

Write-Output "configured`t$Bucket`t$Region`taccount=$account`tversioned+encrypted+private+object-lock-$LockMode-$LockDays`d"
