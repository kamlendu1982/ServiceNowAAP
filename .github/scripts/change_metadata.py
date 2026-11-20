#!/usr/bin/env python3
"""Validate the change details a pull request must carry, and build the AAP extra vars.

Every pull request to main must have these four lines in its description (the
description becomes the squash commit message on main):

    ServiceNow: CHG0034567
    Jira: NETOPS-123
    Feature: bgp
    Description: Add BGP peer to Azure East US VPN gateway on edge-rtr01

Subcommands
  validate-pr       Check the pull request in GITHUB_EVENT_PATH. Fails the job with a
                    clear message, and with --comment keeps one PR comment up to date.
  validate-message  Check a plain commit message from a file (local use, tests).
  merged            On a merged pull request: check the details again, check approvals,
                    and write the AAP extra vars to --output.

Only the Python standard library is used. PR text is read from the event file, never
interpolated into shell, so it cannot inject commands into the workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIELDS = ("servicenow", "jira", "feature", "description")
COMMENT_MARKER = "<!-- gitops-change-metadata -->"
LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z -]*?)\s*:\s*(.*?)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE_RE = re.compile(r"^\s*(```|~~~)")


# --------------------------------------------------------------------------- parsing


@dataclass
class Result:
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_policy(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _visible_lines(message: str) -> list[str]:
    """Lines a reader would see: no HTML comments, nothing inside code fences."""
    text = HTML_COMMENT_RE.sub("", message.replace("\r\n", "\n"))
    lines, in_fence = [], False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def parse_message(message: str, policy: dict[str, Any]) -> Result:
    """Find the four 'Key: value' lines anywhere in the message and validate them."""
    alias_to_field = {
        alias.lower(): name for name in FIELDS for alias in policy[name]["aliases"]
    }
    raw: dict[str, str] = {}
    result = Result()

    for line in _visible_lines(message or ""):
        match = LINE_RE.match(line)
        if not match:
            continue
        name = alias_to_field.get(match.group(1).strip().lower())
        if not name:
            continue
        if name in raw:
            result.errors.append(
                f"**{policy[name]['label']}** appears more than once. Keep a single "
                f"`{policy[name]['label']}:` line."
            )
            continue
        raw[name] = match.group(2)

    for name in FIELDS:
        label, example = policy[name]["label"], policy[name]["example"]
        if name not in raw:
            result.errors.append(
                f"**{label}** is missing. Add a line like `{label}: {example}`."
            )
        elif not raw[name]:
            result.errors.append(
                f"**{label}** is empty. Fill it in, for example `{label}: {example}`."
            )

    if "servicenow" in raw and raw["servicenow"]:
        value = raw["servicenow"].upper()
        if re.fullmatch(policy["servicenow"]["pattern"], value):
            result.values["servicenow"] = value
        else:
            result.errors.append(
                f"**ServiceNow** `{raw['servicenow']}` is not a valid record number. "
                f"Use the full number, for example `{policy['servicenow']['example']}`."
            )

    if "jira" in raw and raw["jira"]:
        value = raw["jira"].upper()
        projects = [p.upper() for p in policy["jira"].get("projects") or []]
        if not re.fullmatch(policy["jira"]["pattern"], value):
            result.errors.append(
                f"**Jira** `{raw['jira']}` is not a valid issue key. "
                f"Use the project key and number, for example `{policy['jira']['example']}`."
            )
        elif projects and value.split("-")[0] not in projects:
            result.errors.append(
                f"**Jira** `{value}` is not in an allowed project. "
                f"Allowed projects: {', '.join(f'`{p}`' for p in projects)}."
            )
        else:
            result.values["jira"] = value

    if "feature" in raw and raw["feature"]:
        allowed = policy["feature"]["allowed"]
        features: list[str] = []
        for item in raw["feature"].split(","):
            item = item.strip().lower().replace("-", "_").replace(" ", "_")
            if item and item not in features:
                features.append(item)
        unknown = [f for f in features if f not in allowed]
        if unknown:
            result.errors.append(
                f"**Feature** {', '.join(f'`{u}`' for u in unknown)} is not a known feature. "
                f"Use one of: {', '.join(f'`{a}`' for a in allowed)}."
            )
        elif len(features) > 1 and not policy["feature"].get("allow_multiple", False):
            result.errors.append("**Feature** takes a single feature for each pull request.")
        elif features:
            result.values["features"] = features

    if "description" in raw and raw["description"]:
        value = raw["description"]
        low, high = policy["description"]["min_length"], policy["description"]["max_length"]
        if len(value) < low:
            result.errors.append(
                f"**Description** is too short ({len(value)} characters). "
                f"Describe the change in at least {low} characters."
            )
        elif len(value) > high:
            result.errors.append(
                f"**Description** is too long ({len(value)} characters, limit {high}). "
                "Put the detail in the rest of the pull request description."
            )
        else:
            result.values["description"] = value

    return result


# --------------------------------------------------------------------------- messages


def expected_block(policy: dict[str, Any]) -> str:
    return "\n".join(f"{policy[n]['label']}: {policy[n]['example']}" for n in FIELDS)


def failure_markdown(result: Result, policy: dict[str, Any]) -> str:
    problems = "\n".join(f"- {e}" for e in result.errors)
    allowed = ", ".join(f"`{a}`" for a in policy["feature"]["allowed"])
    multiple = (
        " Separate several features with commas."
        if policy["feature"].get("allow_multiple")
        else ""
    )
    return (
        f"{COMMENT_MARKER}\n"
        "### Change details check failed\n\n"
        "This pull request can't be merged until its description has the change details "
        "below. The description becomes the commit message on `main` and is sent to AAP.\n\n"
        f"**What needs fixing**\n{problems}\n\n"
        "**Expected format** (add these lines to the pull request description):\n"
        f"```\n{expected_block(policy)}\n```\n"
        f"Allowed features: {allowed}.{multiple}\n\n"
        "Edit the description and the check runs again on its own."
    )


def success_markdown(result: Result) -> str:
    v = result.values
    return (
        f"{COMMENT_MARKER}\n"
        "### Change details check passed\n\n"
        "| Field | Value |\n|---|---|\n"
        f"| ServiceNow | `{v['servicenow']}` |\n"
        f"| Jira | `{v['jira']}` |\n"
        f"| Feature | {', '.join(f'`{f}`' for f in v['features'])} |\n"
        f"| Description | {_md_escape(v['description'])} |\n\n"
        "After approval and merge, these details are sent to AAP to start the change."
    )


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown.replace(COMMENT_MARKER, "").strip() + "\n")


def annotate_errors(result: Result) -> None:
    for error in result.errors:
        plain = error.replace("**", "").replace("`", "'")
        print(f"::error title=Change details::{plain}")


# --------------------------------------------------------------------------- GitHub API


class GitHub:
    def __init__(self, token: str, api_url: str | None = None) -> None:
        self.token = token
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def paginate(self, path: str, limit: int = 3000) -> list[Any]:
        items: list[Any] = []
        page = 1
        sep = "&" if "?" in path else "?"
        while len(items) < limit:
            batch = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items[:limit]


def upsert_comment(gh: GitHub, repo: str, pr_number: int, body: str) -> None:
    comments = gh.paginate(f"/repos/{repo}/issues/{pr_number}/comments")
    mine = [c for c in comments if COMMENT_MARKER in (c.get("body") or "")]
    if mine:
        gh.request("PATCH", f"/repos/{repo}/issues/comments/{mine[-1]['id']}", {"body": body})
    else:
        gh.request("POST", f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})


def approval_state(gh: GitHub, repo: str, pr: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (approvers, reviewers requesting changes) from each reviewer's latest review."""
    latest: dict[str, str] = {}
    author = (pr.get("user") or {}).get("login")
    for review in gh.paginate(f"/repos/{repo}/pulls/{pr['number']}/reviews"):
        login = (review.get("user") or {}).get("login")
        state = review.get("state")
        if not login or login == author or state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        latest[login] = state
    approvers = sorted(u for u, s in latest.items() if s == "APPROVED")
    blockers = sorted(u for u, s in latest.items() if s == "CHANGES_REQUESTED")
    return approvers, blockers


