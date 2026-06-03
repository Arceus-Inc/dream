# Self-Improving Systems — Synthesis Notes

> Synthesized from video transcripts. Each section distills key ideas, mechanisms, and takeaways — paired with concrete implementation knowledge grounded in the Arceus harness specs (`docs/specs/new-specs/`), which turn out to be a direct reference implementation of this thesis.

---

# Part I — Self-Improving Companies as Recursive AI Loops

## 0. The reframe: companies are Roman legions, and AI breaks the legion

Today's companies are structured like **Roman legions** — nested hierarchies with consistent spans of control, where *named humans are the conduit for information flowing up and down*. The legion existed to project control from a center (Rome) to the edges (Hadrian's Wall). The org chart is the same machine: humans relay orders down and reports up.

The claim: **AI dissolves the need for humans-as-conduits.** The "copilot" framing — AI making each engineer 20-30% more productive — is a category error. It bolts a bigger engine onto the old shape. The real move is to **redefine the unit of value**: a company is not a hierarchy of people, it is **a set of recursive, self-improving AI loops** that keep getting better while you sleep.

**Why this is the right mental model technically:** a hierarchy is a routing topology for scarce decision-making. When decision-making is no longer scarce (tokens are cheap, judgment is partially automatable), the optimal topology flattens. What remains is (a) the *loops* that do the work and improve themselves, and (b) humans at the *boundary* where the system touches reality.

---

## 1. The anatomy of a self-improving loop

The talk gives five layers (from Diana's framework). Each maps to a concrete, buildable component. The Arceus harness specs (`docs/specs/new-specs/`) are essentially a reference implementation of this exact loop, so each layer is anchored to its spec.

| Loop layer (talk) | What it is | Arceus implementation |
|---|---|---|
| **Sensor layer** | Signals from the world: customer emails, support tickets, code changes, cancellations, telemetry | Per-session JSONL event stream + OTel-shaped traces; one line per LLM call / tool call / validator finding / state transition ([07](docs/specs/new-specs/07-verification-evals-observability.md)) |
| **Policy / decision layer** | Rules: what's allowed, what needs human permission, what must be logged | Sandbox tiers + governance auto-merge blocks; static memory is read-only to the agent, written only via human-approved PR ([08](docs/specs/new-specs/08-sandbox-security-governance.md), [10](docs/specs/new-specs/10-memory-and-self-evolution.md)) |
| **Tool layer** | Deterministic APIs the AI calls: query DB, check calendar | Tools / skills / MCPs as the only action surface; writes outside permitted tiers refused at the tool layer ([05](docs/specs/new-specs/05-tools-skills-mcps.md)) |
| **Quality gate** | Evals, safety filters, human review for high-risk | Verify-like-a-user (Playwright for UI, project test runner otherwise) → score against weighted rubric → Ralph-Wiggum reviewer subagent before "done" ([07](docs/specs/new-specs/07-verification-evals-observability.md), [03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) |
| **Learning mechanism** | Catch where it failed, feed it back to the top | Dream phase (nightly curation) + tech-debt auto-filing on every verification failure ([10](docs/specs/new-specs/10-memory-and-self-evolution.md), [07](docs/specs/new-specs/07-verification-evals-observability.md)) |

The key property: **if every layer runs with minimal human intervention, the loop compounds.** Humans move into a *supervisory* role; you "throw tokens at the problem" and the org improves overnight.

### Implementation note — the turn as the atomic loop

Arceus pins the loop down as an explicit FSM so it can be retried, observed, and shared ([03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)):

- **Session:** `starting → orienting → working → sealing → done|aborted`
- **Turn:** `read → plan → act → verify → record`

The `verify → record` edge is where the loop becomes *self*-improving: verification outcomes become structured signal, and `record` writes them durably. This is the smallest unit that "gets better while you sleep" — everything larger (the dream phase, cron-driven optimization) is built on top of guaranteeing this turn shape never breaks half-way.

---

## 2. The "holy shit" moment, decomposed

The talk's pivotal example — a monitoring agent at YC that watches every employee's DB query, notices failures, and **overnight writes the fix, opens a PR, has an agent review and merge it, so the same query succeeds the next morning** — is not magic. It's three primitives composed:

1. **Observe every interaction as structured signal.** Every query is a `tool.call`/`tool.result` trace event with an outcome. (Arceus: [07](docs/specs/new-specs/07-verification-evals-observability.md) — "agent struggle is signal.")
2. **Auto-file the gap.** A failed query that pattern-matches to a missing capability ("need a new DB view / index / skill / tool") appends a **tech-debt entry** naming the missing capability. The matcher is deliberately *conservative* (false negatives over false positives). (Arceus: tech-debt auto-filing, [07](docs/specs/new-specs/07-verification-evals-observability.md) + [09](docs/specs/new-specs/09-task-engine-and-cron.md).)
3. **Close the loop off the hot path.** A nightly cron job reads the day's failures + tech-debt + sessions, and *acts*: writes the code, opens a PR, runs the reviewer subagent, merges low-risk changes. (Arceus: **dream phase**, `kind: dream-curate`, runs ~03:00, [10](docs/specs/new-specs/10-memory-and-self-evolution.md).)

**The build recipe to reproduce the YC moment:**

> Instrument every employee/agent action as a trace event with an outcome → conservatively classify failures into "missing capability" tickets → run a nightly autonomous job that converts those tickets into reviewed, merged PRs → gate anything risky behind human approval.

Crucially, Arceus draws the safety line exactly where it must be: the self-improvement loop can rewrite **tools, skills, views, indexes, and wiki knowledge**, but it **cannot rewrite its own constitution** (`core-beliefs.md`, product specs) without a human — those commits get tagged `[governance-touch]` and are refused a direct push to the default branch, and cron jobs are forbidden from auto-merging governance PRs. This is what prevents "belief drift": six weeks of unsupervised self-editing turning the agent's core principles into something nobody agreed to.

---

## 3. Make everything legible: record → diarize → synthesize → serve

> "If it's recorded, it happened to the AI. If it wasn't recorded, it didn't happen to your intelligence."

This is the load-bearing operational discipline. Three sub-mechanisms:

### 3a. Record everything

Emails, Slack, DMs, office hours, calls, hallway promises. The ambition is total capture (mic'd rooms, smart glasses, clips). Technically this is just: **every interaction must produce a durable artifact in the system of record.** Arceus formalizes this as **"the repo is the only authoritative source"** — state outside the repo (in a DB, in someone's head, in chat) makes the harness uninspectable and unreproducible; putting it in git gets you diffs, rollback, blame, and branches for free ([01](docs/specs/new-specs/01-repo-as-system-of-record.md)).

### 3b. Diarize / compress (you can't dump 100k hours into a context window)

Raw capture is unusable; you synthesize it into "breadcrumbs." Arceus implements this at two scales:

- **Working-memory compression:** per-task scratch memory has a hard cap (50 KB); past the cap a small LLM call compresses it in place, preserving every actionable insight ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)).
- **Dream-phase curation:** the nightly job reads the day's sessions, distills them into atomic **wiki entries** (one concept per file, with `confidence` / `times_referenced` / `last_used` frontmatter), and garbage-collects stale knowledge quarterly into an `_attic/` (never deletes) ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)).

