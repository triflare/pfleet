# Usage

pFleet syncs all GitHub repositories for one or more users concurrently,
keeping an exact local copy of every branch.

## Prerequisites

| Dependency        | Minimum version    | Notes                                                   |
| ----------------- | ------------------ | ------------------------------------------------------- |
| Python            | 3.10               | Built-in modules only; no third-party packages required |
| Git               | any recent version | Must be on your `PATH`                                  |
| GitHub CLI (`gh`) | any recent version | Must be authenticated — run `gh auth login` first       |

## Installation

### From PyPI (recommended)

```bash
pip install pfleet
```

### From source

```bash
git clone https://github.com/triflare/pfleet.git
cd pfleet
pip install .
```

## Quick start

```bash
pfleet myusername
```

This clones every repository owned by `myusername` into the current directory under an owner subdirectory — each repository is stored at <root>/<owner>/<repo>.
For example, this creates ./<owner>/<repo> (e.g., ./myusername/<repo>) instead of ./<repo>. If a repository already exists locally it will be synced in place.

## CLI reference

```text
usage: pfleet [-h] [--limit N] [--threads N] [--cleanup | --no-cleanup]
              [--dir DIR] [--default-branch BRANCH]
              USER [USER ...]
```

### Positional arguments

| Argument | Description                          |
| -------- | ------------------------------------ |
| `USER`   | One or more GitHub usernames to sync |

### Options

| Flag                      | Default            | Description                                      |
| ------------------------- | ------------------ | ------------------------------------------------ |
| `--limit N`, `-l N`       | `100`              | Maximum repositories to fetch per user           |
| `--threads N`, `-t N`     | `4`                | Maximum concurrent worker threads                |
| `--cleanup`, `-c`         | interactive prompt | Delete merged local branches after syncing       |
| `--no-cleanup`            | interactive prompt | Skip merged-branch cleanup                       |
| `--dir DIR`, `-d DIR`     | current directory  | Root directory where repositories are stored     |
| `--default-branch BRANCH` | `main`             | Fallback default branch name used during cleanup |

## Examples

Sync repositories for a single user:

```bash
pfleet octocat
```

Sync multiple users into a shared directory:

```bash
pfleet alice bob --dir ~/repos
```

Sync with branch cleanup enabled, using 8 threads:

```bash
pfleet myusername --cleanup --threads 8
```

Limit the number of repositories fetched per user:

```bash
pfleet myusername --limit 50
```

## How it works

1. **Repo discovery** — pFleet calls `gh repo list <user>` to obtain the full
   list of repositories.
2. **Concurrent processing** — each repository is dispatched to a thread pool.
   - If the repository directory **does not exist**, the repo is cloned via
     `gh repo clone`.
   - If the repository directory **already exists**, pFleet fetches all remote
     branches and resets every local branch to match.
3. **Branch cleanup** (optional) — after syncing, local branches that have been
   merged into the default branch are deleted from every repository.

## Status icons

| Icon       | Meaning                                 |
| ---------- | --------------------------------------- |
| `[ DONE ]` | Operation succeeded                     |
| `[ SYNC ]` | Existing repository synced              |
| `[ NEW  ]` | Repository cloned for the first time    |
| `[ SKIP ]` | Repository skipped (dirty working tree) |
| `[ FAIL ]` | Operation failed                        |

## Notes

- pFleet determines whether a working tree is dirty by running `git status --porcelain` (so untracked files will mark the tree dirty). Repositories with a dirty working tree are **skipped** during sync to avoid data loss.
- The implementation writes `core.filemode = false` into a repository's Git config only on Windows (see pfleet.py: _worker_update and _worker_clone where this is set when `sys.platform == "win32"`) to suppress phantom permission-bit diffs.
- The `--limit` flag caps the number of repositories retrieved per user; raise
  it if a user has more than 100 repositories.
