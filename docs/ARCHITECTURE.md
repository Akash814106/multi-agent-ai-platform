# AgentFlow AI architecture

## Request and answer paths

React sends a JWT to FastAPI and keeps displayed chat history in account-scoped browser storage. FastAPI validates the message and derives identity from verified claims. Every HTTP response gets a server-generated request ID. Chat uses either a JSON response or a Server-Sent Events (SSE) stream.

**General chat.** LangGraph's query router chooses a direct response or the full workflow. The full path enhances the query, looks up conversation memory, plans tasks, and runs research and review in parallel. Research uses Exa, the critic scores findings, and the revision loop is bounded. The final Summary Agent uses successful findings and known gaps to answer the original question. The Memory Save Agent decides what to retain after generation. Its decision stays internal.

**Document chat.** Uploads are validated and parsed into overlapping chunks. PDF chunks retain page numbers. The same local MiniLM embedding function indexes passages and embeds questions. MongoDB confirms ownership and indexing state for all selected IDs. Chroma also filters by owner and those IDs. Relevant passages enter a bounded evidence context. The Direct Response Agent answers in one generation on the normal success path; an empty retrieval can abstain before calling Groq. Document chat bypasses the research graph and does not store document answers in conversation memory.

## Storage responsibilities

| Store | Data |
|---|---|
| MongoDB | Users, owned document metadata and indexing state, and request telemetry. |
| Chroma **user_memory** | Existing conversation memory used by the general graph. |
| Chroma **document_chunks_minilm_v1** | Uploaded passages, vectors, owner and document IDs, chunk IDs, and pages when present. |
| Browser storage | Chat transcripts and source details shown in the workspace. |

Chroma was already in the project and provided persistent local vector search with the same lightweight embedding function for both indexing and lookup. Separate collections keep retrieval and memory independent without introducing a new service. Local disk is a practical development choice, but it binds the index to an instance and calls for a storage design before scaling.

## SSE and guardrails

The stream carries activity transitions, text chunks, and terminal events. Agent durations come from the backend monotonic clock. The frontend merges activity by step ID and displays terminal answer text and citations. A timeout or disconnect stops later work where possible. A synchronous provider or vector call already in progress may return later.

The document prompt sends instructions in a system message and evidence as structured JSON in a separate message. Evidence cannot grant tools or access to other documents. Ownership is enforced before and after retrieval. Valid citation labels map back to actual retrieved passages, and missing or invented-only citations are rejected. This structural check cannot establish whether every claim follows from its cited passage, and prompt boundaries cannot guarantee that a model ignores every malicious instruction.

## Observability lifecycle

Middleware creates one measurement per chat request. Processing starts recording account telemetry after route validation and authentication. The activity recorder keeps executed steps only, and Groq calls are counted at the application client boundary, including dispatched retries. A returned passage count measures selected evidence supplied to the answering path, not citation count.

At termination the middleware captures server-side duration from request entry through the final ASGI response write or disconnect. It schedules an idempotent MongoDB write by request ID outside the answer path. The bounded, in-process writer can drop records on overload, database failure, or process exit. Cancelled or timed-out requests can have steps without a duration, and an already-sent terminal event may complete at the server even if the browser does not receive it. The JSON endpoint does not reliably observe client disconnects. Missing account IDs on legacy tokens, invalid requests, and auth failures are excluded. There is no historical backfill or automatic retention policy.

All observability endpoints require a verified account ID. The owner filter is applied before MongoDB aggregation, pagination, or detail lookup. Successful outcomes alone count in the success-rate numerator. Partial, failed, cancelled, and timed-out records remain in the denominator. The failed card includes timeouts. Agent failure rates use completed and failed steps as the denominator, so unfinished steps do not inflate measured failures. The dashboard does not infer token usage, provider billing, or timings for unfinished steps.

## Evaluation

The evaluation CLI indexes fixed synthetic fixtures into isolated Chroma and test metadata, runs the retrieval path, and compares stable passage IDs against relevance labels. It checks structural citation integrity and labelled fact support with deterministic answer fixtures. There are no Groq requests in the benchmark. An explicit CLI flag publishes only aggregate metrics from the synthetic dataset for the authenticated dashboard. The detailed local artifact stays ignored. The benchmark cannot establish production accuracy or comprehensive resistance to prompt injection.