### 3c. Serve it as a living brain

The YC user manual example is the template: 2,000 hours of recorded office hours → diarize → categorize (fundraising, hiring, co-founder disputes) → regenerate → **150-page manual, dramatically better than the decade-old original, updated monthly.** Every new piece of advice is compared against the manual and either incorporated or discarded. Then it's piped as context into an agent → "the combined wisdom of 16 partners in one" — *but only if it's legible.*

Arceus's three-tier memory makes the "living brain" governable rather than a free-for-all:

- **Working memory** (`.harness/sidecars/{task-id}/`) — ephemeral, agent writes freely, dies with the worktree.
- **Wiki memory** (`docs/wiki/`) — agent-curated long-term knowledge; **written only by the dream phase**; in-session agents propose via a `_proposals/` queue, never write directly.
- **Static memory** (`core-beliefs.md`, product specs) — human-curated constitution; agent reads freely, writes only via governance-gated PR.

The "compared with the existing manual and either incorporated or thrown away" mechanic in the talk *is* the proposal queue → dream-phase merge/reject flow.

---

## 4. Software is ephemeral; context is the asset

A counterintuitive but critical inversion:

- **Store data preciously, forever.** (Gary keeps all email in markdown — never throw data away.)
- **Treat software as disposable.** Codex-class models can one-shot most internal dashboards/tools to high quality; regenerate them as models improve every month or two.
- **The durable asset is comprehension** — the business context and skills ("this is how we run a YC event"). The software that runs the event is throwaway; the *instructions* that generate it are the value.

Arceus encodes this exactly: **"`docs/` is *the* product, not a side artifact."** Skills/tools (the executable surface) are regenerable; the system of record (data + design docs + wiki) is the precious, version-controlled core. Software is downstream of context, not the other way around.

**Practical implication for builders:** invest in the *context pipeline* (capture, diarization, memory tiering, governance) — not in any specific dashboard. The dashboard is a render of the context; the context is the company.

---

## 5. Org-design implications

### Burn tokens, not headcount

Revenue per employee is climbing fast (~5x in 18 months at YC demo day). The binding constraint is shifting from headcount to **token budget**. Token usage is a crude, gameable proxy — *don't* turn it into a promote/fire leaderboard — but **directionally** it identifies who is "token-maxing" (exploring what's newly possible) and worth your attention. We're in a "what is even possible" phase; experiment to the max.

### Middle management is over

The coordination problem middle management solves is exactly what the loop automates (routing, status aggregation, prioritization). Two roles remain:

1. **Everyone is an IC / builder / operator.**
2. **Every task has a DRI** — a single *named human*, never a committee.

This is the same principle Arceus enforces at the machine level: every session, every commit, every PR is attributable to a named actor with an audit trail. "DRI for every task" is the org-chart version of "one sealed session, one commit, one accountable author."

### Where humans still matter — the boundary

The **company brain** (all data, DMs, skills, know-how) sits in the middle. **Humans live at the edge**, where the intelligence makes contact with reality and reaches places the models can't go yet:

- Novel situations the loop has no precedent for
- Ethical / high-stakes / high-emotion judgment calls (a founder considering breaking up with their co-founder)
- Sales and relationship moments ("a human in the room for the next 20 years")

Arceus draws the identical boundary: the **quality gate** and **governance layer** are exactly the points where a human is inserted for high-risk decisions, while everything routine flows through the autonomous loop.

---

## 6. Synthesis: the build checklist

If you were starting a company today, you'd build it in this shape from day one (and most startups are small enough to have no excuse):

1. **System of record first.** One authoritative store (the repo / a structured datastore). Everything that happens produces a durable, inspectable artifact. ([01](docs/specs/new-specs/01-repo-as-system-of-record.md))
2. **Instrument every interaction as a trace event with an outcome.** Sensing is just disciplined logging in a queryable schema. ([07](docs/specs/new-specs/07-verification-evals-observability.md))
3. **Define the loop as an explicit FSM** so turns can be retried, observed, and verified — `read → plan → act → verify → record`. ([03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md))
4. **Verify like a user, score against a rubric, review before "done."** Self-grading by the producing model gives over-confident green ticks; verify end-to-end through real tools. ([07](docs/specs/new-specs/07-verification-evals-observability.md))
5. **Treat failure as signal: auto-file the missing capability.** Conservative classifier → tech-debt tickets. ([07](docs/specs/new-specs/07-verification-evals-observability.md))
6. **Run a nightly autonomous "dream phase"** that converts the day's failures + signals into reviewed, merged improvements + curated knowledge — off the hot path. ([10](docs/specs/new-specs/10-memory-and-self-evolution.md))
7. **Tier your memory and gate the constitution.** Ephemeral working memory, agent-curated wiki (proposal-queued), human-only core beliefs. This is what lets the system self-edit *without belief drift*. ([10](docs/specs/new-specs/10-memory-and-self-evolution.md))
8. **Keep software ephemeral, data precious.** Regenerate tools; never lose context. ([01](docs/specs/new-specs/01-repo-as-system-of-record.md))
9. **Burn tokens, flatten management, name a DRI per task.**
10. **Put humans on the boundary**, not in the conduit.

