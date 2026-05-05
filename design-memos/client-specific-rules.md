Client-specific Outside Counsel Guidelines — design memo
Author: Mark McCaffrey
Status: Design sketch — not implemented in this POC
Context: Extension memo for the Invoice Review Assistant POC. The reviewer's client_id parameter exists as the architectural hook for this design.

Problem statement
The current reviewer applies five universal billing rules. In practice, customers have their own Outside Counsel Guidelines (OCGs) that go beyond — and sometimes override — universal rules, and these guidelines change over time. A single eval set and a single prompt cannot serve all customers. The reviewer needs a way to apply client-specific rules at review time, alongside the universal baseline.
Approach
At review time, the system fetches the relevant OCG passages for a given client_id from a vector store, injects them as additional rules into the system prompt alongside the existing five universal rules, and runs the reviewer logic unchanged. The output schema extends with a source field per flag — universal or client — so reviewers can see which rule fired and where it came from.
Key design choices
Retrieval granularity — line-item level.
Retrieving relevant rules per line is more precise and matters more for trust than the token cost saves. Invoice-level retrieval is cheaper but risks surfacing irrelevant rules and diluting the prompt. Revisit if cost-per-line becomes binding at scale.
Chunking — by rule, not by section or fixed window.
Each OCG rule is the unit of evaluation, so it should be the unit of retrieval. Chunking by section risks combining unrelated rules; chunking by token windows breaks rules across boundaries. By-rule chunking keeps the retrieval unit aligned to the evaluation unit.
Combining universal + client rules — additive, with explicit overrides, conflicts go to the stricter rule.
Client rules layer on top of the universal five by default. Where an OCG explicitly contradicts a universal rule (e.g. "intra-firm comms are billable for our matters"), the client rule wins. Where the conflict is implicit, the stricter rule wins — false negatives are more costly in this domain than false positives, and silent under-flagging erodes customer trust faster than over-flagging.
Source attribution — required, not optional.
Every flag carries source_type (universal | client) and source_ref (e.g. R3 or OCG_section_4.2). Without this, reviewers cannot defend a flag to a firm or update a client's rules with confidence. This is a UX requirement, not a nice-to-have.
Eval set design — per-client evals are the unit of measurement.
The universal eval set stays as a baseline for the general reviewer. Each client gets their own eval set built during onboarding, covering a mix of universal-only, client-specific-only, and combined violations. Reporting is per-client, not global.
What I'd build first
v1 minimum scope: one client. Pick a single customer with a manageable OCG (5–10 client-specific rules in addition to the universal five). Hand-curate a 15–20 line eval set for them — mix of universal-only, client-only, and combined violations.
Goal: measure whether retrieval-augmented review beats prompt-only review on that client's eval set. If yes, the architecture works and we onboard the second client. If no, redesign before scaling.
This is the smallest end-to-end slice that proves the architecture rather than the components. The point is not to ship the feature — it is to decide whether to keep building it.
What I'd watch for
Retrieval failure modes. Wrong OCG passages retrieved → wrong rules applied → silent reviewer error. Retrieval quality often dominates generation quality in RAG systems. The eval set needs lines specifically designed to test retrieval (e.g. lines that look like they fall under one OCG section but actually fall under another).
Cost and latency. RAG adds embedding lookups plus larger prompts on every call. At low volume this is invisible. At customer scale it becomes the operational cost line that matters. Worth instrumenting from day one.
Onboarding cost per client. Each new customer requires OCGs ingested, chunked, indexed, and ideally an eval set built. Whether this is a one-day job or a two-week job determines whether the feature is sellable. The eval-set work is the bottleneck — the system can scale faster than humans can label.
Trust and explainability. "The AI flagged this because of section 4.2 of your OCG" is a much stronger UX than "the AI flagged this." Source attribution (above) supports it; surfacing the retrieved passage in the UI is the next step.
Drift between universal updates and client rules. Universal rules will improve over time. A change to the universal block_billing rule could conflict with how a client's OCG already extends it. Per-client regression testing is the safety net — every universal change needs to be re-scored against every client's eval set before shipping.

Memo written as a design exercise alongside the POC build. RAG implementation deferred; the architectural hooks (client_id parameter, schema with source field) are in place if and when this is built.