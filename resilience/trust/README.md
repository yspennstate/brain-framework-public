# Pinned backup receipt trust roots

These public keys verify completion receipts without trusting S3 metadata or
requiring either AWS account. A valid production receipt must carry signatures
from both keys.

- primary-receipt-signing.pub
  (DER SHA-256 <sha256-of-pinned-pubkey-1>):
  arn:aws:kms:us-east-1:<aws-account-primary>:key/<kms-key-id-1>
- secondary-receipt-signing.pub
  (DER SHA-256 <sha256-of-pinned-pubkey-2>):
  arn:aws:kms:us-west-2:<aws-account-secondary>:key/<kms-key-id-2>

Changing either key is a trust-root rotation and requires an explicit,
independently reviewed migration receipt signed by the old and new keys.