---

## 7. Open questions / where no one has cracked it yet

The talk is candid that this is the bleeding edge — *no one yet has a truly self-improving company in every function.* Open problems worth tracking:

- **Total capture is unsolved in practice.** Hallway conversations, verbal promises, and tacit decisions still escape the record. Hardware (mic'd rooms, smart glasses) is the bet, but the ingestion + diarization pipeline at "100k hours" scale is not a product anyone has.
- **The token-usage proxy is gameable.** It works directionally today only because we're in an exploratory phase; the moment it becomes a formal metric it corrupts. No good replacement measure exists yet.
- **The boundary between auto-merge and human-gated will keep moving.** As models improve, more of the "high-stakes" set migrates into the autonomous loop. Where to draw the line is a live, per-domain judgment call — and getting it wrong in either direction is costly (belief drift if too loose, no compounding if too tight).
- **Self-improving in *every* function is the unproven claim.** Product, support, and internal-tooling loops are demonstrated; whether sales, fundraising, and culture can be looped (or are permanently human-boundary functions) is open.

---

# Case Study: Pulsia — Productizing the Autonomous Company

> A documentary profile of Ben (founder of **Pulsia**), an AI platform that builds and runs companies from a single sentence. Where the YC talk is the *theory* of self-improving companies, Pulsia is the same thesis sold as a consumer product. Most of the segment is founder-journey and go-to-market narrative; the durable engineering content is the **infrastructure economics of long-running autonomy**, extracted below and separated from the hype.

## 8. The thesis, productized

Pulsia's pitch: anyone — explicitly "the 99%", non-technical users — describes their dream business in a sentence, and AI agents build and run it end-to-end. The agent is meant to handle the *real-world* stack, not just code: open a bank account, talk to an accountant, do taxes, talk to a factory to manufacture a product, plug into services, and even **hire a human**. The product promise is "tell it your business, don't babysit it, let it run 24 hours straight." Stated end goal: the **first one-person billion-dollar company.**

This is the YC talk's "company = recursive self-improving AI loops" claim turned into a SaaS. Pulsia describes its own slice as "the **autonomous loops, the orchestrations, the memory layers**" — the same primitives as the loop anatomy in §1, just packaged for end users.

## 9. Autonomy is token- and compute-bound (the core engineering constraint)

The single most useful technical insight: **the unit of the product is "how long can it run unsupervised," and that is gated by token economics.**

- Top-model APIs (Anthropic, OpenAI) are expensive, so autonomy *duration* is cost-bound.
- The original product gave **one task per night**, then let users queue more tasks one at a time.
- The new feature — codename **"Boost"** (debated names: "god mode" / "yolo mode") — lets Pulsia **run continuously for hours**. The feature *is* "buy more autonomous runtime."

This is "burn tokens, not headcount" (§5) made literal: the headline metric a customer buys is autonomous run-length, which is directly token spend. Any harness that wants long unattended runs has to treat token budget as a first-class, user-visible resource — not an invisible backend cost.

## 10. Agent-native infrastructure must be ephemeral and usage-billed

The deepest infra point, and the one that escalates the first talk's "software is ephemeral" theme all the way to **"whole businesses are ephemeral."**

- The web's economic model was **built for humans**: one company builds one standing web server, with minimum commitment and continuous cost.
- That model breaks for agents. A Pulsia user might **launch 10 businesses in parallel and A/B test them**, where each is a *temporary business that exists for minutes*. You cannot pay $10/mo × 10 standing accounts for things that live for minutes.
- Therefore agent-native infra must be **ephemeral + pay-on-demand**: sandboxes that spin up in milliseconds, "pay when you use it, otherwise free."

This maps directly onto Arceus's worktree/sidecar model ([02](docs/specs/new-specs/02-filesystem-and-worktree.md)): per-task isolated worktrees that are created on demand and torn down with their working memory ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)). The economic principle generalizes: **the disposable unit keeps getting bigger** — from a regenerated dashboard (talk 1) to a regenerated worktree (Arceus) to an entire spun-up-and-discarded business (Pulsia).

## 11. Sandboxing is a security *and* cost primitive

Agents "go wild and try to break everything if they can," so containment is first-class. Two motivations, one mechanism:

- **Security:** keep the agent "in the corridor" — a bounded sandbox it cannot escape.
- **Cost:** tighter, ephemeral sandboxes reduce Pulsia's cost, which reduces customer cost, which wins more customers.

This is exactly Arceus's sandbox-tiers + governance posture ([08](docs/specs/new-specs/08-sandbox-security-governance.md)): the agent acts freely *inside* a bounded surface, and the dangerous edges (real money, real-world side effects, constitution edits) are gated. Pulsia's "stay in the corridor" is the consumer-scale version of Arceus refusing writes outside permitted tiers at the tool layer ([05](docs/specs/new-specs/05-tools-skills-mcps.md)).

## 12. The looming wall: compute supply, not headcount

To go from ~$7M to ~$70M run rate, the bottleneck becomes raw compute. How GPU procurement actually works (per Ben, explained for a lay audience):

- Vendors **resell GPUs bundled with a model**; you **reserve racks for 1–3 years**, choose which open-source model to run, route all traffic to them, and pay only for the GPUs — then it's on you to optimize utilization.
- This is distinct from hitting Anthropic's API directly (where Anthropic runs the GPUs for *their* model).
- **"GPUs are a black market"** — they sell out instantly; you compete with buyers in Asia and everywhere. Allocation is won through **connections + investors who route you to priority compute.** Even getting top open-source models to run correctly on rented GPUs is "still a mess."
- Endgame ambition: **own data centers**, fully optimized network/storage/GPU for the agentic platform.

Takeaway for builders: once the loop works and scales, the binding constraint moves from *intelligence quality* to *compute allocation* — a supply-chain/relationships problem, not an engineering one.

## 13. The build-it-thin strategy: orchestration layer + infra partners

Pulsia explicitly owns only a *slice* of the stack and partners for the rest — "a solo founder, but it takes a village" (the Lord of the Rings "I have the ring but need you to come to Mordor" framing). "Working with other people without hiring employees."

- **Pulsia owns:** the end product — autonomous loops, orchestration, memory layers, consumer growth + education.
- **Sapium:** payment rails + API rails, security, scaling (1k → 1M customers), sandboxing. Original deal: "you build the rails, I bring the customers — we win together."
- **Anchor Browser:** agentic browser automation that acts on the web without tripping human-or-bot checks (needed because non-technical users ask for real web tasks).
- **Browserbase + others:** more of the stack.

The lesson rhymes with "software is ephemeral, context is the asset" (§4): don't rebuild the internet — own the *orchestration + memory + customer relationship* (the durable, compounding parts) and rent the commoditized substrate.

## 14. Operating philosophy (the founder's explicit advice)

The closest thing to a thesis statement, and it directly echoes the YC talk's "everyone is an IC, name a DRI, middle management is over":

- **Before product-market fit:** force yourself **solo or a micro-team** (you + maybe one person). Use AI all day (Claude, Codex, Pulsia).
- **After PMF:** get **AI to replace employees.**
- **Why:** staying lean forces you to *fully understand where the edge is*, and understanding the cutting edge is the only way to win in this age. Hire too fast and you build on someone else's knowledge — "that's where you may lose." → "Stay lean, stay solo, and go get them."

Same principle Arceus enforces mechanically: small accountable units, full attribution, and the human kept at the boundary (§5) rather than stuffed into a coordination hierarchy.

## 15. Caveats — separating signal from hype

This segment is a vlog, so weight it accordingly:

- Traction numbers ($30M seed, ~$7M ARR in ~a month, "billion-dollar one-person company") are founder claims in a promotional context, not verified engineering facts.
- Go-to-market advice ("brute force every channel," rebrand boring features as "god mode," SF-serendipity) is real and shrewd, but it's *narrative strategy*, not system design.
- The **durable engineering takeaways**, stripped of hype, are four: (1) autonomy is token/compute-bound and should be a user-visible resource; (2) agent-native infra must be ephemeral + usage-billed because the disposable unit is now an entire business; (3) sandboxing is simultaneously a security and a cost lever; (4) compute supply is the real scaling wall, won through relationships, not code.

---

# Part II — Recursion at the Substrate Level

> Source: YC *Decoded*, on recursion at inference time — the **HRM** (Hierarchical Reasoning Models, Sapient) and **TRM** (Tiny Recursive Models, Alexia Jolicoeur-Martineau) papers from 2025. This part drops *below* the org and product layers to the model architecture itself — and finds the **same law** operating there.

## The throughline (org → product → model)

The same principle keeps surfacing at every altitude:

> **Iterative refinement with reused machinery beats one-shot brute force — and the loop, not the size, is what produces intelligence.**

- **Org level (Part I):** a company is a set of recursive self-improving loops; software is ephemeral, the *loop + context* is the asset.
- **Product level (Pulsia):** the product *is* loop-runtime; customers buy "more autonomous iterations" (Boost).
- **Model level (this part):** reasoning is an *outer refinement loop* reusing one small net; recursion buys depth that parameters cannot.

The "outer refinement loop" in TRM is the architectural twin of the agent turn-loop (`read → plan → act → verify → record`, [03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) and the nightly dream-phase loop ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)). **Same primitive, three altitudes.**

