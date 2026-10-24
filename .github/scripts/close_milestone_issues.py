#!/usr/bin/env python3
"""Close the open issues of a release's milestone and the milestone itself.

Maintainer script for the release workflow; not a pipeline tool.
"""

import argparse
import json
import subprocess
import sys

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUNTIME = 3


def gh(args):
    """Run a gh command and return the completed process."""
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def milestone_number(repo_path, tag):
    """Return the milestone number for tag, or an empty string when absent."""
    result = gh(["api", f"repos/{repo_path}/milestones", "--jq",
                 f'.[] | select(.title=="{tag}") | .number'])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api failed")
    return result.stdout.strip()


def open_issues(repo, tag):
    """Return the open issues of the milestone as a list of dicts."""
    args = ["issue", "list", "--milestone", tag, "--state", "open",
            "--json", "number,title", "--limit", "1000"]
    if repo:
        args += ["--repo", repo]
    result = gh(args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh issue list failed")
    return json.loads(result.stdout or "[]")


def close_issue(repo, number, comment):
    """Close one issue with the shipped comment."""
    args = ["issue", "close", str(number), "--comment", comment]
    if repo:
        args += ["--repo", repo]
    result = gh(args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh issue close failed")


def close_milestone(repo_path, number):
    """Close the milestone by number."""
    result = gh(["api", "-X", "PATCH", f"repos/{repo_path}/milestones/{number}",
                 "-f", "state=closed"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api failed")


def parse_args(argv):
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Close the open issues of a release's milestone and the milestone.")
    parser.add_argument("--tag", required=True,
                        help="the milestone title and the release tag")
    parser.add_argument("--repo", default="", help="OWNER/REPO passed to gh")
    parser.add_argument("--release-url", default="", help="override the release URL")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the actions, call nothing")
    parser.add_argument("--issues-file", default="",
                        help="with --dry-run, read the issue list from a JSON file")
    return parser.parse_args(argv)


def main(argv=None):
    """Close the milestone's open issues and the milestone."""
    args = parse_args(argv)
    repo_path = args.repo or "{owner}/{repo}"
    release_url = args.release_url or (
        f"https://github.com/{args.repo}/releases/tag/{args.tag}" if args.repo else "")
    comment = f"Shipped in {args.tag}: {release_url}"

    if args.dry_run and args.issues_file:
        with open(args.issues_file, encoding="utf-8") as handle:
            issues = json.load(handle)
        for issue in issues:
            print(f"would close #{issue['number']}: {comment}")
        print(f"would close milestone {args.tag}")
        return EXIT_OK

    try:
        number = milestone_number(repo_path, args.tag)
        if not number:
            print(f"no milestone named {args.tag}")
            return EXIT_OK
        issues = open_issues(args.repo, args.tag)
        if args.dry_run:
            for issue in issues:
                print(f"would close #{issue['number']}: {comment}")
            print(f"would close milestone {args.tag}")
            return EXIT_OK
        for issue in issues:
            close_issue(args.repo, issue["number"], comment)
        close_milestone(repo_path, number)
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"close_milestone_issues: {error}", file=sys.stderr)
        return EXIT_RUNTIME
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
