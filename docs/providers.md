# Live specialist providers

All three providers are wired through small `httpx` adapters. No request occurs until a live round is explicitly invoked. Local offline comparisons never call them.

Set these in `.env`, using the model identifiers available to your accounts:

```dotenv
OPENAI_API_KEY=your-local-key
OPENAI_MODEL=your-openai-model-id
ANTHROPIC_API_KEY=your-local-key
ANTHROPIC_MODEL=your-claude-model-id
GEMINI_API_KEY=your-local-key
GEMINI_MODEL=your-gemini-model-id
MECHANICS_PROVIDER=openai
FABRICATION_PROVIDER=anthropic
MEASUREMENT_PROVIDER=gemini
```

`GOOGLE_API_KEY` is accepted as a Gemini alias. Anthropic credentials spanning multiple workspaces may also require `ANTHROPIC_WORKSPACE_ID`. Keys are excluded from provider representations, errors, API configuration responses and evidence exports. `.env` is ignored by Git. Do not use a chat subscription login token in place of an API credential.

Model names are intentionally not silently chosen. All providers must support text generation and the configured JSON response mode. This implementation uses OpenAI Responses with JSON-object output, Anthropic Messages with JSON instructions, and Gemini `generateContent` with JSON MIME output. Every returned object is validated against bounded contracts; refusals, truncation and malformed JSON are explicit failures. Models with extensive internal reasoning may need a larger output allowance than this prototype's bounded 800-token response cap.

The default assignment is OpenAI for mechanics, Claude for fabrication and Gemini for measurement. Roles may be reassigned using the `*_PROVIDER` variables. All agents can propose; each reviews the peer proposals. The application executes only a valid action supported by at least two agents. LLM responses cannot fabricate observations, change frozen limits, execute code or certify physical acceptance.

Each call reserves a conservative text-byte input allowance plus its capped output. Actual provider usage replaces the reservation. Missing usage or timeouts remain conservatively charged; no automatic retry is made. The run-wide budget aggregates all three providers. It is a local token admission limit, not a dollar-spend guarantee. The service records provider/model, round, tokens, evidence references and decision outcome. Prompt data includes numerical measurements and geometry; human notes/raw images are excluded.

Adapters have been tested with mocked HTTP responses, including failure accounting and all-three-provider routing. Live authentication, account access and model-specific behavior still require verification with your credentials.

Official API contracts checked during implementation:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create) (API version `2026-10-21`)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)

## NVIDIA hosted models (build.nvidia.com)

`*_PROVIDER=nvidia` routes a specialist to NVIDIA's OpenAI-compatible endpoint
(`https://integrate.api.nvidia.com/v1/chat/completions`, `Authorization: Bearer nvapi-...`).
Set `NVIDIA_API_KEY` and `NVIDIA_MODEL`. Verified 2026-10-31 with
`nvidia/nemotron-3.5-lightning-30b-a3b`. For this key, `llama-3.3-nemotron-super-49b-v1.5`
returned HTTP 410 (retired) and `nemotron-3-super-120b-a12b` HTTP 404, although both are
listed in NVIDIA's docs. `GET /v1/models` shows what a key can use.

- Reasoning is turned off with `chat_template_kwargs: {"enable_thinking": false}`
  (`NVIDIA_NO_THINK=1`, the default). On the verified model this cut a tiny JSON reply
  from 225 to 16 output tokens and 3.1 s to 0.3 s. JSON is parsed from the reply text;
  `<think>` blocks and code fences are stripped. Truncation (`finish_reason` other than
  `stop`) is an explicit failure.
- Calls are spaced by `NVIDIA_MIN_INTERVAL_S` (default 1.6 s) for free-tier rate
  limits; failures are never retried automatically.
- Free-credit calls are counted against `SCIENCECLAW_NVIDIA_MAX_CALLS` (default 2000),
  separately from the paid providers' dollar and call limits. No dollar price is claimed.
