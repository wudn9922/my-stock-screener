import os
import subprocess
from datetime import datetime


def run_git_command(command):
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    if result.stderr.strip():
        print(result.stderr.strip())

    return result.returncode


def get_csv_last_date(csv_path):
    """
    讀取 CSV 最後一列的日期，失敗時回傳 None。
    只讀檔尾，避免整檔載入。
    """
    try:
        with open(
            csv_path,
            "rb"
        ) as csv_file:
            csv_file.seek(0, os.SEEK_END)
            size = csv_file.tell()
            csv_file.seek(
                max(0, size - 512)
            )
            tail_lines = (
                csv_file.read()
                .decode(
                    "utf-8",
                    errors="ignore"
                )
                .strip()
                .splitlines()
            )

        if len(tail_lines) < 2:
            return None

        return datetime.strptime(
            tail_lines[-1]
            .split(",")[0]
            .strip()[:10],
            "%Y-%m-%d"
        )

    except Exception:
        return None


def push_report_to_github():
    if os.environ.get("GENERATED_BRANCH_MODE") == "1":
        # schedule.yml publishes the complete daily snapshot after all builders.
        print("GENERATED_BRANCH_MODE=1：交由 workflow 提交 generated 分支")
        return True
    run_git_command(
        [
            "git",
            "config",
            "--global",
            "user.name",
            "github-actions[bot]"
        ]
    )

    run_git_command(
        [
            "git",
            "config",
            "--global",
            "user.email",
            (
                "github-actions[bot]"
                "@users.noreply.github.com"
            )
        ]
    )

    # docs/report：Atlas 報告 JSON 與 data 歷史資料
    add_paths = [
        path
        for path in [
            "docs/report",
            "data"
        ]
        if os.path.exists(path)
    ]

    if not add_paths:
        print(
            "ℹ️ 沒有需要提交的報告路徑"
        )
        return True

    add_result = run_git_command(
        [
            "git",
            "add",
            "-A",
            "--"
        ]
        + add_paths
    )

    if add_result != 0:
        print("❌ git add 失敗")
        return False

    diff_result = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--quiet"
        ],
        check=False
    )

    if diff_result.returncode == 0:
        print(
            "ℹ️ 沒有 Git 異動需要提交"
        )
        return True

    commit_result = run_git_command(
        [
            "git",
            "commit",
            "-m",
            (
                "⚙️ 量化報告自動更新 "
                "(動態群組與畫線同步)"
            )
        ]
    )

    if commit_result != 0:
        print("❌ git commit 失敗")
        return False

    push_result = run_git_command(
        [
            "git",
            "push"
        ]
    )

    if push_result == 0:
        print(
            "✅ GitHub Pages 資料推送完成"
        )
        return True

    print("❌ git push 失敗")
    return False
