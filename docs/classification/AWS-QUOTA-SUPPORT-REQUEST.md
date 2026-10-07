# Draft AWS support request

Subject: Bedrock Haiku 4.5 applied RPM quota 10; increase to 120 rejected against default 10000

Please investigate the applied Amazon Bedrock quota for cross-region model
inference requests per minute for Anthropic Claude Haiku 4.5 in ap-southeast-2,
quota code L-CCA5DF70.

GetServiceQuota returns an applied account quota of 10 requests per minute and
Adjustable=true. GetAWSDefaultServiceQuota returns 10000. RequestServiceQuotaIncrease
with DesiredValue=120 fails with IllegalArgumentException: "You must provide a
quota value greater than the default quota value of 10000.0".

Our existing Australian inference profile is
au.anthropic.claude-haiku-4-5-20251001-v1:0. It serves a read-only Gmail badge
classification workflow via a pinned Bedrock Flow. A typical inbox displays
approximately 22 threads, with multiple team members sharing the service.
CloudWatch InvocationThrottles for that profile recorded 394 throttles in the
queried 2026-10-07T00:00:00Z to 2026-10-08T00:00:00Z window (queried before the
window ended). This metric may include other consumers of the same profile.

We request an applied quota of 120 requests per minute, or correction of the
reported/applied discrepancy and guidance on the correct quota-increase path.
We are not requesting a model change, cross-geography routing, or provisioned
throughput purchase. Backend rate limits and bounded retries are being deployed.

No customer email content, credentials or personal account data is included.