- `MECHANICS_MODEL` / `FABRICATION_MODEL` / `MEASUREMENT_MODEL` optionally give each
  specialist its own model. `SINGLE_AGENT_PROVIDER` / `SINGLE_AGENT_MODEL` configure the
  single-agent baseline (default: the mechanics assignment).

## Agent teams, concurrency and private context

- **Team per study.** The New study form's *Agent team* picks one provider and model for all
  three specialists and the single-agent baseline (one family, consistent behaviour), frozen
  with the study (`run.team`). "As set in .env" keeps the per-role `*_PROVIDER`/`*_MODEL` settings.
  Default NVIDIA model: `nvidia/nemotron-3-ultra-550b-a55b` (verified 2026-10-31, ~0.7 s for a
  short JSON reply with thinking off). `nemotron-3.5-lightning-30b-a3b` also works.
- **Concurrent stages.** The three specialists propose at the same time (none sees another's
  proposal for that round), then review at the same time. Token admission is reserved under a
  lock including calls in flight; a call that does not fit waits for in-flight calls rather
  than failing, so tight budgets degrade to sequential calls.
- **Shared information** is the evidence record: measurements, model fit, requirements,
  progress summary and the peers' published proposals and reviews.
- **Own context.** Every call is a separate conversation with its role's instructions. Each
  agent may keep a private `notebook` (600 characters) that only it receives next round
  (`context.local_state.notebook`). Notebooks are shown to people in the Agents view.
- **Normalization.** Fields with exactly one legal value (a design's proof condition, any
  action's cost) are filled in and logged as `normalized`; no check is relaxed.
- **Failed replies.** A malformed reply or provider error makes that agent abstain for that
  stage; the others continue. Only token-budget exhaustion (or every call failing) ends a
  decision. The single agent gets the same tolerance: a bad reply is retried like an invalid
  attempt. Before this, one bad reply ended a collective study's search. A failed *review* is
  retried once, because a missing owner review would block the proposal.
- **Output caps.** Proposals get 800 output tokens and reviews 1600. Reviews of three proposals
  with findings were being cut off at 800 tokens (invalid JSON). Provider timeout is 90 s
  (`PROVIDER_TIMEOUT_S`).

## Specialist charters (who sees what, who decides what)

Each specialist has its own evidence, tools and decisions (`agents.CHARTERS`), following the
proposal's agent table. They are not three copies of one reviewer.

| Agent | Sees (its own context and tools) | Proposes | Must approve |
|---|---|---|---|
| Mechanics | Model parameters and fit, residuals, section analysis (I, predicted deflection, bending vs mount share, root stress, requirement failures), recommended geometry | Tests or designs | Every design |
| Fabrication | Process limits, fabrication and print records, material use, screened design templates (lightest grid geometries that pass the requirement check). **No raw load data, no model parameters** | Designs only | Every design |
| Measurement | Load records with uncertainty, fixture motion and quality flags; station coverage, signal-to-noise and mount share per station; identifiability. **No material parameters** | Tests only | Every test |

- **Ownership rule.** A proposal needs two supporters *and* its owners: designs need mechanics
  and fabrication, tests need measurement. An owner's objection is logged as `owner_objection`.
- **Stress tests.** In review, each specialist receives `your_stress_tests`: what its own tools
  compute for every proposal (mechanics: margin, stress, model transfer; fabrication: material,
  grid steps, fabrication budget; measurement: signal-to-noise, pass margin in σ, repeats).
  Reviews carry `findings`, shown under *Stress tests* in the Agent activity timeline.
- **No master agent.** Coordination is a fixed, published protocol, not an agent with authority:
  propose, review, ownership plus quorum, deterministic tie-break. A boss agent would turn the
  collective into a single decision-maker with helpers, which is what the single-agent baseline
  already measures.
- **Baselines.** The independent baseline uses the same charters and views without peer review.
  The single agent sees every view and may propose anything.
