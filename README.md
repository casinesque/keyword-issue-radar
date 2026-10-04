# keyword-issue-radar

## Idea

This project started from a recurring problem: finding good open source issues to contribute to is often harder than expected. Issues on favorite projects may already be fixed, closed, inactive, or linked to an existing pull request.

I was also curious about GitHub APIs and possible integrations, so I started building a tool with the goal of finding open GitHub issues that may be good contribution opportunities for specific keywords, while avoiding issues already seen and issues that already have a linked pull request.

## Problem

Manually searching for useful GitHub issues takes time. Many issues are old, already being worked on, unclear, or not suitable for a first contribution.

This script automates a first selection pass by returning filtered and sorted issues.

## Target User

A developer who wants to find open source issues to contribute to, especially around topics such as Docker, Kubernetes, Terraform, Helm, CI, GitHub Actions, or build workflows.

## Input

The script accepts keywords from the command line.

Example:

```bash
python src/core.py helm --no-cache
```

The maximum number of keyword parameters is 6 and this cannot be changed.

If no keywords are provided, the script performs a global search over open issues.

## Output

The script prints an ordered list of issues with:

- calculated score
- repository star count
- issue URL
- title
- repository
- score reasons

## APIs Used

### GitHub REST Search API

Used to search for open issues.

Endpoint:

```text
https://api.github.com/search/issues
```

Main query:

```text
is:issue is:open -linked:pr created:>YYYY-MM-DD
```

With keywords:

```text
is:issue is:open -linked:pr created:>YYYY-MM-DD (keyword1 OR keyword2 OR keyword3)
```

The `-linked:pr` filter excludes issues that already have a linked pull request.

### GitHub GraphQL API

Used to fetch repository star counts.

Endpoint:

```text
https://api.github.com/graphql
```

Field used:

```text
stargazerCount
```

## Authentication

To avoid rate limiting, a GitHub token must be provided with a simple read scope and configured as an environment variable before executing the program.

```python
TOKEN = os.environ["GH_TOKEN"]
```

The token must not be written in the source code.

## Cache

By default, cache files are stored under:

```text
~/.cache/keyword-issue-radar
```

This folder can be changed with:

```text
KEYWORD_ISSUE_RADAR_CACHE_DIR
```

### Already Seen Issues Cache

Used to avoid showing the same issues repeatedly.

Default file:

```text
~/.cache/keyword-issue-radar/seen_issues.json
```

Variable to change the file path:

```text
GH_SEEN_ISSUES_FILE
```

### Repository Stars Cache

Used to avoid requesting star counts for the same repositories on every run.

Default file:

```text
~/.cache/keyword-issue-radar/repo_stars.json
```

Variable to change the file path:

```text
GH_STARS_CACHE_FILE
```

Cache duration:

```text
3 days
```

After 3 days, the cached value is considered stale and is requested from GitHub again.

## Scoring

The score measures whether an issue looks like a good contribution opportunity.

Score components:

- repository popularity
- useful labels, especially those suggested for newcomers
- issue freshness
- number of comments

### Repository Popularity

Stars are compressed with a logarithmic scale, so very large repositories do not dominate the ranking.

Example:

```python
def repo_score(stars):
    return min(40, int(math.log10(stars + 1) * 10))
```

### Useful Labels

Positive labels:

- good first issue
- first timers only
- beginner
- starter
- easy
- low hanging fruit
- help wanted
- contributions welcome
- up for grabs

Variants with spaces, hyphens, and underscores are handled through regex.

Example:

```text
good first issue
good-first-issue
good_first_issue
```

### Freshness

Recently updated issues receive more points.

Rules:

- updated in the last 7 days: 30 points
- updated in the last 30 days: 20 points
- updated in the last 90 days: 10 points
- older than that: 0 points

### Comments

Issues with fewer comments are considered easier to evaluate.

Rules:

- 0 comments: 20 points
- 1-5 comments: 15 points
- 6-15 comments: 5 points
- more than 15 comments: -10 points

## Sorting

Results are sorted by final score, from highest to lowest.

If two issues have a similar score, repository stars can be used as a secondary criterion.

## Main Filters

The script filters out:

- closed issues
- pull requests
- issues with a linked pull request
- issues created before the cutoff date
- issues already seen, unless `--no-cache` is used
- issues below the minimum score threshold

## Options

### Disable Already Seen Issues Cache

```bash
python src/core.py docker --no-cache
```

Shows issues already seen in previous runs.

## Environment Variables

### Required

```text
GH_TOKEN
```

### Optional

```text
SEEN_CACHE_MAX
GH_SEEN_ISSUES_FILE
GH_STARS_CACHE_FILE
KEYWORD_ISSUE_RADAR_CACHE_DIR
MAX_ALLOWED_DAYS
```

### Variable Reference

`SEEN_CACHE_MAX` controls how many already seen issue URLs are stored in the seen issues cache. Default: `5000`.

`GH_SEEN_ISSUES_FILE` overrides the path of the already seen issues cache file.

`GH_STARS_CACHE_FILE` overrides the path of the repository stars cache file.

`KEYWORD_ISSUE_RADAR_CACHE_DIR` overrides the default cache directory.

`MAX_ALLOWED_DAYS` controls how far back the issue search goes. Default: `365`.