## 16. Reasoning is bounded by *steps*, not by *knowledge*

A one-shot transformer with a fixed layer count has a fixed *compute budget per token*. Some problems are **incompressible** — they provably require a minimum number of sequential steps (comparison sort: n log n; also Sudoku, mazes, rolling sum). A 30-layer model on a 31-element list *literally cannot finish* — not undertrained, just out of steps. **Intelligence has a depth dimension that parameters don't buy.** This is the cleanest argument for why "just make it bigger" hits a wall on *reasoning* specifically, as opposed to knowledge recall.

## 17. Two ways to add depth — the field mostly bought the expensive one

- **Parameter depth:** stack more layers / params (what LLMs did).
- **Compute depth via recursion:** reuse the *same* weights in a loop (what HRM/TRM do).

TRM gets compute depth *without* parameter depth: a **7M-param model beats 100B+ models on ARC** by looping. Melanie Mitchell's framing: bigger is **sufficient but not necessary**; recursion is **also sufficient but not necessary.** The industry optimized one knob (size) and under-explored the other (depth-by-iteration) — because recursion was hard to *train*, not because it was worse.

## 18. The bottleneck was never the idea — it was the gradient

Recursion has been "known" since the 2010s RNN era. What killed it was **backprop-through-time (BPTT)**: vanishing/exploding gradients plus having to retain every activation ("a million copies of your brain"). The breakthrough is a **cheaper way to learn a recursive process** — don't backprop through the whole unroll; **truncate at t=1.** The architecture was always viable; the training method was the prison. General lesson: when a powerful idea looks dead, check whether it's the *idea* or the *optimization* that failed.

## 19. Chain-of-thought is recursion in the wrong space — and it's capped by human knowledge

