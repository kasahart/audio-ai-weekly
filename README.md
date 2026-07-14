# Audio Research Weekly — Automated Update System

This system automatically collects and analyzes papers from the arXiv `cs.SD` and `eess.AS` categories every Friday and publishes the results on GitHub Pages.

## Display language and data schema

The web UI supports Japanese and English. `?lang=ja|en` takes precedence over the
saved `arxiv-language` value, which in turn takes precedence over the browser
language. Changing the header's `JA / EN` control updates both the URL and saved
preference without resetting other filters.

Category objects contain `label` (Japanese) and `labelEn`. Paper analysis keeps
the Japanese fields and adds `taskEn`, `whatEn`, `novelEn`, `methodEn`,
`validationEn`, and `discussionEn`; the original `title` and `abstract` supply
English title and abstract copy. Weekly data contains `trend` and `trendEn`.

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

Open the **Actions** tab, select **Weekly arXiv Update**, and choose **Run workflow**.

## AI Provider

Select one provider for all AI processing in `config/settings.yaml`:

```yaml
ai:
  provider: github_models  # or gemini
```

For local runs, export the API key used by the selected provider:

```bash
export GITHUB_TOKEN="..."     # github_models
export GEMINI_API_KEY="..."   # gemini
```

GitHub Models is the default and keeps the existing behavior. Gemini uses its
official [OpenAI-compatible endpoint](https://ai.google.dev/gemini-api/docs/openai)
with [`gemini-3.5-flash`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash);
the existing `openai` SDK is used for both providers. When selecting Gemini in GitHub
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
scripts/
  fetch_papers.py    # Retrieve papers from arXiv
  analyze_papers.py  # Analyze papers with the configured AI provider
  build_data.py      # Generate data and update the index
  test_connection.py # Test connectivity
web/                 # React frontend
```
