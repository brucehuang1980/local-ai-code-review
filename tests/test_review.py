"""Tests for review.py — load_config, chunk_diff, build_prompt, get_git_diff,
review_chunk, run_review."""

import os
import pathlib
from unittest.mock import MagicMock, mock_open

import pytest

from review import build_prompt, chunk_diff, get_git_diff, load_config, review_chunk, run_review


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_load_config_defaults(self, mocker):
        """No config.yaml → return built-in defaults."""
        mock_path_cls = mocker.patch("review.Path")
        config_path_mock = MagicMock()
        config_path_mock.exists.return_value = False
        mock_path_cls.return_value.parent.__truediv__.return_value = config_path_mock

        config = load_config()

        assert config["review"]["language"] == "zh-TW"
        assert config["review"]["max_diff_lines"] == 500
        assert config["review"]["output"] == "markdown"

    def test_load_config_merges_yaml(self, mocker):
        """config.yaml overrides language but preserves other defaults."""
        yaml_content = "review:\n  language: en\n"
        mock_path_cls = mocker.patch("review.Path")
        config_path_mock = MagicMock()
        config_path_mock.exists.return_value = True
        mock_path_cls.return_value.parent.__truediv__.return_value = config_path_mock
        mocker.patch("builtins.open", mock_open(read_data=yaml_content))

        config = load_config()

        assert config["review"]["language"] == "en"
        assert config["review"]["max_diff_lines"] == 500

    def test_load_config_partial_yaml(self, mocker):
        """config.yaml sets only max_diff_lines; other keys keep defaults."""
        yaml_content = "review:\n  max_diff_lines: 200\n"
        mock_path_cls = mocker.patch("review.Path")
        config_path_mock = MagicMock()
        config_path_mock.exists.return_value = True
        mock_path_cls.return_value.parent.__truediv__.return_value = config_path_mock
        mocker.patch("builtins.open", mock_open(read_data=yaml_content))

        config = load_config()

        assert config["review"]["max_diff_lines"] == 200
        assert config["review"]["language"] == "zh-TW"
        assert config["review"]["output"] == "markdown"


# ---------------------------------------------------------------------------
# chunk_diff
# ---------------------------------------------------------------------------

class TestChunkDiff:
    def test_chunk_diff_empty(self):
        assert chunk_diff("", 10) == []

    def test_chunk_diff_small(self):
        diff = "\n".join(f"line {i}" for i in range(5))
        result = chunk_diff(diff, 10)
        assert len(result) == 1
        assert result[0] == diff

    def test_chunk_diff_exact(self):
        diff = "\n".join(f"line {i}" for i in range(10))
        result = chunk_diff(diff, 10)
        assert len(result) == 1

    def test_chunk_diff_over(self):
        diff = "\n".join(f"line {i}" for i in range(25))
        result = chunk_diff(diff, 10)
        assert len(result) == 3

    def test_chunk_diff_content_preserved(self):
        diff = "\n".join(f"line {i}" for i in range(15))
        chunks = chunk_diff(diff, 10)
        rejoined = "\n".join(chunks)
        assert rejoined == diff


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_build_prompt_single_chunk(self):
        prompt = build_prompt("+ added", "zh-TW", 0, 1)
        assert "（第" not in prompt

    def test_build_prompt_multi_chunk(self):
        prompt = build_prompt("+ added", "zh-TW", 0, 3)
        assert "（第 1/3 部分）" in prompt

    def test_build_prompt_contains_diff(self):
        diff = "+ unique_marker_line"
        prompt = build_prompt(diff, "zh-TW", 0, 1)
        assert "unique_marker_line" in prompt

    def test_build_prompt_contains_language(self):
        prompt = build_prompt("+ x", "en", 0, 1)
        assert "en" in prompt


# ---------------------------------------------------------------------------
# get_git_diff
# ---------------------------------------------------------------------------

class TestGetGitDiff:
    def test_get_git_diff_success(self, mocker):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "+added line\n-removed line\n"
        mocker.patch("review.subprocess.run", return_value=mock_result)

        result = get_git_diff("abc123", "def456")

        assert result == "+added line\n-removed line\n"

    def test_get_git_diff_failure(self, mocker):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "fatal: bad revision"
        mocker.patch("review.subprocess.run", return_value=mock_result)

        with pytest.raises(SystemExit):
            get_git_diff("bad", "commit")


# ---------------------------------------------------------------------------
# review_chunk
# ---------------------------------------------------------------------------

class TestReviewChunk:
    def _make_client(self, content="review text"):
        client = MagicMock()
        response = MagicMock()
        response.choices[0].message.content = content
        client.chat.completions.create.return_value = response
        return client

    def test_review_chunk_calls_api(self):
        client = self._make_client()
        review_chunk(client, "gpt-4", "+ test line", "zh-TW", 0, 1)

        client.chat.completions.create.assert_called_once()
        kwargs = client.chat.completions.create.call_args[1]
        assert kwargs["model"] == "gpt-4"
        assert kwargs["temperature"] == 0.1

    def test_review_chunk_returns_content(self):
        client = self._make_client("## 摘要\n測試通過")
        result = review_chunk(client, "gpt-4", "+ test", "zh-TW", 0, 1)
        assert result == "## 摘要\n測試通過"


# ---------------------------------------------------------------------------
# run_review
# ---------------------------------------------------------------------------

class TestRunReview:
    def test_run_review_empty_diff(self, mocker):
        mocker.patch("review.load_dotenv")
        mocker.patch("review.get_git_diff", return_value="   \n  ")

        result = run_review("abc", "def", base_url="http://localhost", api_key="x")

        assert result == ""

    def test_run_review_single_chunk(self, mocker):
        mocker.patch("review.load_dotenv")
        mocker.patch("review.get_git_diff", return_value="+ added line\n- removed line")
        mocker.patch("review.review_chunk", return_value="## 摘要\n很好")

        result = run_review("abc123", "def456", base_url="http://localhost", api_key="x")

        assert "Code Review Report" in result
        assert "abc123..def456" in result
        assert "## 摘要" in result

    def test_run_review_output_file(self, mocker, tmp_path):
        mocker.patch("review.load_dotenv")
        mocker.patch("review.get_git_diff", return_value="+ added line")
        mocker.patch("review.review_chunk", return_value="## 審查報告")

        output = str(tmp_path / "report.md")
        run_review("abc", "def", base_url="http://localhost", api_key="x", output_file=output)

        content = pathlib.Path(output).read_text(encoding="utf-8")
        assert "Code Review Report" in content
        assert "## 審查報告" in content