CoT gives LLMs Turing-completeness at test time, but it's recursion in **discrete token space**, bolted on, and **bounded by the training data's frontier.** If only bubble sort exists in the corpus, CoT will never *derive* merge sort. Both common hacks — CoT and tool-use (`call sort()`) — inherit the **ceiling of human knowledge.** Genuine novel reasoning (the "Einstein in 1911 rebuilds physics" test) needs reasoning in **continuous latent space**, where the model can *discover* an algorithm it was never shown. TRM does exactly this on Sudoku **with no chain-of-thought traces** — *discovering the method without being taught it* is the real headline.

## 20. The unit of learning can be a *memory state*, not an input

HRM/TRM re-run the loop **without resetting the hidden carry** (ZL/ZH), so each pass starts from a different point in memory space on the *same* input. The reframe worth keeping: **"you're constructing a mini-batch not from different data but from different memory states."** You can manufacture learning diversity from internal state, not just from more data.

## 21. "We don't know why it works" — stated out loud

The deep-equilibrium / fixed-point justification *technically doesn't hold* (residual deltas don't go to zero), yet it works — and TRM then shows backpropping *further* works better. The researchers are explicit that the **theory is post-hoc; the empirical result led.** A faithful model of how the field actually moves: ablate aggressively, keep what survives, theorize later. (This is the same posture as "agent struggle is signal" — let the failures, not the priors, drive the next change, [07](docs/specs/new-specs/07-verification-evals-observability.md).)

## 22. Bio-inspiration is a muse, not a spec

The recurring ML pattern: start bio-inspired → veer to the bio-*implausible* variant that runs better on a GPU (AlexNet's brain-like local response normalization was dead weight; VGG's "just go deeper, 3×3 convs" won). The more useful lens is **automata theory**: treat the hidden carry as a **learned Turing tape / radix-sort memory bank** the model learns to use in a single forward pass. Reach for CS theory over neuroscience when deciding *what to actually build*.

## 23. The likely synthesis: LLMs build the space, tiny recursive models reason inside it

LLMs are spectacular at **finding rich latent/embedding spaces** but barely reason *within* them — their reasoning is routed back out through token space. The endgame isn't LLM *or* recursion, it's:

> a **big model** to map raw input → clean latent space, then a **tiny recursive reasoner** operating *inside* that space, plus a **non-BPTT** way to train deep recursion.

The decomposition is the actionable bet: **representation is scale's job; reasoning is recursion's job.** Slam the two together (giant LLM + heavy recursion + something other than BPTT) and "it's going to take off."

## Technical appendix — HRM vs TRM at a glance

| | **HRM** (Sapient) | **TRM** (Jolicoeur-Martineau) |
|---|---|---|
| Params | ~27M | ~7M (3–4× smaller) |
| ARC-1 | ~70% (vs o3 ≈ 0 at the time) | ~87% |
| Pretraining | None (tabula rasa, ~1k ARC tasks) | None |
| Recursion levels | 3: low (TL) → high (TH) → outer refinement (N) | Same outer loop; collapses low/high into one weight-shared `net` |
| Net | lnet + hnet, 4 transformer layers each | Single shared `net`, **1 layer** (MLP beat attention on Sudoku; lost on mazes) |
| Gradient trick | DEQ / fixed-point; backprop once, `stop_grad` | **Truncated BPTT at t=1**, but through **one full recursion loop** |
| Carries | ZL (low hidden state/"carry"), ZH (high) | Same, kept distinct (named x/y in the paper; y is latent, not a label) |

**What survives the ablations (the magic to keep):**
1. The **outer refinement loop** is the dominant source of gains (Constantin's scaling ablations at NDEA).
2. **Truncated BPTT at t=1 is sufficient** — counterintuitive and under-explored.
3. **Weight-sharing + tiny net** is enough; depth-by-recursion replaces depth-by-parameters.
4. **EM-style alternation:** update ZL conditioned on (X, ZH); update ZH conditioned on ZL; ZH is a *candidate latent answer one MLP lookup from the truth*. On Sudoku: ZL does scratch work and proposes, ZH commits a cell, loop fills the puzzle.

**Caveat / scope:** HRM and TRM are **task-specific** (a Sudoku model can't do ARC without retraining); LLMs are general-purpose. The open problem is making recursive reasoners **general** — which is what §23's synthesis is reaching for.

---

# Part III — Recursion Aimed at Its Own Substrate (the lab altitude)

> Source: an interview with **Richard Socher**, CEO/co-founder of **Recursive Super Intelligence (RSI)** — a lab building recursive self-improving superintelligence to *automate knowledge discovery*. Co-founders span the open-endedness / self-improvement research lineage (Tim Rocktäschel — rainbow teaming; Jeff Clune — Darwin Gödel Machine, AI Scientist; Alexey Dosovitskiy — vision transformer; Caiming Xiong — prompt engineering; Josh Tobin — OpenAI deep research/Codex/agents; Tim Shi — Cresta).

## The missing middle altitude — and the sharpened throughline

This is the institution between Part I (org) and Part II (model): the **research lab as a recursive self-improving engine.** It connects them with one mechanism — *recursion aimed at the thing that produces the recursion*:

- **Part I (org):** the company improves its own tools/skills overnight.
- **Part III (lab):** a lab builds a machine that improves AI research *itself*, then exports it to all of science.
- **Part II (model):** a model improves its own reasoning by looping.

So the throughline sharpens from "the loop beats size" to:

> **The highest-leverage loop is the one pointed back at its own substrate.** AI that can *code* can improve the AI; a company that can edit its own skills compounds; a model that loops on its own latent state reasons deeper. Self-improvement is recursion turned on the machinery that produces the recursion.

It also adds two conceptual tools the earlier parts lacked: **open-endedness** (no terminal benchmark) and **knowledge-per-watt** (a thermodynamic efficiency frontier).

## 24. The "why now": AI is code → AI can code → close the loop

The precise trigger for RSI being viable *now*: it went from "**AI is code**" (models are software) to "**AI can code**" (models write software). The instant both are true, the system can **modify its own substrate** and the self-improvement loop closes. This is the cleanest one-sentence statement of why recursive self-improvement crossed from sci-fi to fundable in ~2025 — and it is the same event as the YC "monitoring agent writes its own PR overnight" moment (§2), generalized from *the company improving its tools* to *the AI improving the AI*.

## 25. Open-endedness vs benchmark-chasing

The dominant paradigm optimizes a *fixed target* — "get 100/100, then you're done." Biological and cultural evolution are **open-ended**: processes that keep inventing novelty with no terminal state (amino acids → eyes → brains → culture, and it never stops). The bet: as benchmark curves flatten, the field must switch from *optimizing-to-a-ceiling* to *open-ended generation of novelty*. This is the deepest idea in the talk, and it is exactly the posture of the Arceus dream phase — continuous curation with no "done" state ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)) — as opposed to a one-shot eval pass.

