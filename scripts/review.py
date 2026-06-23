"""
AI PR Reviewer
Fetches the PR diff, sends it to GitHub Models API,
and posts inline review comments back on the PR.
"""

import os
import re
import sys
import json
import requests

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_TOKEN    = os.environ["GITHUB_TOKEN"]
GH_MODELS_TOKEN = os.environ.get("GH_MODELS_TOKEN", GITHUB_TOKEN)
REPO            = os.environ["REPO"]          # e.g. "myorg/myrepo"
PR_NUMBER       = os.environ["PR_NUMBER"]
HEAD_SHA        = os.environ["HEAD_SHA"]

GITHUB_API      = "https://api.github.com"
MODELS_API      = "https://models.inference.ai.azure.com"
MODEL           = "gpt-4o"                    # swap to any GitHub Models model

GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Files to skip — binaries, lockfiles, generated code
SKIP_EXTENSIONS = {
    ".lock", ".sum", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
    ".min.js", ".min.css", ".map",
}

MAX_DIFF_CHARS = 12_000   # per-file cap before truncation
MAX_FILES      = 20       # max files reviewed per PR


# ── GitHub helpers ────────────────────────────────────────────────────────────

def get_pr_files() -> list[dict]:
    """Return list of changed files with their diffs."""
    url = f"{GITHUB_API}/repos/{REPO}/pulls/{PR_NUMBER}/files"
    resp = requests.get(url, headers=GH_HEADERS)
    resp.raise_for_status()
    return resp.json()


def post_review(comments: list[dict]) -> None:
    """Post a PR review with inline comments."""
    url = f"{GITHUB_API}/repos/{REPO}/pulls/{PR_NUMBER}/reviews"
    body = {
        "commit_id": HEAD_SHA,
        "event": "COMMENT",
        "body": "🤖 **AI PR Review** — automated review by GitHub Models (`gpt-4o`).",
        "comments": comments,
    }
    resp = requests.post(url, headers=GH_HEADERS, json=body)
    if resp.status_code not in (200, 201):
        print(f"Failed to post review: {resp.status_code} {resp.text}")
        sys.exit(1)
    print(f"Posted review with {len(comments)} comment(s).")


# ── Diff parsing ──────────────────────────────────────────────────────────────

def should_skip(filename: str) -> bool:
    for ext in SKIP_EXTENSIONS:
        if filename.endswith(ext):
            return True
    return False


def parse_hunk_positions(patch: str) -> dict[int, int]:
    """
    Map diff line numbers (1-based position in the patch) to actual
    file line numbers. GitHub's review API uses patch positions, not
    file line numbers, for inline comments.

    Returns: {file_line_number: patch_position}
    """
    positions: dict[int, int] = {}
    patch_position = 0
    current_line = 0

    for raw_line in patch.splitlines():
        patch_position += 1
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if hunk:
            current_line = int(hunk.group(1)) - 1
            continue
        if raw_line.startswith("-"):
            continue                      # deleted line — no file line number
        current_line += 1
        positions[current_line] = patch_position

    return positions


# ── GitHub Models API ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a senior software engineer performing a pull request code review.
Review the diff provided and identify issues in three categories:

1. SECURITY — SQL injection, hardcoded secrets, missing auth checks,
   unsafe deserialization, XSS vectors, insecure dependencies, etc.
2. TESTS — missing unit tests for new logic, untested edge cases,
   missing error path coverage.
3. QUALITY — dead code, unclear naming, overly complex logic,
   missing error handling, performance concerns, code duplication.

Respond ONLY with a JSON array. Each element must have exactly these fields:
  - "line": integer — the line number in the NEW file where the issue is
  - "severity": "critical" | "warning" | "suggestion"
  - "category": "security" | "tests" | "quality"
  - "comment": string — concise explanation and recommended fix (max 3 sentences)

Only flag real issues. If the diff looks clean, return an empty array: []
Do not include markdown fences, preamble, or any text outside the JSON array.\
"""


def review_file(filename: str, patch: str) -> list[dict]:
    """Send one file's diff to GitHub Models and return parsed issues."""
    if len(patch) > MAX_DIFF_CHARS:
        patch = patch[:MAX_DIFF_CHARS] + "\n... (truncated)"

    user_message = f"File: `{filename}`\n\n```diff\n{patch}\n```"

    headers = {
        "Authorization": f"Bearer {GH_MODELS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }

    resp = requests.post(
        f"{MODELS_API}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        print(f"  Models API error {resp.status_code}: {resp.text}")
        return []

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        issues = json.loads(raw)
        return issues if isinstance(issues, list) else []
    except json.JSONDecodeError:
        print(f"  Could not parse model response for {filename}:\n{raw[:300]}")
        return []


# ── Severity icons ────────────────────────────────────────────────────────────

ICONS = {
    "critical":   "🔴",
    "warning":    "🟡",
    "suggestion": "🔵",
}

CATEGORY_LABELS = {
    "security": "**Security**",
    "tests":    "**Missing Tests**",
    "quality":  "**Code Quality**",
}


def format_comment(issue: dict) -> str:
    icon     = ICONS.get(issue["severity"], "⚪")
    category = CATEGORY_LABELS.get(issue["category"], issue["category"])
    return f"{icon} {category}: {issue['comment']}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    files = get_pr_files()
    print(f"PR has {len(files)} changed file(s).")

    reviewable = [
        f for f in files
        if f.get("patch")
        and f["status"] != "removed"
        and not should_skip(f["filename"])
    ][:MAX_FILES]

    print(f"Reviewing {len(reviewable)} file(s) (skipped binaries/lockfiles).")

    all_comments: list[dict] = []

    for file in reviewable:
        filename = file["filename"]
        patch    = file["patch"]
        print(f"  → {filename}")

        issues = review_file(filename, patch)
        if not issues:
            print("     no issues found.")
            continue

        positions = parse_hunk_positions(patch)

        for issue in issues:
            line = issue.get("line")
            if not isinstance(line, int):
                continue

            position = positions.get(line)
            if position is None:
                # Fall back to last position in the patch
                position = max(positions.values()) if positions else 1

            all_comments.append({
                "path":     filename,
                "position": position,
                "body":     format_comment(issue),
            })
            print(f"     [{issue['severity']}] line {line}: {issue['category']}")

    if not all_comments:
        print("No issues found. PR looks clean — no review posted.")
        return

    post_review(all_comments)


if __name__ == "__main__":
    main()