# Audio AI Weekly / 音響AI週報 — Automated Update System

This system automatically collects and analyzes papers from the arXiv `cs.SD` and `eess.AS` categories every Friday and publishes the results on GitHub Pages.

Published site: https://kasahart.github.io/audio-ai-weekly/

Official acknowledgement required by the [arXiv brand guidelines](https://info.arxiv.org/brand/brand-guidelines.html):

> Thank you to arXiv for use of its open access interoperability. This service was not reviewed or approved by, nor does it necessarily express or reflect the policies or opinions of, arXiv.

Weekly summaries are **AI-generated from titles and abstracts · not human-reviewed**
and may contain mistranslations, inaccurate summaries, or overgeneralizations.
Consult the original paper for research decisions. Long-form features are
**AI-generated from titles and abstracts · machine-checked for source and
translation consistency · not human-reviewed**; those automated checks are not
human fact-checking.

## Display language and data schema

The web UI supports Japanese and English. `?lang=ja|en` takes precedence over the
saved `arxiv-language` value, which in turn takes precedence over the browser
language. Changing the header's `JA / EN` control updates both the URL and saved
preference without resetting other filters.

Category objects contain `label` (Japanese) and `labelEn`. Paper analysis keeps
the Japanese fields and adds `taskEn`, `whatEn`, `novelEn`, `methodEn`,
`validationEn`, and `discussionEn`; the original `title` and `abstract` supply
English title and abstract copy. Weekly data contains `trend` and `trendEn`.

## Twice-monthly deep-dive features

In addition to the Friday paper feed, the site can publish two automated long-form
features each month:

- the second Tuesday: a field primer explaining why a research direction exists,
  how it developed, and where its limits are;
- the fourth Tuesday: a debate brief comparing competing approaches, evaluation
  assumptions, and implications for researchers and practitioners.

Each feature publishes matched full-length English and Japanese editions. The
generator first writes an 800–1,100 word canonical English article, verifies its
grounding, and only then translates the fixed section, block, and citation structure
into a Japanese 8–12 minute read. A separate bilingual review rejects material
omissions, additions, or changed qualifications. Topic selection starts from the
recent weekly archive, then retrieves additional arXiv primary sources for
historical context. The generator uses only the supplied paper metadata and
abstracts for factual claims. Validated HTTPS code and project URLs already present
in weekly metadata are shown as metadata-linked reading resources; they are not
treated as independent evidence or endorsed as official by the publisher.

Publication is fail-closed. A feature must pass structural checks for article
length and reading time, Japanese/English language checks, source coverage, one
metadata-linked resource, three distinct perspectives, and block-level citations,
followed by separate grounding and translation-fidelity reviews. Bounded correction
attempts are allowed; if either review still fails, that publication slot is
skipped. Generated feature JSON is stored under `data/features/`, and
`scripts/render_features.py` turns it into shareable Japanese and English static
pages under `/features/<slug>/` and `/features/<slug>/en/`.

## Research Areas

- Audio foundation models
- Source separation
- Anomalous sound detection

## Setup

### 1. Configure the repository

Enable GitHub Pages for the repository.

### 2. Start the development environment with DevContainer

Open the repository in VS Code and select **Reopen in Container**.

### 3. Verify the setup

```bash
python scripts/test_connection.py        # Test the configured AI connection
python scripts/fetch_papers.py --dry-run # Test arXiv retrieval
```

### 4. Run manually

Open the **Actions** tab, select **Weekly Audio AI Update**, and choose **Run workflow**.

For a deep-dive preview, first restore published weekly JSON into `data/`, then run:

```bash
python scripts/generate_feature.py --date 2026-07-14 --article-type primer --dry-run
```

The **Publish Deep-Dive Features** workflow supports a target date, an automatic
or explicit article type, dry-run mode, and an explicit same-slot replacement mode.
Its scheduled run checks every Tuesday at 03:00 UTC (12:00 JST) and publishes only
in the second and fourth Tuesday slots.

## arXiv Request Identity

arXiv requests explicitly accept Atom/XML and default to the generic
`audio-ai-weekly/1.0` User-Agent. Optionally set `ARXIV_USER_AGENT` to override
that identifier and `ARXIV_CONTACT` to append an operator contact URL or email.
Empty values fall back to the configured User-Agent and omit the contact.

For local runs, supply these as environment variables. To use them in GitHub
Actions, add repository **Secrets** under **Settings → Secrets and variables →
Actions**, then map them into the environment of the fetch/analysis steps (or
workflow). Creating secrets alone does not expose them to the Python process:

```yaml
env:
  ARXIV_USER_AGENT: ${{ secrets.ARXIV_USER_AGENT }}
  ARXIV_CONTACT: ${{ secrets.ARXIV_CONTACT }}
```

Do not put personal contact details in tracked configuration, workflow files, or
documentation. These values are sent to arXiv in the User-Agent header; the fetcher
does not log the headers.

Category searches, related-paper ID lookups, feature-source searches, and metadata
enrichment share an explicit default TLS
context with ALPN set to `http/1.1` and post-handshake client authentication
disabled. This handshake succeeded in local tests where arXiv returned empty
HTTP 406 responses to the implicit urllib context. Server certificate and
hostname verification remain enabled; this does not bypass TLS verification.
It is a compatibility workaround, not a guarantee against arXiv outages or
rate limits. Fetch failures still stop the run rather than publish empty data.

## AI Provider

Select one provider for all AI processing in `config/settings.yaml`:

```yaml
ai:
  provider: gemini
```

For local runs, export the API key used by the selected provider:

```bash
export GEMINI_API_KEY="..."   # gemini
```

GitHub Models [was retired on July 30, 2026](https://docs.github.com/en/github-models),
including its inference API. Its former endpoint is rejected before sending credentials.
The default fallback list is empty. If Gemini fails, publication stops safely.
An alternative provider requires its own credentials.

The repository currently selects Gemini. Gemini uses its official
[OpenAI-compatible endpoint](https://ai.google.dev/gemini-api/docs/openai)
with [`gemini-3.5-flash`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash);
the existing `openai` SDK is used for the compatible API. When selecting Gemini in GitHub
Actions, add `GEMINI_API_KEY` under **Settings → Secrets and variables → Actions
→ New repository secret**. `GITHUB_TOKEN` remains in use for deployment.

## Adding or Removing Keywords

Edit the `include` list in `config/keywords.yaml`. No code changes are required.

## Repository Structure

```text
.devcontainer/       # DevContainer configuration
.github/workflows/   # GitHub Actions workflows
config/
  keywords.yaml      # Editable filtering keywords
  prompts/           # Editable AI prompt templates
  settings.yaml      # System settings
data/
  index.json         # Index of all published weeks
  weekly/            # Weekly JSON files (YYYY-MMDD.json)
  features/          # Feature index and grounded long-form article JSON
scripts/
  fetch_papers.py    # Retrieve papers from arXiv
  analyze_papers.py  # Analyze papers with the configured AI provider
  build_data.py      # Generate data and update the index
  generate_feature.py # Select, research, generate, and validate a feature
  render_features.py  # Render feature JSON as static HTML pages
  test_connection.py # Test connectivity
web/                 # React frontend
```

## License

This project is licensed under the [MIT License](LICENSE).