## 26. Knowledge-per-watt — the efficiency frontier nobody prices

The brain runs at ~20W; a B200 GPU at ~1000W. Evolution already found a vastly more energy-efficient intelligence — proof the current trajectory is far from the efficiency Pareto frontier. Framing intelligence as **"new knowledge per watt"** turns it into a thermodynamic problem and implies the biggest wins may be *off the current scaling trajectory entirely*, not further along it.

## 27. Biology is an existence proof, not a blueprint — expect to beat it

The bird → airplane analogy (the bio-plausibility theme of Part II, restated at lab scale): biology proves a capability is *possible* and reveals the underlying dynamics (aerodynamics), but the engineered version diverges and often surpasses it on the dimension you care about (planes fly faster; some are now near bird-light). There is a **whole Pareto frontier of intelligences**, and the point of a self-improving system is that it lets you **pick your point on that frontier** — trade cost for speed, efficiency for velocity — and optimize directly against that chosen objective. Same shape as TRM's "recursion buys a different point than scaling" (§17).

## 28. Adversarial self-improvement is the proven mechanism

Rainbow teaming (Rocktäschel): one LLM *attacks* another to elicit unsafe outputs, the attacks are harvested, and the original is **inoculated** — repeat. The transferable insight isn't the safety use; it's the **structure**: two AIs in an open-ended back-and-forth that keeps improving the system with no natural endpoint, now used across all major labs. This is the recursive loop applied to *capability generation itself*, not just task execution — the generator/evaluator or reviewer dynamic (Ralph-Wiggum loop, [03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) escalated to "make each other fundamentally better."

## 29. Timing is a symmetric risk; first-principles is the edge

- **Early kills you as surely as late.** "An academic ahead of their time is a *visionary*; a founder ahead of their time is *dead and no one cares*." Being early is a failure mode with the same outcome as being late — which is why the *why-now* question dominates over the *is-this-possible* question.
- **First-principles contrarianism:** Socher's arc (neural-nets-for-NLP went desk-rejected in 2010 → consensus today) yields the meta-lesson — don't optimize within the field's current frame; **find what's structurally holding the field back and solve it by reconceiving the whole problem.** The alpha lives in the unpopular-but-correct position and evaporates once it's consensus.

## 30. The new S-curve thesis (falsifiable)

Pre-training is hitting **logarithmic returns** — "add one or two orders of magnitude more data for minor improvements." The shared conviction: **RSI is the next step-function / new S-curve**, drawing gains from post-training, reasoning, and verticals rather than more pre-training data. Worth tracking as a concrete, falsifiable claim.

## 31. Bootstrap on the tightest loop, then export the machine

The concrete plan: build an AI with not a high-schooler's but **"50,000 PhDs"** worth of capability, automate the scientific method, and **apply it to AI research itself first** — the domain with the tightest feedback loop and where you own the substrate. Once that compounds, point the same **"Eureka machine"** at slower-feedback sciences: biotech/drug discovery, fusion, batteries, chemistry. The sequencing insight: **bootstrap where the loop is tightest and the feedback is yours (AI improving AI), then export the machine to domains where feedback is slow.** (This is the AI Scientist / Darwin Gödel Machine research line, productized.)

## 32. Stay small on purpose; delegate to the agents

Despite a megastar roster, the explicit goal is to **keep the human team as small as possible, at a very high bar, and delegate heavily to agents/AI.** Same "burn tokens, not headcount / everyone is an IC, humans at the boundary" principle from Part I (§5) — now stress-tested at the hardest case, a frontier *research lab*.

---

# Part IV — Evolving the Harness, Not the Model

> Source: **Agentic Harness Engineering (AHE)** — paper + framework (Fudan & Peking University, 2026; [arXiv 2604.25850](https://arxiv.org/abs/2604.25850), [code](https://github.com/china-qijizhifeng/agentic-harness-engineering)). Every prior part is a *talk* or a *theory* arguing self-improvement should work. This is the one that **ran the loop, measured it, and put a number on a public leaderboard** (#3 on Terminal-Bench 2.0 at 84.7% on GPT-5.5). It is the empirical keystone: the most direct real-world validation of the doc's throughline, and the part that maps almost 1:1 onto the Arceus specs.

## The throughline, completed (org → lab → model → *harness*)

Parts I–III moved across altitudes — company, lab, model — and found the same law: *the highest-leverage loop is the one pointed back at its own substrate.* AHE supplies the missing experimental proof at the altitude builders actually live at: **the scaffold around a fixed model.**

> You usually **can't retrain the frontier model.** So you recurse on the thing you *do* control — the harness. AHE freezes GPT-5.4 and evolves only the scaffolding, and it beats a hand-written harness **and transfers to other models and benchmarks without re-evolution.**

This is the doc's central claim reduced to its cleanest controlled experiment: hold the model constant, point the loop at the substrate you own, measure the compounding. The "self-improving company" of Part I and the "Eureka machine" of Part III are this same mechanism scaled up; AHE is it scaled *down* to something reproducible on a benchmark.

## 33. The sharp claim: the harness is a learnable, *transferable* artifact

The field spent 2023–2025 treating the **model** as the unit of improvement (better weights → better agent). AHE inverts it: freeze the base model, evolve only the scaffold — and get a bigger jump than most fine-tunes deliver: **Terminal-Bench 2 pass@1 69.7% → 77.0%** on GPT-5.4, beating hand-written Codex (71.9%) and the self-evolving ACE / TF-GRPO baselines.

The number isn't the point; the **transfer** is. They freeze the evolved harness and run it on (a) a different benchmark (SWE-bench-verified) and (b) **four other base models** — with *zero* re-evolution — and it still helps. If the harness were overfitting to Terminal-Bench, transfer would collapse. It doesn't. So the harness encoded **general software-engineering experience**, not benchmark tricks. Schmid's line is the whole thesis: **"Harness is the dataset. Your competitive advantage is the traces your Harness captures."** The traces are the training data, the harness is the trained artifact, the base LLM is just the fixed substrate it runs on.

This is the strongest possible answer to "is self-improvement real or hype?" — it's a controlled, ablated, leaderboard-ranked, transfer-tested *yes*.

## 34. Decomposition is the enabling move: 7 orthogonal components

The key engineering act is refusing to treat "the harness" as one blob of prompt+tools. It's split into **7 components**, each in its own git-tracked file/directory. **Orthogonal** means changing one doesn't disturb the others.

| # | Component | Job | Analogy | Arceus home |
|---|-----------|-----|---------|-------------|
| 1 | **System Rules** (`systemprompt.md`) | how the agent thinks, decides, its boundaries | constitution | `core-beliefs.md` + specs ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)) |
| 2 | **Tool Descriptions** (`tool_descriptions/*.yaml`) | schema + usage + gotchas the *LLM sees* | product manual | tool/skill/MCP catalog ([05](docs/specs/new-specs/05-tools-skills-mcps.md)) |
| 3 | **Tool Implementations** (`tools/*.py`) | the code that actually runs | robot factory | tool/skill/MCP code ([05](docs/specs/new-specs/05-tools-skills-mcps.md)) |
| 4 | **Middleware** (`middleware/*.py`) | pipeline hooks: intercept, transform, compact | security checkpoint | sandbox tiers + governance gate ([08](docs/specs/new-specs/08-sandbox-security-governance.md)) |
| 5 | **Skills** (`skills/*/SKILL.md`) | reusable workflow patterns / SOPs | playbook | skills ([05](docs/specs/new-specs/05-tools-skills-mcps.md)) |
| 6 | **Sub-Agents** (`sub_agents/*/`) | delegatable specialist units | outsourced team | reviewer subagent + roles ([03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) |
| 7 | **Long-Term Memory** (`MEMORY.md`) | persistent cross-session knowledge | notebook | wiki memory + dream phase ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)) |

