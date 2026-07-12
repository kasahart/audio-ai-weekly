# Audio Research Weekly — Automated Update System

This system automatically collects and analyzes papers from the arXiv `cs.SD` and `eess.AS` categories every Friday and publishes the results on GitHub Pages.

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
python scripts/test_connection.py        # Test the GitHub Models connection
python scripts/fetch_papers.py --dry-run # Test arXiv retrieval
```

### 4. Run manually

Open the **Actions** tab, select **Weekly arXiv Update**, and choose **Run workflow**.

## Adding or Removing Keywords

Edit the `include` list in `config/keywords.yaml`. No code changes are required.

## Repository Structure

```text
.devcontainer/       # DevContainer configuration
.github/workflows/   # GitHub Actions workflows
config/
  keywords.yaml      # Editable filtering keywords
  settings.yaml      # System settings
data/
  index.json         # Index of all published weeks
  latest.json        # Latest weekly data
  weekly/            # Weekly JSON files (YYYY-MMDD.json)
scripts/
  fetch_papers.py    # Retrieve papers from arXiv
  analyze_papers.py  # Analyze papers with GitHub Models
  build_data.py      # Generate data and update the index
  test_connection.py # Test connectivity
web/                 # React frontend
```
