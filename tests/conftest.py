import pytest


@pytest.fixture
def sample_pr_payload():
    return {
        "eventKey": "pr:opened",
        "pullRequest": {
            "id": 42,
            "fromRef": {
                "latestCommit": "abc1234567890abcd",
                "repository": {
                    "slug": "my-repo",
                    "project": {"key": "PROJ"},
                },
            },
            "toRef": {
                "latestCommit": "def9876543210efgh",
                "repository": {
                    "slug": "my-repo",
                    "project": {"key": "PROJ"},
                },
            },
        },
    }
