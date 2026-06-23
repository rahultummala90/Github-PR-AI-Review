# AI PR Reviewer

A GitHub Actions workflow that reviews every pull request using the GitHub Models API and posts inline comments directly on the changed lines.

## What it does

On every PR open, update, or reopen:

1. Fetches the diff for all changed files
2. Sends each file's diff to `gpt-4o` via GitHub Models API
3. Parses the response for security issues, missing tests, and code quality problems
4. Posts inline comments on the exact lines with issues

## Comment format

- 🔴 **Security** — SQL injection, hardcoded secrets, missing auth, XSS, etc.
- 🟡 **Missing Tests** — untested new logic, missing edge cases
- 🔵 **Code Quality** — dead code, unclear naming, missing error handling

## Setup

### 1. Add the files to your repo

```
.github/
  workflows/
    pr-review.yml
  scripts/
    review.py
```

### 2. Add the secret

Go to **Settings → Secrets and variables → Actions** and add:

| Secret            | Value                                                                                                                      |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `GH_MODELS_TOKEN` | A GitHub personal access token with `models:inference` scope (or use `GITHUB_TOKEN` if your org has GitHub Models enabled) |

### 3. Open a PR

The workflow triggers automatically. Check the **Files changed** tab — comments appear inline on the diff.

## Configuration

Edit the top of `review.py` to change:

```python
MODEL = "gpt-4o"          # any GitHub Models model
MAX_DIFF_CHARS = 12_000   # per-file diff size cap
MAX_FILES = 20            # max files reviewed per PR
```

To skip additional file types, add extensions to `SKIP_EXTENSIONS`.

## How it handles large PRs

- Skips binary files, lockfiles, and minified assets automatically
- Truncates diffs over `MAX_DIFF_CHARS` per file
- Caps at `MAX_FILES` files per PR
- If no issues are found, no review is posted (no noise)

## Switching models

GitHub Models supports multiple models. Change `MODEL` in `review.py`:

```python
MODEL = "gpt-4o"           # default
MODEL = "gpt-4o-mini"      # faster, cheaper
MODEL = "Phi-4"            # Microsoft open model
MODEL = "Meta-Llama-3.1-70B-Instruct"  # Meta
```

## Local testing

```bash
export GITHUB_TOKEN=ghp_...
export GH_MODELS_TOKEN=ghp_...
export REPO=myorg/myrepo
export PR_NUMBER=42
export HEAD_SHA=abc123

pip install requests
python .github/scripts/review.py
```
