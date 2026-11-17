# Cloud platform alternatives to the Anthropic Console for running Claude (Fable 5.2, Opus 5.5, Sonnet 5.5), November 2026

Scope: an individual (personal Gmail, no company) who hit the Console's Start-tier $500/month cap. Sources are mainly Anthropic's live docs (platform.claude.com, fetched 17 Nov 2026), Google's and AWS's docs, OpenRouter's site, and AWS re:Post community threads (labelled as such). Two kinds of offering need to be kept apart:
- **Anthropic-operated, billed through a cloud marketplace:** Claude Platform on AWS, Claude Models on Google Cloud (Anthropic managed), and Claude in Microsoft Foundry. Prices are Anthropic list prices, converted to "Claude Consumption Units" at $0.01 each.
- **Partner-operated, where the cloud provider bills:** Amazon Bedrock, and Claude Models on Google Cloud (Google managed), which used to be called "Vertex AI". The cloud provider sets the prices and quotas.

Reference list prices on the Anthropic API ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)), per million tokens (MTok):

| Model | Input | 5m cache write | 1h cache write | Cache read | Output |
|---|---|---|---|---|---|
| Fable 5.2 | $8 | $10 | $16 | $0.20 (0.025x) | $40 |
| Opus 5.5 | $4 | $5 | $8 | $0.20 (0.05x) | $20 |
| Sonnet 5.5 | $2 | $2.50 | $4 | $0.10 (0.05x) | $10 |

Start-tier spend caps and rate limits on the Anthropic API ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits)):
- **Monthly spend caps:** Start $500, Build $1,000, Scale $200,000. The Custom tier has no cap.
- **Start-tier rate limits:**
  - Fable 5.2: 4,000 requests per minute (RPM), 500k input tokens per minute (ITPM), 100k output tokens per minute (OTPM).
  - Opus 5.5 and Sonnet 5.5: each 4,000 RPM, 2M ITPM, 400k OTPM.

---

## Claude Platform on AWS: is its spend cap separate from an existing Console org's cap? Can an individual subscribe? How fast does it move tiers?

### Takeaway
Signing up always creates a **new, separate Anthropic organization**, so it gets its own Start-tier cap. That cap is the same $500/month. It moves up only "automatically as [the org builds] a history of paid AWS Marketplace invoices". The self-service tier-increase button does not exist there, and no timetable is published. The docs neither confirm nor rule out individual sign-up. The org-details form asks for an "entity type".

### Cited Findings
- "Signing up for Claude Platform on AWS always provisions a new Anthropic organization tied to your AWS account. There is no in-place conversion: an existing organization, such as a first-party Claude API organization, can't become a Claude Platform on AWS organization." — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- "This organization is separate from any existing organizations your company has with Anthropic"; API keys, workspaces and Console settings don't carry over, and "both can serve traffic in parallel." — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- "Organizations on Claude Platform on AWS are placed on the Start tier." "...can move to a higher usage tier automatically as they build a history of paid AWS Marketplace invoices." — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- Spend caps on Claude Platform on AWS: "The same monthly spend caps apply, and requests stop at the cap in the same way." At the cap, requests fail with HTTP 429 until 00:00 UTC on the 1st of the next month or until the org moves up a tier. Spend is computed at list prices and can lag about 2 hours, and "the overshoot is billed." — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits); [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- "The self-service **Request tier increase** flow in the Claude Console is not available: the **Rate limits** page directs you to your Anthropic account representative instead." To ask for more, contact the account representative or Anthropic support, and include the models, the peak input and output tokens per minute for each, and the cached share. — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws); [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- Requirements: "An active AWS account with a subscription to Claude Platform on AWS". The org setup form asks for "organization name, entity type, country, intended use". It accepts Anthropic's Commercial Terms of Service. — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- Setup steps:
  1. AWS Console → Claude Platform on AWS → Sign up. This "takes a few minutes".
  2. platform.claude.com/partner-signup: owner email, then the org form.
  3. Create a workspace (`wrkspc_...`).
  4. Sign in to the Console via IAM.
  5. Run `aws iam enable-outbound-web-identity-federation` once. "Without this step, every request returns 'Outbound web identity federation is disabled for your account'."
  — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- Models: `claude-fable-5-2`, `claude-opus-5-5` and `claude-sonnet-5-5` are listed, with the same IDs as the Anthropic API. "New models typically launch on Claude Platform on AWS the same day as the first-party Claude API." All AWS commercial regions are supported, and each workspace is bound to one region. — [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws)
