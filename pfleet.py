#!/usr/bin/env python3
"""
pFleet — Portable Fleet
Sync all GitHub repositories for one or more users, concurrently.

Requires Python 3.10+ and the GitHub CLI (gh) to be installed and authenticated.
"""

import sys

# Python 3.10+ requirement enforced by pyproject.toml requires-python field

import argparse
import os
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# ANSI colours & icons
# ---------------------------------------------------------------------------


def _enable_ansi_on_windows() -> None:
    """Enable VT100/ANSI colour processing on Windows 10+ terminals."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        STD_OUTPUT_HANDLE = ctypes.c_ulong(-11)
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except (AttributeError, ImportError, OSError):
        pass  # Non-fatal: fall back to printing raw escape sequences


_enable_ansi_on_windows()

_RESET = "\033[0m"
_DIM = "\033[90m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}"


ICON_WAIT = _c(_DIM, "[ .... ]")
ICON_DONE = _c(_GREEN, "[ DONE ]")
ICON_SYNC = _c(_CYAN, "[ SYNC ]")
ICON_NEW = _c(_YELLOW, "[ NEW  ]")
ICON_FAIL = _c(_RED, "[ FAIL ]")
ICON_SKIP = _c(_YELLOW, "[ SKIP ]")
DIVIDER = _c(_DIM, "═" * 62)
SUBDIV = _c(_DIM, "─" * 62)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, capturing stdout+stderr.  Never raises on non-zero exit."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return result
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout=str(exc), stderr="")


def _sanitize_dirname(name: str) -> str:
    """Strip trailing dots and spaces (Windows FS restrictions)."""
    return name.rstrip(". ")


def _list_repos(user: str, limit: int) -> tuple[list[str] | None, str]:
    """Return (repo_names, error_message).  On success error_message is empty."""
    result = _run(["gh", "repo", "list", user, "--limit", str(limit), "--json", "name", "--jq", ".[].name"])
    if result.returncode != 0:
        msg = result.stdout.strip() or f"'gh repo list' failed with exit code {result.returncode}"
        return None, msg
    return [r for r in result.stdout.splitlines() if r.strip()], ""


# ---------------------------------------------------------------------------
# Worker — update an existing local repo
# ---------------------------------------------------------------------------


def _worker_update(repo_dir: str, *, cleanup: bool, default_branch_hint: str) -> tuple[str, int]:
    """
    Fetch + sync all remote branches for an already-cloned repo.
    Returns (status_line, branches_pruned).
    """
    name = os.path.basename(repo_dir)

    # Suppress phantom permission-bit diffs on Windows
    _run(["git", "config", "core.filemode", "false"], cwd=repo_dir)

    # Abort if this directory is not actually a git repository
    if _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_dir).returncode != 0:
        return f" {ICON_FAIL} {_c(_RED, name + '  not a git repository')}", 0

    # Abort if the working tree is dirty (includes untracked files)
    status_result = _run(["git", "status", "--porcelain"], cwd=repo_dir)
    if status_result.returncode != 0:
        detail = status_result.stdout.strip()
        return f" {ICON_FAIL} {_c(_RED, name + '  status check failed')}\n   {_c(_RED, detail)}", 0

    dirty = bool(status_result.stdout.strip())
    if dirty:
        return (
            f" {ICON_SKIP} {_c(_YELLOW, name + '  [dirty tree — skipped]')}",
            0,
        )

    # Fetch all remote changes, prune stale tracking refs
    fetch = _run(["git", "fetch", "origin", "--prune", "--quiet"], cwd=repo_dir)
    if fetch.returncode != 0:
        detail = fetch.stdout.strip()
        return f" {ICON_FAIL} {_c(_RED, name + '  fetch failed')}\n   {_c(_RED, detail)}", 0

    # Remember the current branch/HEAD so we can return to it afterwards
    br_result = _run(["git", "branch", "--show-current"], cwd=repo_dir)
    original_branch = br_result.stdout.strip()

    head_result = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo_dir)
    original_head = head_result.stdout.strip()

    # Sync every remote branch to a matching local branch
    ref_result = _run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"],
        cwd=repo_dir,
    )
    if ref_result.returncode != 0:
        detail = ref_result.stdout.strip()
        return f" {ICON_FAIL} {_c(_RED, name + '  could not list remote refs')}\n   {_c(_RED, detail)}", 0

    failed_branches = []
    for remote_ref in ref_result.stdout.splitlines():
        remote_ref = remote_ref.strip()
        if not remote_ref or remote_ref == "origin/HEAD":
            continue
        local_branch = remote_ref.removeprefix("origin/")
        co = _run(["git", "checkout", "-B", local_branch, remote_ref, "--quiet"], cwd=repo_dir)
        if co.returncode != 0:
            failed_branches.append(local_branch)
            continue  # non-fatal: try remaining branches

    # If any checkout failed, report failure instead of success
    if failed_branches:
        detail = ", ".join(failed_branches)
        return f" {ICON_FAIL} {_c(_RED, name + '  failed to sync branches: ' + detail)}", 0

    # Optional: delete local branches already merged into the default branch
    pruned = 0
    if cleanup:
        pruned = _do_cleanup(repo_dir, default_branch_hint)

    # Restore the original branch or detached HEAD
    if original_branch:
        _run(["git", "checkout", original_branch, "--quiet"], cwd=repo_dir)
    else:
        _run(["git", "checkout", "--detach", original_head, "--quiet"], cwd=repo_dir)

    return f" {ICON_SYNC} {_c(_CYAN, name + '  branches synced')}", pruned


def _do_cleanup(repo_dir: str, hint: str) -> int:
    """Delete local branches that have been merged into the default branch."""
    # Determine the default branch
    db_result = _run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_dir,
    )
    if db_result.returncode == 0:
        default_branch = db_result.stdout.strip().removeprefix("origin/")
    else:
        default_branch = hint or "main"

    # Ensure the default branch actually exists (local or on origin)
    local_check = _run(
        ["git", "rev-parse", "--verify", f"refs/heads/{default_branch}"],
        cwd=repo_dir,
    )
    has_local = local_check.returncode == 0

    has_remote = False
    if not has_local:
        remote_check = _run(
            ["git", "rev-parse", "--verify", f"refs/remotes/origin/{default_branch}"],
            cwd=repo_dir,
        )
        has_remote = remote_check.returncode == 0
        if not has_remote:
            # Default branch cannot be resolved; skip cleanup for this repo.
            return 0

    if has_local:
        checkout_result = _run(
            ["git", "checkout", default_branch, "--quiet"],
            cwd=repo_dir,
        )
    else:
        # Branch exists only on the remote — create a local tracking branch.
        checkout_result = _run(
            [
                "git",
                "checkout",
                "-b",
                default_branch,
                "--track",
                f"origin/{default_branch}",
                "--quiet",
            ],
            cwd=repo_dir,
        )
    if checkout_result.returncode != 0:
        # Failed to switch to (or create) the default branch; skip cleanup.
        return 0

    # Use plumbing to list branches merged into the default branch (locale-independent)
    merged_result = _run(
        ["git", "for-each-ref", "--format=%(refname:short)", f"--merged={default_branch}", "refs/heads/"],
        cwd=repo_dir,
    )
    if merged_result.returncode != 0:
        return 0
    pruned = 0
    for branch in merged_result.stdout.splitlines():
        branch = branch.strip()
        if branch and branch != default_branch and _run(["git", "branch", "-d", branch], cwd=repo_dir).returncode == 0:
            pruned += 1
    return pruned


# ---------------------------------------------------------------------------
# Worker — clone a new repo
# ---------------------------------------------------------------------------


def _worker_clone(repo_name: str, owner: str, target_dir: str) -> tuple[str, int]:
    """Clone *owner/repo_name* into *target_dir*. Returns (status_line, 0)."""
    result = _run(["gh", "repo", "clone", f"{owner}/{repo_name}", target_dir, "--", "--quiet"])

    if result.returncode != 0:
        detail = result.stdout.strip()
        return (
            f" {ICON_FAIL} {_c(_RED, repo_name + '  clone failed')}\n   {_c(_RED, 'ERROR:')} {detail}",
            0,
        )

    # Apply filemode fix immediately after clone
    _run(["git", "config", "core.filemode", "false"], cwd=target_dir)
    return f" {ICON_NEW} {_c(_YELLOW, repo_name + '  cloned successfully')}", 0


# ---------------------------------------------------------------------------
# Per-repo dispatcher
# ---------------------------------------------------------------------------


def process_repo(
    repo_name: str,
    owner: str,
    root_dir: str,
    *,
    cleanup: bool,
    default_branch_hint: str,
) -> tuple[str, int]:
    """Return (status_line, branches_pruned)."""
    target_dir = os.path.join(root_dir, _sanitize_dirname(owner), _sanitize_dirname(repo_name))
    if os.path.isdir(target_dir):
        return _worker_update(target_dir, cleanup=cleanup, default_branch_hint=default_branch_hint)
    return _worker_clone(repo_name, owner, target_dir)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    """argparse type that accepts only integers >= 1."""
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"argument value '{value}' must be >= 1")
    return ivalue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pfleet",
        description="pFleet — sync all GitHub repos for one or more users. Requires Python 3.10+.",
    )
    parser.add_argument(
        "users",
        nargs="+",
        metavar="USER",
        help="GitHub username(s) to sync",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=_positive_int,
        default=100,
        metavar="N",
        help="Maximum repos to fetch per user (default: 100, must be >= 1)",
    )
    parser.add_argument(
        "--threads",
        "-t",
        type=_positive_int,
        default=4,
        metavar="N",
        help="Maximum concurrent worker threads (default: 4, must be >= 1)",
    )
    cleanup_group = parser.add_mutually_exclusive_group()
    cleanup_group.add_argument(
        "--cleanup",
        "-c",
        dest="cleanup",
        action="store_true",
        default=None,
        help="Delete merged local branches (skips interactive prompt)",
    )
    cleanup_group.add_argument(
        "--no-cleanup",
        dest="cleanup",
        action="store_false",
        help="Skip merged-branch cleanup (skips interactive prompt)",
    )
    parser.add_argument(
        "--dir",
        "-d",
        default=os.getcwd(),
        metavar="DIR",
        help="Root directory for repos (default: current directory)",
    )
    parser.add_argument(
        "--default-branch",
        default="main",
        metavar="BRANCH",
        help="Fallback default branch name for cleanup (default: main)",
    )
    return parser.parse_args()


def ask_cleanup() -> bool:
    """Interactive prompt; returns True if the user chooses yes."""
    try:
        answer = input(" Clean merged local branches across ALL repos? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except EOFError:
        return False


def _check_gh() -> bool:
    """Return True if `gh` is installed and authenticated, False otherwise. Prints a friendly error message on failure."""
    result = _run(["gh", "auth", "status"])
    if result.returncode != 0:
        print(
            f"{ICON_FAIL} {_c(_RED, 'GitHub CLI (gh) is not installed or not authenticated.')}",
            file=sys.stderr,
        )
        print(
            f"  {_c(_DIM, 'Install: https://cli.github.com  |  Authenticate: gh auth login')}",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:  # noqa: C901 pylint: disable=too-many-branches,too-many-statements
    # Main has clear linear phases: preflight → banner → cleanup decision → process users → footer
    args = parse_args()

    # ------------------------------------------------------------------
    # Pre-flight: verify gh is installed and authenticated
    # ------------------------------------------------------------------
    if not _check_gh():
        return 1

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------
    print()
    print(f"  {_c(_BOLD, 'pFleet — GitHub Multi-User Sync (multi-threaded)')}")
    print(f"  {DIVIDER}")
    print()

    # ------------------------------------------------------------------
    # Cleanup decision
    # ------------------------------------------------------------------
    if args.cleanup is None:
        do_cleanup = ask_cleanup()
    else:
        do_cleanup = args.cleanup

    if do_cleanup:
        print(f"  {_c(_DIM, 'Branch cleanup: enabled')}")
    else:
        print(f"  {_c(_DIM, 'Branch cleanup: disabled')}")
    print()

    root_dir = os.path.abspath(args.dir)
    try:
        os.makedirs(root_dir, exist_ok=True)
    except OSError as exc:
        print(f"  {ICON_FAIL} {_c(_RED, 'Cannot use root directory: ' + root_dir)}", file=sys.stderr)
        print(f"   {_c(_RED, str(exc))}", file=sys.stderr)
        return 1
    total_pruned = 0
    overall_ok = True

    # ------------------------------------------------------------------
    # Process each user
    # ------------------------------------------------------------------
    for user in args.users:
        print(f"  {_c(_BOLD, 'User: ' + user)}")
        print(f"  {SUBDIV}")

        # Fetch repo list
        print(f"  {ICON_WAIT} Fetching repo list …", end="\r", flush=True)
        repos, err_msg = _list_repos(user, args.limit)

        if repos is None:
            print(f"\r\033[2K  {ICON_FAIL} {_c(_RED, 'Error fetching repos for ' + user)}\n   {_c(_RED, err_msg)}")
            overall_ok = False
            print()
            continue

        repo_count = len(repos)
        print(f"\r\033[2K  {ICON_DONE} {_c(_GREEN, f'Found {repo_count} repo(s). Spawning threads…')}")

        # Process repos concurrently
        futures = {}
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            for repo in repos:
                future = executor.submit(
                    process_repo,
                    repo,
                    user,
                    root_dir,
                    cleanup=do_cleanup,
                    default_branch_hint=args.default_branch,
                )
                futures[future] = repo

            for future in as_completed(futures):
                try:
                    msg, pruned = future.result()
                    total_pruned += pruned
                    if ICON_FAIL in msg:
                        overall_ok = False
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    repo = futures[future]
                    overall_ok = False
                    msg = f" {ICON_FAIL} {_c(_RED, repo + '  unexpected error: ' + str(exc))}"
                    # Emit full traceback to stderr for diagnostics (not shown to user-facing msg)
                    tb = traceback.format_exc()
                    print(tb, file=sys.stderr)
                print(msg)

        print()

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    print(f"  {DIVIDER}")
    if do_cleanup and total_pruned:
        print(f"  {_c(_DIM, f'Total local branches pruned: {total_pruned}')}")

    if overall_ok:
        print(f"  {ICON_DONE} {_c(_GREEN, 'All tasks completed.')}")
    else:
        print(f"  {ICON_FAIL} {_c(_RED, 'Some tasks failed. Exiting with code 1.')}")
    print()

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())