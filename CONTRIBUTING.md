# Contributing

Thank you for your interest in contributing! This guide explains our workflow.

## Prerequisites

- A GitHub account
- Git installed locally
- Familiarity with pull requests

## Workflow

1. **Create a GitHub issue first** — Every change starts with an issue. Describe the bug, feature, or task.
2. **Fork and clone** — Fork the repo and clone your fork locally.
3. **Create a branch** — Branch from `main` using the naming convention:
   ```
   username/issue-XX-description
   ```
   Example: `jameskazie/issue-42-fix-auth-redirect`
4. **Make your changes** — Keep commits focused and atomic.
5. **Push and open a PR** — Push your branch and open a pull request referencing the issue number in the title.
6. **Review** — Address any feedback from reviewers.
7. **Merge** — Once approved, the PR will be merged and the branch deleted.

## Commit Conventions

- Use conventional commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`
- Keep the first line under 72 characters
- Reference the issue number where relevant (e.g., `fix: resolve login redirect (#42)`)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold it.
