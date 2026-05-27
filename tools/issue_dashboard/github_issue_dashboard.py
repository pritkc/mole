#!/usr/bin/env python3
"""Generate static issue and pull request label analytics for MOLE.

Closed items are grouped by the labels present at closure time. Open items are
grouped by their current labels.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
UNLABELED = "(unlabeled)"


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if not start_dt or not end_dt:
        return None
    return max((end_dt - start_dt).total_seconds(), 0.0)


def days_from_seconds(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value / 86400, 2)


def label_name(label: Any) -> str | None:
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        return label.get("name")
    return None


def current_label_names(item: dict[str, Any]) -> list[str]:
    names = [label_name(label) for label in item.get("labels", [])]
    return sorted(name for name in names if name)


def label_catalog(labels: list[dict[str, Any]]) -> list[dict[str, str]]:
    catalog = []
    for label in labels:
        name = label.get("name")
        if not name:
            continue
        catalog.append(
            {
                "name": name,
                "color": label.get("color") or "ededed",
                "description": label.get("description") or "",
            }
        )
    return sorted(catalog, key=lambda item: item["name"].casefold())


def labels_at_time(
    timeline: list[dict[str, Any]],
    as_of: str | None,
    fallback_labels: list[str] | None = None,
) -> tuple[list[str], str]:
    """Replay label timeline events up to ``as_of``.

    If there are no label timeline events at all, fall back to the supplied
    labels and mark the source as a fallback. That keeps old or incomplete API
    histories usable while making the uncertainty visible in the output.
    """

    as_of_dt = parse_timestamp(as_of)
    if not as_of_dt:
        return sorted(fallback_labels or []), "current"

    label_events = []
    for event in timeline:
        event_type = event.get("event")
        if event_type not in {"labeled", "unlabeled"}:
            continue
        name = label_name(event.get("label"))
        created_at = parse_timestamp(event.get("created_at"))
        if name and created_at:
            label_events.append((created_at, event_type, name))

    if not label_events:
        return sorted(fallback_labels or []), "current-fallback"

    labels: set[str] = set()
    for created_at, event_type, name in sorted(label_events, key=lambda item: item[0]):
        if created_at > as_of_dt:
            break
        if event_type == "labeled":
            labels.add(name)
        elif event_type == "unlabeled":
            labels.discard(name)

    return sorted(labels), "timeline"


def item_type(issue: dict[str, Any]) -> str:
    return "pull_request" if issue.get("pull_request") else "issue"


def normalize_item(
    issue: dict[str, Any],
    pull: dict[str, Any] | None,
    timeline: list[dict[str, Any]],
    repository_url: str,
) -> dict[str, Any]:
    current_labels = current_label_names(issue)
    closed_at = issue.get("closed_at")
    labels_at_close: list[str] = []
    labels_at_close_source = "not-closed"

    if closed_at:
        labels_at_close, labels_at_close_source = labels_at_time(
            timeline,
            closed_at,
            fallback_labels=current_labels,
        )

    type_name = item_type(issue)
    merged_at = pull.get("merged_at") if pull else None
    closure_seconds = seconds_between(issue.get("created_at"), closed_at)
    merge_seconds = seconds_between(issue.get("created_at"), merged_at)
    reference_labels = labels_at_close if closed_at else current_labels

    return {
        "number": issue.get("number"),
        "type": type_name,
        "state": issue.get("state"),
        "title": issue.get("title") or "",
        "url": issue.get("html_url") or f"{repository_url}/issues/{issue.get('number')}",
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": closed_at,
        "merged_at": merged_at,
        "author": (issue.get("user") or {}).get("login"),
        "labels_current": current_labels,
        "labels_at_close": labels_at_close,
        "labels_at_close_source": labels_at_close_source,
        "metric_labels": reference_labels or [UNLABELED],
        "days_to_close": days_from_seconds(closure_seconds),
        "days_to_merge": days_from_seconds(merge_seconds),
    }


def percentile_days(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"average": None, "median": None}
    return {
        "average": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
    }


def build_dashboard(
    records: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    repository: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    generated_dt = parse_timestamp(generated_at) or datetime.now(timezone.utc)

    label_info = {item["name"]: item for item in label_catalog(labels)}
    label_names = sorted(
        set(label_info)
        | {label for record in records for label in record.get("metric_labels", [])},
        key=str.casefold,
    )

    label_metrics = []
    for name in label_names:
        scoped = [record for record in records if name in record.get("metric_labels", [])]
        closed = [record for record in scoped if record.get("closed_at")]
        open_items = [record for record in scoped if not record.get("closed_at")]
        pulls = [record for record in scoped if record.get("type") == "pull_request"]
        issues = [record for record in scoped if record.get("type") == "issue"]
        close_days = [record["days_to_close"] for record in closed if record.get("days_to_close") is not None]
        merge_days = [record["days_to_merge"] for record in pulls if record.get("days_to_merge") is not None]
        open_ages = [
            days_from_seconds(seconds_between(record.get("created_at"), generated_at))
            for record in open_items
        ]
        open_ages = [age for age in open_ages if age is not None]
        close_stats = percentile_days(close_days)
        merge_stats = percentile_days(merge_days)
        info = label_info.get(name, {"name": name, "color": "ededed", "description": ""})

        label_metrics.append(
            {
                "label": name,
                "color": info.get("color") or "ededed",
                "description": info.get("description") or "",
                "total": len(scoped),
                "issues": len(issues),
                "pull_requests": len(pulls),
                "open": len(open_items),
                "closed": len(closed),
                "open_issues": sum(1 for record in issues if not record.get("closed_at")),
                "open_pull_requests": sum(1 for record in pulls if not record.get("closed_at")),
                "merged_pull_requests": sum(1 for record in pulls if record.get("merged_at")),
                "closed_unmerged_pull_requests": sum(
                    1
                    for record in pulls
                    if record.get("closed_at") and not record.get("merged_at")
                ),
                "average_days_to_close": close_stats["average"],
                "median_days_to_close": close_stats["median"],
                "average_days_to_merge": merge_stats["average"],
                "median_days_to_merge": merge_stats["median"],
                "oldest_open_days": round(max(open_ages), 2) if open_ages else None,
            }
        )

    label_metrics.sort(key=lambda item: (-item["total"], item["label"].casefold()))
    open_records = [record for record in records if not record.get("closed_at")]
    closed_records = [record for record in records if record.get("closed_at")]

    return {
        "schema_version": 1,
        "repository": repository,
        "generated_at": generated_dt.isoformat().replace("+00:00", "Z"),
        "label_source_policy": (
            "Closed issues and pull requests are grouped by labels reconstructed "
            "from timeline events at closed_at. Open items are grouped by current labels."
        ),
        "summary": {
            "total": len(records),
            "open": len(open_records),
            "closed": len(closed_records),
            "issues": sum(1 for record in records if record.get("type") == "issue"),
            "pull_requests": sum(1 for record in records if record.get("type") == "pull_request"),
            "open_issues": sum(
                1 for record in records if record.get("type") == "issue" and not record.get("closed_at")
            ),
            "open_pull_requests": sum(
                1
                for record in records
                if record.get("type") == "pull_request" and not record.get("closed_at")
            ),
            "merged_pull_requests": sum(1 for record in records if record.get("merged_at")),
            "labels": len(label_names),
        },
        "labels": [label_info[name] for name in sorted(label_info, key=str.casefold)],
        "label_metrics": label_metrics,
        "items": sorted(records, key=lambda item: item.get("number") or 0, reverse=True),
    }


@dataclass
class GitHubClient:
    token: str | None = None
    api_root: str = API_ROOT
    max_retries: int = 3

    def get_json(self, url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mole-issue-dashboard",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        for attempt in range(self.max_retries):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    links = response.headers.get("Link", "")
                    return payload, links
            except urllib.error.HTTPError as error:
                if error.code in {403, 429, 502, 503, 504} and attempt + 1 < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"GitHub API request failed: {error.code} {url}\n{detail}") from error

        raise RuntimeError(f"GitHub API request failed after retries: {url}")

    def paginate(self, path: str) -> list[Any]:
        url = f"{self.api_root}{path}"
        items: list[Any] = []
        while url:
            payload, links = self.get_json(url)
            if not isinstance(payload, list):
                raise RuntimeError(f"Expected list response from {url}")
            items.extend(payload)
            url = next_link(links)
        return items


def next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">")
        if start != -1 and end != -1 and end > start:
            return section[start + 1 : end]
    return None


def repo_path(owner: str, repo: str, endpoint: str, **params: str) -> str:
    query = urllib.parse.urlencode(params)
    return f"/repos/{owner}/{repo}/{endpoint}?{query}" if query else f"/repos/{owner}/{repo}/{endpoint}"


def fetch_repository_dashboard(owner: str, repo: str, token: str | None) -> dict[str, Any]:
    client = GitHubClient(token=token)
    repository = f"{owner}/{repo}"
    repository_url = f"https://github.com/{repository}"
    labels = client.paginate(repo_path(owner, repo, "labels", per_page="100"))
    issues = client.paginate(
        repo_path(owner, repo, "issues", state="all", per_page="100", sort="created", direction="asc")
    )
    pulls = client.paginate(
        repo_path(owner, repo, "pulls", state="all", per_page="100", sort="created", direction="asc")
    )
    pulls_by_number = {pull.get("number"): pull for pull in pulls}

    records = []
    closed_count = sum(1 for issue in issues if issue.get("closed_at"))
    closed_seen = 0
    for issue in issues:
        timeline: list[dict[str, Any]] = []
        if issue.get("closed_at"):
            closed_seen += 1
            timeline = client.paginate(
                repo_path(owner, repo, f"issues/{issue['number']}/timeline", per_page="100")
            )
        records.append(
            normalize_item(
                issue,
                pulls_by_number.get(issue.get("number")),
                timeline,
                repository_url,
            )
        )
        if issue.get("closed_at"):
            print(f"Fetched timeline {closed_seen}/{closed_count}: #{issue['number']}", file=sys.stderr)

    return build_dashboard(records, labels, repository)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_repository = os.environ.get("GITHUB_REPOSITORY", "csrc-sdsu/mole")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=default_repository, help="GitHub repository as owner/name")
    parser.add_argument(
        "--output",
        default="doc/sphinx/source/_static/issue-dashboard/data/dashboard.json",
        help="Output JSON path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if "/" not in args.repository:
        raise SystemExit("--repository must use owner/name format")

    owner, repo = args.repository.split("/", 1)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    dashboard = fetch_repository_dashboard(owner, repo, token)
    write_json(Path(args.output), dashboard)
    print(f"Wrote {args.output} with {dashboard['summary']['total']} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