- Pricing: token usage is "rated in USD at standard per-model, per-feature rates (same as Model pricing)", then converted to CCUs at $0.01 each. Metering is hourly, invoicing monthly in arrears, and "no prepaid credits". US-only inference (`inference_geo: "us"`) costs 1.1x; global is standard. — [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Feature parity: it is Anthropic-operated with "same-day API parity". Per-workspace rate limit configuration and fast mode are not available on Claude Platform on AWS. — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)

### Inferences
- The new org's cap is separate from the existing Console org's. In theory the two together give about $1,000 a month ($500 on each), but each is still capped at $500.
- Tiers move up on paid AWS invoices, which are monthly in arrears. The first move above Start therefore probably cannot happen before the first invoice is paid, which is at least about one month after sign-up. This is an inference; no timing is published.
- Running two orgs to get two caps may conflict with Anthropic's terms or anti-abuse policy. That was not checked; flag it before recommending.

### Gaps
- The docs do not say whether "entity type" offers "individual". It is unknown whether an individual AWS account holder can complete the org form. The Console's tier request already refuses Gmail; it is unknown whether this flow has the same rule.
- The docs give no number of invoices or months before an automatic tier move.
- Whether a new Claude Platform on AWS org may start in the lower "Evaluation" tier is not stated. The general docs say new orgs "may start in the Evaluation tier" ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits)). The Claude Platform on AWS page says Start tier.

---

## Amazon Bedrock: availability for a new account, default quotas, increases, spend caps, caching, pricing

### Takeaway
AWS bills Bedrock, and Anthropic's monthly spend caps do not apply there. That makes it the most direct way round the $500 cap.

Model availability:
- Opus 5.5 and Sonnet 5.5 are on the `bedrock-mantle` Messages API.
- Fable 5.2 is **only** on `bedrock-runtime` (InvokeModel/Converse), through the `global.` or `us.` inference profiles.
- Access to Opus 5.5, Sonnet 5.5 and Fable 5.2 is gated per model ("check Amazon Bedrock model access"). Older models are "Open".

Quotas and risks:
- The mantle default is 2M input TPM. It can be raised to 5M input / 500k output TPM without Anthropic's approval.
- Bedrock-runtime counts each output token as 10 tokens against quota for Opus 5.5 and Fable 5.2.
- New AWS accounts officially get "reduced quotas". Many community reports show token-per-day quotas stuck at **0** on new accounts until AWS Support verifies the account. That is the main risk to "soon".

### Cited Findings
- **Access**:
  - "Amazon Bedrock sets access criteria for each Claude model individually. Claude Fable 5.1, Claude Fable 5, Claude Opus 4.8, Claude Sonnet 5, Claude Opus 4.7, and Claude Haiku 4.5 are open to all Amazon Bedrock customers. For any other model's criteria, check Amazon Bedrock model access in the AWS console."
  - In the model table, Opus 5.5, Sonnet 5.5 and Haiku 5.5 show Access "See Access". Mythos models are "Active (verification required)".
  — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
- **Endpoints**:
  - "Claude Fable 5.2 and Claude Mythos 5.2 aren't available in Claude in Amazon Bedrock. Amazon Bedrock serves them on the `bedrock-runtime` endpoint only." — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
  - Fable 5.2's base ID is `anthropic.claude-fable-5-2`, with `global` = Yes and `us` = Yes inference profiles (no eu/jp/apac). — [Claude on Amazon Bedrock (bedrock-runtime)](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy)
  - The mantle IDs are `anthropic.claude-opus-5-5` and `anthropic.claude-sonnet-5-5`. The endpoint is `https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages`. — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
  - The legacy page says to get access via "AWS Console > Bedrock > Model Access and request access to Anthropic models". It names no use-case form. — [bedrock-runtime page](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy)
