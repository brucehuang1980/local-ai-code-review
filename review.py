#!/usr/bin/env python3
"""AI Code Review CLI — reviews a git commit range using an OpenAI-compatible LLM."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from openai import OpenAI


def load_config() -> dict:
    defaults = {
        "review": {
            "language": "zh-TW",
            "max_diff_lines": 500,
            "output": "markdown",
        }
    }
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
            for key, val in user_config.items():
                if isinstance(val, dict) and key in defaults:
                    defaults[key].update(val)
                else:
                    defaults[key] = val
    return defaults


def get_git_diff(from_commit: str, to_commit: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{from_commit}..{to_commit}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: git diff failed\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def chunk_diff(diff: str, max_lines: int) -> list[str]:
    lines = diff.splitlines()
    chunks, current = [], []
    for line in lines:
        current.append(line)
        if len(current) >= max_lines:
            chunks.append("\n".join(current))
            current = []
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_prompt(diff: str, language: str, chunk_index: int, total_chunks: int) -> str:
    chunk_note = f"（第 {chunk_index + 1}/{total_chunks} 部分）" if total_chunks > 1 else ""
    return f"""你是一位資深軟體工程師，請審查以下 git diff 代碼變更{chunk_note}。

請用 {language} 回覆，格式為 Markdown，包含：
1. **摘要**：本次變更概述
2. **問題**：潛在 bug、安全漏洞、效能問題（若無填「無」）
3. **建議**：改善建議（若無填「無」）
4. **評分**：1-10 分，附簡短說明

```diff
{diff}
```"""


def review_chunk(
    client: OpenAI,
    model: str,
    diff: str,
    language: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    prompt = build_prompt(diff, language, chunk_index, total_chunks)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return response.choices[0].message.content


def run_review(
    from_commit: str,
    to_commit: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    load_dotenv()
    config = load_config()

    final_base_url = base_url or os.getenv("LLM_BASE_URL")
    final_api_key = api_key or os.getenv("LLM_API_KEY", "dummy")
    final_model = model or os.getenv("LLM_MODEL", "gpt-4")
    language = config["review"]["language"]
    max_diff_lines = int(config["review"]["max_diff_lines"])

    if not final_base_url:
        print("Error: LLM_BASE_URL not set. Use --base-url or set in .env", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(base_url=final_base_url, api_key=final_api_key)

    print(f"Getting diff: {from_commit}..{to_commit}")
    diff = get_git_diff(from_commit, to_commit)

    if not diff.strip():
        print("No changes found in the specified commit range.")
        return ""

    chunks = chunk_diff(diff, max_diff_lines)
    print(f"Reviewing {len(chunks)} chunk(s)...")

    parts = []
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i + 1}/{len(chunks)}...")
        parts.append(review_chunk(client, final_model, chunk, language, i, len(chunks)))

    separator = "\n\n---\n\n"
    report = (
        f"# Code Review Report\n\n"
        f"**Commit Range**: `{from_commit}..{to_commit}`\n\n"
        + separator.join(parts)
    )

    if output_file:
        Path(output_file).write_text(report, encoding="utf-8")
        print(f"Report saved: {output_file}")
    else:
        print("\n" + report)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="AI Code Review — review a git commit range with a local LLM"
    )
    parser.add_argument("--from", dest="from_commit", required=True, help="Start commit (exclusive)")
    parser.add_argument("--to", dest="to_commit", required=True, help="End commit (inclusive)")
    parser.add_argument("--base-url", help="LLM API base URL (overrides .env LLM_BASE_URL)")
    parser.add_argument("--api-key", help="LLM API key (overrides .env LLM_API_KEY)")
    parser.add_argument("--model", help="Model name (overrides .env LLM_MODEL)")
    parser.add_argument("--output", help="Write report to file instead of stdout")
    args = parser.parse_args()

    run_review(
        from_commit=args.from_commit,
        to_commit=args.to_commit,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