# --------------------------------------------------------------------------- commands


def load_event() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        sys.exit("GITHUB_EVENT_PATH is not set: run this inside GitHub Actions or set it to an event file.")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pr_message(pr: dict[str, Any]) -> str:
    """The squash commit message GitHub builds: title, blank line, description."""
    return f"{pr.get('title') or ''}\n\n{pr.get('body') or ''}"


def cmd_validate_pr(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    event = load_event()
    pr = event["pull_request"]
    result = parse_message(pr_message(pr), policy)
    markdown = success_markdown(result) if result.ok else failure_markdown(result, policy)
    write_summary(markdown)

    if args.comment:
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY") or event["repository"]["full_name"]
        if token:
            try:
                upsert_comment(GitHub(token), repo, pr["number"], markdown)
            except urllib.error.HTTPError as exc:
                print(f"::warning::Could not update the pull request comment ({exc.code}). The check result still stands.")
        else:
            print("::warning::GITHUB_TOKEN is not set, so no pull request comment was written.")

    if result.ok:
        print("Change details check passed:", json.dumps(result.values))
        return 0
    annotate_errors(result)
    print(markdown.replace(COMMENT_MARKER, "").strip())
    return 1


def cmd_validate_message(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    message = Path(args.file).read_text(encoding="utf-8")
    result = parse_message(message, policy)
    if result.ok:
        print(json.dumps(result.values, indent=2))
        return 0
    print(failure_markdown(result, policy).replace(COMMENT_MARKER, "").strip(), file=sys.stderr)
    return 1


def cmd_merged(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    event = load_event()
    pr = event["pull_request"]
    repo = os.environ.get("GITHUB_REPOSITORY") or event["repository"]["full_name"]

    if not pr.get("merged"):
        print("::error::This pull request was closed without merging. Nothing is sent to AAP.")
        return 1

    result = parse_message(pr_message(pr), policy)
    if not result.ok:
        annotate_errors(result)
        write_summary(failure_markdown(result, policy))
        print("::error::The merged pull request has no valid change details, so AAP was not started. "
              "Check whether the required status check was bypassed.")
        return 1

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is required to check approvals.")
    gh = GitHub(token)

    approvers, blockers = approval_state(gh, repo, pr)
    required = int(policy.get("required_approvals", 1))
    if len(approvers) < required or blockers:
        reason = (
            f"it has {len(approvers)} approval(s) and needs {required}"
            if len(approvers) < required
            else f"changes were still requested by {', '.join(blockers)}"
        )
        print(f"::error title=Not approved::Pull request #{pr['number']} was merged but {reason}. "
              "AAP was not started. Branch protection may have been bypassed.")
        write_summary(f"### AAP not started\n\nPull request #{pr['number']} was merged but {reason}.")
        return 1

    merge_sha = pr["merge_commit_sha"]
    commit = gh.request("GET", f"/repos/{repo}/commits/{merge_sha}")
    parents = [p["sha"] for p in commit.get("parents", [])]

    limit = int(policy.get("max_changed_files", 500))
    files = gh.paginate(f"/repos/{repo}/pulls/{pr['number']}/files", limit=limit + 1)
    changed = [f["filename"] for f in files[:limit]]

    v = result.values
    extra_vars = {
        "gitops_servicenow_number": v["servicenow"],
        "gitops_jira_key": v["jira"],
        "gitops_features": v["features"],
        "gitops_description": v["description"],
        "gitops_summary": pr.get("title") or "",
        "gitops_commit_sha": merge_sha,
        "gitops_previous_sha": parents[0] if parents else pr["base"]["sha"],
        "gitops_repository": repo,
        "gitops_branch": pr["base"]["ref"],
        "gitops_pr_number": pr["number"],
        "gitops_pr_url": pr["html_url"],
        "gitops_author": (pr.get("user") or {}).get("login"),
        "gitops_merged_by": (pr.get("merged_by") or {}).get("login"),
        "gitops_merged_at": pr.get("merged_at"),
        "gitops_approvers": approvers,
        "gitops_changed_files": changed,
        "gitops_changed_files_truncated": len(files) > limit,
    }

    Path(args.output).write_text(json.dumps(extra_vars, indent=2), encoding="utf-8")
    write_summary(
        "### Change details for AAP\n\n"
        f"| ServiceNow | Jira | Feature | Commit | Approved by |\n|---|---|---|---|---|\n"
        f"| `{v['servicenow']}` | `{v['jira']}` | {', '.join(v['features'])} | "
        f"`{merge_sha[:10]}` | {', '.join(approvers)} |\n\n"
        f"{_md_escape(v['description'])}\n"
    )
    print(f"Change details valid, approved by {', '.join(approvers)}. Extra vars written to {args.output}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", default=".github/change-policy.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-pr", help="validate the pull request in GITHUB_EVENT_PATH")
    p.add_argument("--comment", action="store_true", help="create or update one PR comment with the result")

    p = sub.add_parser("validate-message", help="validate a commit message file")
    p.add_argument("--file", required=True)

    p = sub.add_parser("merged", help="validate a merged PR, check approvals, write AAP extra vars")
    p.add_argument("--output", required=True)

    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    handler = {
        "validate-pr": cmd_validate_pr,
        "validate-message": cmd_validate_message,
        "merged": cmd_merged,
    }[args.command]
    return handler(args, policy)


if __name__ == "__main__":
    sys.exit(main())