Why orthogonality is load-bearing: it makes failures **attributable to a layer.** "The agent can't paginate search results" stops being "the agent is bad" and becomes "component 2 (the tool *description*) omits the `page_size` param the implementation already supports." The decomposition is exactly what converts a vague capability complaint into a single-file, revertible fix. The forbidden anti-patterns say the same thing from the negative side: don't write tool *logic* into System Rules (mixing 1 and 3), don't stuff memory into System Rules (context bloat every load), don't have two components do one job, and never change tool code without updating its description (2 and 3 drifting apart).

## 35. Three observability layers — the actual machinery

Self-improvement needs a *signal* to improve on. AHE's signal is three nested layers, and this is what makes it work rather than just being a nice taxonomy:

1. **Component observability (NexAU).** Every component is a file under git, so every edit is a diff — auditable and revertible. You can always answer "what changed, and can I undo it." This is the substrate that makes everything else safe — and it's literally Arceus's **repo-as-system-of-record** ([01](docs/specs/new-specs/01-repo-as-system-of-record.md)) applied to the harness itself.
2. **Experience observability (Agent Debugger).** The scale problem: one iteration's raw traces routinely exceed **10 million tokens** — unreadable by any optimizer, human or LLM. So the debugger *distills* them into layered reports (`analysis/overview.md` cross-task root causes + `analysis/detail/{task}.md` per-task deep dives), where **every claim links back to the originating raw trace.** Read digests by default, drill to the raw trace before committing. This is the same compression discipline as Arceus's working-memory cap + dream-phase curation (§3, [10](docs/specs/new-specs/10-memory-and-self-evolution.md)).
3. **Decision observability (Evolve Agent).** The meta-agent that edits the harness. It can only write inside `workspace/` (the 7 components), and every edit must carry a **Change Manifest** (§36).

The governing sentence: **"The trace, not the pass rate, is the unit every later step operates on."** A pass rate tells you *that* you failed; the trace tells you *why*. You cannot improve on a scalar. This is precisely Arceus's *"agent struggle is signal"* ([07](docs/specs/new-specs/07-verification-evals-observability.md)) — the failure trace, not the green/red bit, is what feeds the next improvement.

## 36. The Change Manifest — falsification applied to prompt engineering

This is the heart of the spec, and the single most transferable idea. Ordinary prompt engineering is vibes: "let me reword this and see." AHE makes every edit a **falsifiable hypothesis** with four mandatory fields:

1. **`failure_evidence`** — *what* failed, with a trace excerpt. ("T-042 failed when search returned >50 results; trace shows the agent tried `page=2` but the description never declared it.")
2. **`root_cause`** — *why*, not what. ("search supports pagination, but the description omits `page_size`/`offset`, so the LLM doesn't know it can paginate.")
3. **`targeted_fix`** — the specific change. ("add `page_size`/`offset` to `input_schema.properties`; document pagination.")
4. **`predicted_impact`** — `expected_fixes: [T-042, T-057]`, `at_risk_regressions: [T-013]`, plus rationale.

