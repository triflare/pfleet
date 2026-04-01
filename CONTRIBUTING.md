# Contributing to pFleet

Thank you for your interest in contributing to pFleet!

## Getting started

1. Fork the repository and clone your fork.
2. Create a feature branch: `git checkout -b my-feature`.
3. Make your changes, keeping commits focused and descriptive.
4. Open a pull request against `main`.

## Prerequisites

- Python 3.10 or newer
- GitHub CLI (`gh`) installed and authenticated (`gh auth login`)
- Git

## Code style

pFleet uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.
Run the following checks before opening a pull request:

```bash
pip install ruff
ruff check pfleet.py
ruff format --check pfleet.py
```

## Reporting bugs

Open an issue and include:

- A short, descriptive title.
- Steps to reproduce the problem.
- Expected vs. actual behaviour.
- Your OS, Python version (`python --version`), Git version (`git --version`), and `gh` version (`gh --version`).

## Suggesting features

Open an issue labelled _enhancement_ and describe:

- The problem you are trying to solve.
- Your proposed solution.
- Any alternatives you considered.

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating you agree to abide by its terms.