- **Quotas (mantle)**: "Default quota is 2 million input tokens per minute (TPM). You can request up to 5 million input TPM and 500,000 output TPM without additional Anthropic approval. AWS enforces requests-per-minute (RPM) limits on the Bedrock side; contact AWS Support for RPM adjustments." — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
- **Quotas (runtime)**:
  - Bedrock-runtime has TPM and tokens-per-day (TPD) quotas.
  - "The burndown rate for... Claude Opus 5.5, Claude Sonnet 5, Claude Opus 5, Claude Fable 5.1, and Claude Fable 5.2 is **10x for output tokens**."
  - `max_tokens` is reserved against quota at the start of each request.
  - Cache reads don't count.
  - "Models available exclusively on the `bedrock-mantle` endpoint have separate quotas for input and output tokens, so burndown does not apply."
  — [AWS: How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- **New accounts at 0** (community, not official AWS statements):
  - Several AWS re:Post threads report new accounts whose Service Quotas show an applied value of 0 for Claude token-per-day quotas, marked "Not adjustable". Every request returns 429 "Too many tokens per day".
  - The suggested fix is a Support Center "Account and billing" case asking for account verification.
  - One thread reports a support case unassigned for 2 days.
  - Example: L-B29C9321 (Sonnet 4.6 max tokens/day) applied 0, default 4,320,000,000.
  — [re:Post 1](https://repost.aws/questions/QU72KiZDvYS0yLPDQ5scqZ_A/amazon-bedrock-throttlingexception-too-many-tokens-per-day-on-brand-new-account-quota-stuck-at-0); [re:Post 2](https://repost.aws/questions/QUxr781LoTQjuuxJiRwy_YkQ/all-bedrock-token-per-day-quotas-stuck-at-applied-value-0-not-adjustable-on-new-account-support-case-unassigned-for-2-days); [re:Post 3](https://repost.aws/questions/QUtZk1wFTXSFOsuCU-_o2V2g/amazon-bedrock-anthropic-quotas-are-all-0-and-every-request-returns-http-429-too-many-tokens-per-day); [re:Post 4](https://repost.aws/questions/QUmfeTj3cNRJGuelOAsFiFvg/bedrock-anthropic-claude-models-return-429-too-many-tokens-per-day-quasi-zero-quota-request-aws-support-escalation)
- **Spend cap**: Anthropic's spend caps are documented only for the Anthropic API and Claude Platform on AWS. The Bedrock pages mention no monthly spend cap. "Data handling for this offering is governed by Amazon Bedrock." — [Rate limits](https://platform.claude.com/docs/en/api/rate-limits); [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
- **Prompt caching**:
  - Prompt caching (5 minute and 1 hour) is marked "Yes" for Bedrock. — Anthropic `claude-api` skill reference table (shared/platform-availability.md, cached 2026-11-03); consistent with "Prompt caching" listed as supported on the [Bedrock mantle page](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
  - On bedrock-runtime, "Automatic prompt caching (the top-level `cache_control` field)" is **not supported**. Use explicit breakpoints there. — [bedrock-runtime page](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy)
  - Cache reads don't count against Bedrock TPM/TPD. — [AWS burndown](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)
- **Global vs regional pricing**: "Global: dynamic routing across all available regions... No pricing premium." "Regional endpoints carry a 10 percent pricing premium over global endpoints." The global endpoint covers Opus 5.5 and Sonnet 5.5. Fable 5.2 has a `global.` inference profile. — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock); [bedrock-runtime page](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy)
- **Price level**:
  - Anthropic says Bedrock and Google-managed Vertex "have independent regional pricing" and points to AWS's page. — [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
  - AWS's pricing page could not be read (tables render in JavaScript). Only this note came through: "Starting October 7, 2026, cache read pricing for Claude Sonnet 5.5 is reduced by 50% from the prior rate." Batch is "50% lower" than on-demand. — [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)
  - OpenRouter lists Opus 5.5 at $4/$20 via "Amazon Bedrock, Azure, Google Vertex, Claude Platform on AWS, and Anthropic", and $4.40/$22 for Bedrock/Azure/Vertex (likely the regional +10%). That suggests Bedrock global matches Anthropic list price. — [OpenRouter Opus 5.5](https://openrouter.ai/anthropic/claude-opus-5.5)
- **Features missing on Bedrock**: Message Batches, Files API, server-side tools (web search, web fetch, code execution), MCP connector, Managed Agents, server-side `fallbacks`. — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)

### Inferences
- With no Anthropic spend cap, the limits on Bedrock are AWS quotas and the AWS account's own credit standing. A brand-new account with a personal card is likely to start with throttled or zero daily quotas. Expect days, not minutes, before real throughput, unless AWS verification is quick.
- Fable 5.2 on bedrock-runtime burns quota at 10x for output. An agent that writes a lot of output hits TPM/TPD sooner than on mantle. Keep `max_tokens` tight, because it is reserved at request start.
- Pricing at the global endpoint is probably the same as Anthropic list price (supported only by OpenRouter's listing). It is 10% more on regional or US profiles. Confirm on the AWS pricing page or calculator before relying on it.

### Gaps
- The exact access criteria for Opus 5.5, Sonnet 5.5 and Fable 5.2 on Bedrock: whether there is a use-case form, account age or verification. The docs only say to check the console.
- Official default TPD/TPM for Opus 5.5 and Fable 5.2 on bedrock-runtime for a new account. The figures in the AWS General Reference quota table were not fetched.
- Typical approval time for Service Quotas increases. No official figure; anecdotes say 2+ days.
- Bedrock per-token prices for these three models, including 1-hour cache write pricing, could not be read from AWS's JavaScript page.
- Bedrock has no official monthly spend cap. AWS Budgets only alert by default. This is general AWS knowledge, not checked on this pass.

---

## Google Cloud: Vertex AI ("Claude Models on Google Cloud (Google managed)") and the Anthropic-managed alternative

### Takeaway
There are now two Google offerings.

**(a) Google managed (Vertex / "Agent Platform"):**
- It serves Fable 5.2, Opus 5.5 and Sonnet 5.5 under bare IDs.
- Google bills it and sets the quotas.
- Default per-project quotas are large:
  - Opus 5.5 on the global endpoint: 2,000 QPM, 20M input TPM, 2M output TPM.
  - Sonnet 5.5 on the global endpoint: 2,500 QPM, 25M input TPM, 2.5M output TPM.
  - Fable 5.2: "Granted upon model enablement", with no numbers given.
- No Anthropic spend cap is documented.

**(b) Anthropic managed:**
- It creates a separate new Anthropic org, billed via Google Cloud Marketplace.
- Rate limits are Anthropic-managed, and increases go through an account representative or sales.
- The docs don't say whether the $500 Start cap applies; it is probably like Claude Platform on AWS.

### Cited Findings
- **Vertex models**: the model IDs are `claude-fable-5-2`, `claude-opus-5-5` and `claude-sonnet-5-5`. "Claude Models on Google Cloud (Google managed) offers different Claude models in different regions." Check Model Garden. — [Google managed](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed)
- **Vertex setup**:
  - A project that can use Agent Platform.
  - An IAM role with `aiplatform.endpoints.predict` (e.g. `roles/aiplatform.user`).
  - gcloud ADC.
  — [Google managed](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed)
- **Vertex quotas, per project**:

  | Model | Endpoint | QPM | Input TPM (uncached + cache write) | Output TPM |
  |---|---|---|---|---|
  | Sonnet 5.5 | Global | 2,500 | 25,000,000 | 2,500,000 |
  | Sonnet 5.5 | US / EU multi-region | 1,250 | 12,500,000 | 1,250,000 |
  | Opus 5.5 | Global | 2,000 | 20,000,000 | 2,000,000 |
  | Opus 5.5 | US / EU multi-region | 1,000 | 10,000,000 | 1,000,000 |

  - Fable 5.2: "Granted upon model enablement", under shared lineage quotas, no numbers given.
  - "Quotas on the global endpoint and each multi-region endpoint are independent buckets."
  - "The maximum quotas might vary by account and, in some cases, access might be restricted."
  - Increases are requested in the Google Cloud console.
  — [Google: Quotas for Anthropic Claude models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/quotas)
- **Vertex pricing**: "Multi-region and regional endpoints cost 10 percent more than global endpoints." Regional endpoints serve only Sonnet 4.6 and earlier, so the new models need global or multi-region. Google invoices; prices are on Google's generative AI pricing page. — [Google managed](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed)
- **Vertex features**:
  - Supported: prompt caching, the web search tool (basic version only), fast mode.
  - Not supported: Batches, Files API, code execution, web fetch, Managed Agents, server-side fallback.
  — [Google managed](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed)
  - 1-hour caching is marked "Yes" for Vertex in the skill's reference table. — Anthropic `claude-api` skill reference (shared/platform-availability.md, cached 2026-11-03)
- **Anthropic managed on Google Cloud**:
  - "These steps create a new Anthropic organization for your Google Cloud project. It's separate from any Anthropic organization your company already has."
  - Setup: subscribe in Google Cloud Marketplace, enable services, create the org.
  - "Anthropic manages rate limits for Claude Models on Google Cloud (Anthropic managed). To request higher limits, contact your Anthropic account representative or contact sales."
  - Billing: "One Claude Consumption Unit is $0.01 USD", and the charges appear on the Google Cloud bill.
  — [Anthropic managed](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/anthropic-managed)
  - US-only inference has the same 1.1x multiplier as the Anthropic API. — [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Inferences
- For a quick increase in throughput with no Anthropic cap, Vertex (Google managed) looks the strongest on paper. Its default quotas for Opus 5.5 and Sonnet 5.5 are 10x the Anthropic Start tier's ITPM. But "might vary by account... access might be restricted" probably bites on new, free-trial or low-history billing accounts.
- The Anthropic-managed Google offering probably carries the same tier and spend-cap system as Claude Platform on AWS, because it is an Anthropic org. Its docs don't say so explicitly.

### Gaps
- Whether a new Google Cloud project, especially a free-trial billing account, gets the default Claude quotas or zero. It is also unknown whether partner models need a paid (upgraded) billing account.
- Google's per-token prices for Fable 5.2, Opus 5.5 and Sonnet 5.5 on Vertex, including 1-hour cache write. Google's pricing page was not fetched.
- Whether the Anthropic-managed Google offering starts on the Start tier with the $500 cap.
- Typical turnaround for Vertex quota increases.

---

## Microsoft Foundry

### Takeaway
Foundry is Anthropic-operated and billed through the Azure Marketplace at **Anthropic list price** (CCUs at $0.01). Models:
- Fable 5.2 only as "Hosted on Anthropic" deployments.
- Opus 5.5 and Sonnet 5.5 in both hosting options.

Rate limits are managed through Azure, and no Anthropic spend cap is documented. The prerequisite is just "an active Azure subscription". The rules on which subscription types qualify were not found.

### Cited Findings
- "When you access Claude in Microsoft Foundry, you pay for Claude usage through the Azure Marketplace... You manage costs through your Azure subscription." — [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry)
- **Models**:
  - Fable 5.2: Hosted on Anthropic only.
  - Opus 5.5 and Sonnet 5.5: Hosted on Azure and Hosted on Anthropic.
  - Deployment names default to the model IDs.
  — [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry)
- **Prerequisites**:
  - "An active Azure subscription".
  - Access to the Foundry portal.
  - A Foundry User or Cognitive Services User role.
  - On the first Claude deployment, you accept the Azure Marketplace terms and subscribe to the offer.
  — [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry)
- **Pricing**: "Anthropic rates your token usage in USD at standard per-model, per-feature rates... converts the result to CCUs at $0.01 USD per CCU." It is postpaid, "no prepaid credits". The US Data Zone deployment type costs 1.1x. — [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- **Rate limits**: on a 429, "Consider requesting rate limit increases through the Azure portal or Azure support." Foundry does not return Anthropic's rate-limit headers. — [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry)
- **Features not supported**: Batches, Models API, Admin API, advisor tool, Managed Agents, server-side fallback. Hosted on Azure also lacks code execution, Files API and newer web tools. — [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry)

### Inferences
- Foundry looks like another route at list price without the Anthropic tier cap, with limits set in Azure. Prices match the Anthropic API exactly (global deployments).

### Gaps
- Default Foundry quotas for Claude deployments, in TPM and RPM.
- Whether Azure free-trial, student or sponsored subscriptions can buy Marketplace offers such as Claude. Commonly they cannot, but that was not checked.
- Whether "Hosted on Anthropic" deployments are subject to any Anthropic-side spend tier.
- Setup time.

---

## Third-party routers (OpenRouter etc.)

### Takeaway
OpenRouter serves Opus 5.5, and very likely Fable 5.2 and Sonnet 5.5, routing to Anthropic, Claude Platform on AWS, Bedrock, Vertex and Azure:
- Model prices are passed through **without markup**.
- There is a **5.5% fee when buying credits** ($0.80 minimum; 5% for crypto).
- Routing to Anthropic happens through OpenRouter's own provider accounts, so the user's $500 cap does not apply.

The trust trade-offs: a third party sees all prompts, which may matter for a trading bot's strategy prompts. Caching behaviour across rotating providers also matters.

### Cited Findings
- OpenRouter's Opus 5.5 page lists providers "Amazon Bedrock, Azure, Google Vertex, Claude Platform on AWS, and Anthropic". It shows $4/$20 per MTok and a $4.40/$22 tier for Bedrock/Azure/Vertex. An $8/$40 figure is listed for Anthropic, probably the fast-mode variant; this was unclear in the fetch. "Pay-as-you-go with no markup on the provider's price." — [OpenRouter: Claude Opus 5.5](https://openrouter.ai/anthropic/claude-opus-5.5)
- "OpenRouter charges a 5.5% ($0.80 minimum) fee when you purchase credits." "Crypto payments are charged a fee of 5%." — [OpenRouter FAQ](https://openrouter.ai/docs/faq)
- BYOK (bring your own key): pay-as-you-go accounts get $25,000 a month of list-price inference with no BYOK fee, then 5%. — [OpenRouter FAQ](https://openrouter.ai/docs/faq)
- Data: OpenRouter says it does "zero logging of your prompts/completions" by default. Opting in to logging gives a 1% discount. Retention durations are not stated in the FAQ. — [OpenRouter FAQ](https://openrouter.ai/docs/faq)
- No rate-limit numbers for paid accounts appear in the FAQ or on the model page; a 429 means "retry with backoff". — [OpenRouter FAQ](https://openrouter.ai/docs/faq); [OpenRouter model page](https://openrouter.ai/anthropic/claude-opus-5.5)

### Inferences
- The effective cost is about 5.5% over list price, because of the credit-purchase fee. It is still the fastest unblock: card payment, minutes to set up, no cap from the user's Anthropic org.
- BYOK with the user's own Anthropic key would **not** escape the $500 cap, because it would bill the same org. BYOK with their own Bedrock or Vertex credentials just passes through those platforms' quotas.
- Some Anthropic-specific features may not pass through a router. Examples: server-side `fallbacks`, the beta headers this repo's agents use, 1-hour cache TTL control, and preserved thinking across turns when the provider changes. Prompt-cache hit rates can drop if requests are spread across providers. Not checked; test before relying on it.

### Gaps
- OpenRouter pages for Fable 5.2 and Sonnet 5.5 were not fetched. Their availability there is inferred, not confirmed.
- OpenRouter's rate limits on paid accounts for these models.
- Whether OpenRouter keeps prompt caching (including 1-hour TTL) and Anthropic beta features end to end.
- Other routers (e.g. Requesty, Portkey, LiteLLM-hosted) were not researched.

---

## Cross-platform summary (for the report writer)

| Route | Fable 5.2 | Opus 5.5 / Sonnet 5.5 | Anthropic $ cap? | Default throughput | Price vs list | Time to first call (est.) |
|---|---|---|---|---|---|---|
| Claude Platform on AWS | Yes | Yes | Yes: new separate org, Start $500 | Start tier (Opus/Sonnet 2M ITPM) | Same (CCU) | Minutes to an hour; possible "entity type" hurdle |
| Bedrock (mantle / runtime) | runtime only (global, us) | mantle | No | mantle 2M ITPM, up to 5M/500k OTPM without Anthropic approval; new accounts may start at 0 TPD | Global probably list price; regional +10% (unverified on AWS page) | Hours to days (model access + possible verification) |
| Vertex (Google managed) | Yes (quota "granted upon enablement") | Yes | No | Opus 5.5 global 20M ITPM / 2M OTPM; Sonnet 5.5 25M / 2.5M | Google-set; multi-region +10%; numbers not retrieved | Probably hours; access "might be restricted" |
| Google Cloud (Anthropic managed) | Yes (Anthropic models) | Yes | Probably (new org; not stated) | Anthropic-managed; raise via representative or sales | Same (CCU) | Hours |
| Microsoft Foundry | Hosted on Anthropic only | Yes | Not documented | Azure-managed; numbers not found | Same (CCU); US Data Zone 1.1x | Hours |
| OpenRouter | Probably | Yes | No | Not published | +5.5% credit fee | Minutes |

(Times are estimates, not sourced; see each section's Gaps.)
