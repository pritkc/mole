import unittest

from tools.issue_dashboard.github_issue_dashboard import (
    UNLABELED,
    build_dashboard,
    labels_at_time,
    normalize_item,
)


class ClosureLabelTests(unittest.TestCase):
    def test_label_added_after_closure_is_excluded(self):
        labels, source = labels_at_time(
            [
                {
                    "event": "labeled",
                    "label": {"name": "Bug"},
                    "created_at": "2026-01-02T00:00:00Z",
                },
                {
                    "event": "labeled",
                    "label": {"name": "stale"},
                    "created_at": "2026-01-11T00:00:00Z",
                },
            ],
            "2026-01-10T00:00:00Z",
            fallback_labels=["Bug", "stale"],
        )

        self.assertEqual(labels, ["Bug"])
        self.assertEqual(source, "timeline")

    def test_label_removed_after_closure_remains_in_closure_labels(self):
        labels, source = labels_at_time(
            [
                {
                    "event": "labeled",
                    "label": {"name": "Documentation"},
                    "created_at": "2026-01-02T00:00:00Z",
                },
                {
                    "event": "unlabeled",
                    "label": {"name": "Documentation"},
                    "created_at": "2026-01-11T00:00:00Z",
                },
            ],
            "2026-01-10T00:00:00Z",
            fallback_labels=[],
        )

        self.assertEqual(labels, ["Documentation"])
        self.assertEqual(source, "timeline")

    def test_label_removed_before_closure_is_excluded(self):
        labels, source = labels_at_time(
            [
                {
                    "event": "labeled",
                    "label": {"name": "Enhancement"},
                    "created_at": "2026-01-02T00:00:00Z",
                },
                {
                    "event": "unlabeled",
                    "label": {"name": "Enhancement"},
                    "created_at": "2026-01-08T00:00:00Z",
                },
            ],
            "2026-01-10T00:00:00Z",
            fallback_labels=["Enhancement"],
        )

        self.assertEqual(labels, [])
        self.assertEqual(source, "timeline")

    def test_normalized_closed_item_uses_closure_labels_for_metrics(self):
        issue = {
            "number": 10,
            "state": "closed",
            "title": "Closed bug",
            "html_url": "https://github.com/csrc-sdsu/mole/issues/10",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-12T00:00:00Z",
            "closed_at": "2026-01-11T00:00:00Z",
            "labels": [{"name": "stale"}],
            "user": {"login": "octocat"},
        }

        record = normalize_item(
            issue,
            None,
            [
                {
                    "event": "labeled",
                    "label": {"name": "Bug"},
                    "created_at": "2026-01-03T00:00:00Z",
                },
                {
                    "event": "labeled",
                    "label": {"name": "stale"},
                    "created_at": "2026-01-12T00:00:00Z",
                },
            ],
            "https://github.com/csrc-sdsu/mole",
        )

        self.assertEqual(record["labels_current"], ["stale"])
        self.assertEqual(record["labels_at_close"], ["Bug"])
        self.assertEqual(record["metric_labels"], ["Bug"])
        self.assertEqual(record["days_to_close"], 10.0)


class DashboardAggregationTests(unittest.TestCase):
    def test_aggregates_closed_by_closure_labels_and_open_by_current_labels(self):
        records = [
            {
                "number": 3,
                "type": "issue",
                "state": "closed",
                "title": "Closed as bug",
                "url": "https://github.com/csrc-sdsu/mole/issues/3",
                "created_at": "2026-01-01T00:00:00Z",
                "closed_at": "2026-01-11T00:00:00Z",
                "merged_at": None,
                "metric_labels": ["Bug"],
                "labels_current": ["stale"],
                "labels_at_close": ["Bug"],
                "days_to_close": 10.0,
                "days_to_merge": None,
            },
            {
                "number": 2,
                "type": "issue",
                "state": "open",
                "title": "Open docs",
                "url": "https://github.com/csrc-sdsu/mole/issues/2",
                "created_at": "2026-01-06T00:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "metric_labels": ["Documentation"],
                "labels_current": ["Documentation"],
                "labels_at_close": [],
                "days_to_close": None,
                "days_to_merge": None,
            },
            {
                "number": 1,
                "type": "pull_request",
                "state": "closed",
                "title": "Unlabeled PR",
                "url": "https://github.com/csrc-sdsu/mole/pull/1",
                "created_at": "2026-01-01T00:00:00Z",
                "closed_at": "2026-01-03T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",
                "metric_labels": [UNLABELED],
                "labels_current": [],
                "labels_at_close": [],
                "days_to_close": 2.0,
                "days_to_merge": 2.0,
            },
        ]

        dashboard = build_dashboard(
            records,
            [
                {"name": "Bug", "color": "B60205", "description": ""},
                {"name": "Documentation", "color": "0075ca", "description": ""},
            ],
            "csrc-sdsu/mole",
            generated_at="2026-01-21T00:00:00Z",
        )
        metrics = {item["label"]: item for item in dashboard["label_metrics"]}

        self.assertEqual(metrics["Bug"]["closed"], 1)
        self.assertEqual(metrics["Bug"]["average_days_to_close"], 10.0)
        self.assertEqual(metrics["Documentation"]["open"], 1)
        self.assertEqual(metrics[UNLABELED]["merged_pull_requests"], 1)
        self.assertEqual(dashboard["summary"]["open_issues"], 1)


if __name__ == "__main__":
    unittest.main()