The root_cause/failure_evidence distinction is the whole game: "tool errored" is not a root cause; "the description didn't declare a param the implementation supports" *is* — and it names exactly one component to fix. Then comes the falsification: next iteration, predictions are checked against reality and a **verdict** is written back — `keep` (predictions held), `revert` (didn't help or caused regressions — roll it back), or `partial` (some held, adjust). **An edit whose prediction fails gets rolled back.** Popper in a prompt repo: a change survives only if it made a prediction *and the prediction came true*.

This maps directly onto Arceus's tech-debt auto-filing + proposal-queue + governance flow ([07](docs/specs/new-specs/07-verification-evals-observability.md), [10](docs/specs/new-specs/10-memory-and-self-evolution.md)): a failure becomes a sourced ticket (evidence + cause), an improvement becomes a queued proposal (targeted fix + predicted impact), and the dream-phase merge/reject is the verdict.

## 37. The loop — staggered generations automate falsification

`evaluate → analyze → improve → verify`, repeated (terminates on `target_pass_rate`, default 0.95, or `max_iterations`). The clever bit is **staggering**: each `runs/iteration_NNN/` holds two generations at once — `input/` is the workspace produced by loop `NNN-1` (just evaluated), `evolve/` is what loop `NNN` writes (evaluated *next* loop). So when you eval iteration NNN, the pass↔fail **flips** are attributed back to the previous loop's edits in `change_evaluation.json`. Falsification isn't a manual review step — it's structurally baked into the pipeline's cadence.

This is the same shape as Arceus's `evaluate → analyze → improve` at the *meta* altitude: the per-turn FSM (`read → plan → act → verify → record`, [03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) run one level up, where the "task" being worked is *the harness itself* and the nightly dream phase is the outer loop ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)).

## 38. Two principles that fall out: minimal start + component extrapolation

- **Minimal start.** A working harness needs only **2 components**: System Rules + one tool (description + implementation). Everything else (middleware, skills, sub-agents, memory) is added *only when earned*. The paper's finding: evolving up from minimal beats pre-configuring all 7 — every component that exists exists because a trace proved it was needed. Nothing is speculative. (Mirror of Part I §6's "start with the system of record + the loop, grow the rest.")
- **Component extrapolation.** If the same failure persists 2+ generations and fixing it at one layer isn't working — **roll back and re-solve at a different layer.** You keep patching the tool *description* and the agent still misuses the tool? Stop patching layer 2; maybe the real fix is a *middleware* that truncates the tool's output, or a *skill* encoding the correct call sequence. It's a search over *which layer owns the bug*, not just *what the fix is* — a discipline Arceus lacks an explicit name for but could adopt directly in the dream phase.

## 39. Why AHE is the keystone for this whole document

Every other source argues self-improvement *should* compound. AHE is the one that **closed the loop, ablated it, and ranked it.** And it validates the exact throughline — *the highest-leverage loop points at the substrate you control* — because the authors literally *couldn't* touch the model, so they recursed on the harness, and it worked **and transferred.**

The mapping onto Arceus is nearly 1:1, which is what makes AHE worth absorbing rather than merely citing — **Arceus's self-evolution layer is "AHE aimed at the product instead of the harness," and AHE proves that class of loop converges:**

| AHE | Arceus spec |
|-----|-------------|
| git-tracked file-level components | repo-as-system-of-record ([01](docs/specs/new-specs/01-repo-as-system-of-record.md)) |
| "trace not pass-rate is the unit" | verify-like-a-user + OTel traces, *"agent struggle is signal"* ([07](docs/specs/new-specs/07-verification-evals-observability.md)) |
| Change Manifest `failure_evidence`/`root_cause` | tech-debt auto-filing ([07](docs/specs/new-specs/07-verification-evals-observability.md)) |
| Evolve Agent writes only inside `workspace/` (7 components) | agent self-edits tools/skills/wiki but **not** the constitution; `[governance-touch]` gating ([10](docs/specs/new-specs/10-memory-and-self-evolution.md), [08](docs/specs/new-specs/08-sandbox-security-governance.md)) |
| `evaluate → analyze → improve → verify` outer loop | loop/turn FSM run at the meta altitude ([03](docs/specs/new-specs/03-loop-and-turn-lifecycle.md)) |
| Long-Term Memory evolution + minimal start | three-tier memory + dream phase + proposal queue ([10](docs/specs/new-specs/10-memory-and-self-evolution.md)) |

## 40. What Arceus could steal from AHE tomorrow

Three concrete, non-speculative borrowings:

1. **Adopt the Change Manifest as the proposal-queue schema.** Arceus already files tech-debt and queues wiki proposals; require each to carry `failure_evidence + root_cause + targeted_fix + predicted_impact`, then have the *next* dream phase write back a `keep / revert / partial` verdict. This turns self-edits from "looked reasonable at 3am" into falsifiable, auto-revertible hypotheses.
2. **Make the trace, not the score, the unit of the dream phase.** Distill the day's session JSONL into sourced root-cause digests (overview + per-task) with links back to raw traces — exactly the Agent Debugger pattern — so the curation step reasons over *why* failures happened, not just counts of them.
3. **Add component-extrapolation to the governance rules.** When the same failure mode recurs across N dream phases and the same-layer fix keeps getting reverted, escalate: try the fix at a different component layer (skill ↔ tool ↔ middleware ↔ system rules) before giving up. This is the missing "what to do when the obvious fix doesn't take" rule.

> **The unifying takeaway across all four parts:** self-improvement is recursion turned on the machinery that produces the recursion — and AHE is the proof that, even when you can't touch the model, pointing the loop at the *harness* compounds, generalizes, and survives the ablations. The substrate you control is enough.
