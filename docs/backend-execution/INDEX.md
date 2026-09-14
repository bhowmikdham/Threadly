# Backend execution task index

Generated from [tasks.json](tasks.json). All entries initially describe planned work.
Read START-HERE.md, the selected card and its dependencies; one bounded PR at a time.

| Task | Work | Requires | Original packages |
|---|---|---|---|
| [B00](tasks/B00.md) | Verify baseline and settle shared contracts | Verified checkout | T01, T02, T04, T05, T08, T10, T11, T13 |
| [B01](tasks/B01.md) | Persist actual Google capabilities and harden auth boundaries | B00 | T03 |
| [B02](tasks/B02.md) | Add durable action, approval and attempt storage | B00 | T05, T10, T12 |
| [B03](tasks/B03.md) | Build immutable MIME payloads and send previews | B01, B02 | T02, T10, T12 |
| [B04](tasks/B04.md) | Implement explicit approval, rejection and cancellation | B03 | T05, T12 |
| [B05](tasks/B05.md) | Implement the Gmail executor with writes disabled | B01, B04 | T12 |
| [B06](tasks/B06.md) | Reconcile uncertain Gmail sends and complete email gate | B05 | T12 |
| [B07](tasks/B07.md) | Bind UI selections and ordinal references | B00 | T06, T17 |
| [B08](tasks/B08.md) | Resume clarification with durable typed inputs | B07 | T05, T06, T17 |
| [B09](tasks/B09.md) | Implement bounded Other and evidence retrieval | B07 | T09 |
| [B10](tasks/B10.md) | Execute compound steps with explicit artifact streams | B08, B09 | T05, T06, T08, T09, T11, T13 |
| [B11](tasks/B11.md) | Add non-calendar plans and explicit commitment acceptance | B10 | T14 |
| [B12](tasks/B12.md) | Add Calendar preferences, capabilities and freebusy reads | B01 | T03, T15 |
| [B13](tasks/B13.md) | Implement the deterministic timezone and slot engine | B12 | T15 |
| [B14](tasks/B14.md) | Add scheduling offers and meeting negotiation state | B10, B13 | T06, T11, T16 |
| [B15](tasks/B15.md) | Create approved Calendar events and reconcile outcomes | B06, B14 | T12, T16 |
| [B16](tasks/B16.md) | Create the versioned native/Flow operation registry | B00 | T03, T04, T07 |
| [B17](tasks/B17.md) | Invoke Bedrock Flows through an authorized read bridge | B10, B16 | T03, T07, T08, T11, T13, T14, T16 |
| [B18](tasks/B18.md) | Run all-intent UI, model-quality and recovery gates | B06, B07, B08, B09, B10, B11, B14, B15, B17 | T08, T09, T11, T13, T14, T16, T17, T18 |
| [B19](tasks/B19.md) | Add operational controls, retention and background reliability | B00, B06, B15, B17 | T02, T03, T05, T19 |
| [B20](tasks/B20.md) | Run staged pilot and close original initial-release packages | B18, B19 | T01, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12, T13, T14, T15, T16, T17, T18, T19 |
| [B21](tasks/B21.md) | Add opt-in proactive suggestions and follow-up triggers | B20 | T20 |
| [B22](tasks/B22.md) | Add consented sent-mail style personalization | B20 | T21 |
| [B23](tasks/B23.md) | Add voice through the same task and approval contracts | B20 | T22 |
| [B24](tasks/B24.md) | Add attachment-aware understanding and approved sending | B20 | T23 |
| [B25](tasks/B25.md) | Add event changes, recurrence and richer meeting resources | B20 | T24 |

## Completion boundary

B00–B20 cover baseline through the initial release; B21–B25 are follow-on capabilities.
Dependencies govern implementation ordering, not automatic live activation.
B05 has an explicit hard enabling gate on B06 plus test-account validation.
B19 operational work may be prepared early, but its final gate must use the completed action/Flow runtime.
