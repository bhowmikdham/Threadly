# Sources and scope of claims

Checked 14 September 2026. These sources support specific platform boundaries;
the module layout, B-task decomposition and recommended defaults are Threadly design
choices. Recheck current APIs, account permissions and resource availability when
implementing. No source establishes equal quality between Spark and Astra.

| Source | Used for |
|---|---|
| [OpenAI: AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | Keep persistent project instructions short and link to focused task files. Codex instruction discovery has a size limit; do not assume this whole pack auto-loads. |
| [Google: OAuth web server flow](https://developers.google.com/identity/protocols/oauth2/web-server) | State/security checks, granted permissions and incremental authorization; validate the actual extension/backend flow before changing it. |
| [Google: sending email](https://developers.google.com/workspace/gmail/api/guides/sending) | MIME and base64url send transport. Does not establish a Gmail idempotency guarantee. |
| [Google: threads](https://developers.google.com/workspace/gmail/api/guides/threads) | Reply thread ID, compliant reply headers and subject matching. |
| [Google: message filtering](https://developers.google.com/workspace/gmail/api/guides/filtering) | Scoped candidate search for reconciliation; matching policy is a backend design and must be tested. |
| [Google: Calendar freebusy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query) | Per-calendar errors and half-open busy intervals. |
| [Google: Calendar events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert) | Event insertion fields, provider-compatible IDs and documented collision caveat. |
| [Google: Gmail push](https://developers.google.com/workspace/gmail/api/guides/push) | Watches, renewal and change notifications for later proactive work. |
| [AWS: InvokeFlow](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_InvokeFlow.html) | Typed response events, completion/output/error and input-request handling. |
| [AWS: multi-turn Flows](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-multi-turn-invocation.html) | Currently documented preview functionality; durable human waits remain application-owned. |

Code observations come from the merged baseline and are mapped in BASELINE.md.
No live AWS/Google operation, model comparison or new backend test run was needed
or performed to produce this documentation handoff. PR #9's 233-test and CI evidence
is historical implementation evidence and explicitly labelled as such.
