---
title: Contributing
description: How to contribute to Mailyte — PR process, branch naming, commit conventions, and code review expectations.
---

# Contributing

Thanks for wanting to contribute. Here's how we work.

## PR Process

1. **Pick or create an issue** — check the issue tracker first
2. **Branch off `develop`** — the default working branch; `main` is the release branch — use the naming convention below
3. **Make your changes** — follow the [coding standards](coding-standards.md)
4. **Write/update tests** — PRs without tests for new features will be asked to add them
5. **Open a pull request** — fill out the template
6. **Address review feedback** — iterate until approved
7. **Merge** — squash merge into `develop`

## What CI Checks

Every PR runs `.github/workflows/ci.yml`. It must be green, and several gates are **ratchets** — the tracked number may only improve:

| Gate | What passes |
|------|-------------|
| `ruff format --check .` | Zero formatting diffs |
| `ruff check .` | Violation count ≤ `ruff-baseline.txt` (shrink the baseline when you fix violations) |
| `scripts/run_mypy.sh \| mypy-baseline filter` | No mypy errors beyond `mypy-baseline.txt` |
| Unit tests + coverage | Total line coverage ≥ `coverage-baseline.txt`; changed lines ≥ 60% (`diff-cover` vs `origin/develop`) |
| OpenAPI contract | Committed `openapi.json` matches the live spec; untyped-response count ≤ `openapi-untyped-baseline.txt` |
| Alembic migrations | `alembic upgrade head` reaches head on a fresh DB, is idempotent, and survives seeded data through downgrade/re-upgrade |
| Compose validation | `docker compose config` passes for base + prod + dev file combinations |
| Security scan | Bandit + pip-audit (advisory) |

See [Coding Standards](coding-standards.md) for how to run the lint/type gates locally and when to update the baseline files.

## Branch Naming

```
feature/short-description     # New feature
fix/issue-or-bug-description  # Bug fix
chore/task-description        # Maintenance, deps, CI
docs/what-changed             # Documentation only
refactor/what-changed         # Code restructuring
```

Examples:

- `feature/mailbox-export-api`
- `fix/dkim-key-rotation-crash`
- `chore/upgrade-mysql-exporter`
- `docs/add-scaling-guide`

## Commit Conventions

We use conventional commits. The format:

```
type: short description

Optional longer explanation.
```

Types:

| Type | When to use |
|------|------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that doesn't fix a bug or add a feature |
| `test` | Adding or updating tests |
| `chore` | Build, CI, dependency updates |
| `perf` | Performance improvement |

Examples:

```
feat: add mailbox export endpoint
fix: prevent DKIM crash when key file is missing
docs: add SendGrid migration guide
refactor: extract email validation into shared module
test: add integration tests for webhook delivery
chore: update Postfix base image to 3.8
```

Keep the first line under 72 characters. If you need more detail, add a blank line and then a longer description.

## Code Review

Every PR needs at least one approval before merging. Here's what reviewers look for:

- **Does it work?** — Does the feature actually do what it claims?
- **Tests** — Are there tests? Do they cover the important cases?
- **Error handling** — What happens when things fail?
- **Security** — Any new inputs validated? Secrets exposed? SQL injection?
- **Performance** — Will this cause issues at scale? N+1 queries? Missing indexes?
- **Docs** — If user-facing, are the docs updated?
- **Style** — Does it follow our coding standards?

### Responding to Feedback

- **Don't take it personally** — reviews are about the code, not you
- **Address every comment** — either fix it or explain why you disagree
- **Push new commits** — don't force-push during review (it breaks comment threads)

## What Makes a Good PR

- **Small and focused** — one feature or fix per PR
- **Good description** — explain what changed and why
- **Self-reviewed** — look at your own diff before requesting review
- **No unrelated changes** — don't refactor something unrelated in the same PR
- **Tests pass** — CI must be green

### PR Template

```markdown
## What

Brief description of the change.

## Why

What problem does this solve? Link to issue if applicable.

## How

How does this work? Any design decisions worth noting?

## Testing

How did you test this? What should reviewers check?

## Checklist

- [ ] Tests added/updated
- [ ] Docs updated (if user-facing)
- [ ] No secrets committed
- [ ] Migration included (if DB changes)
- [ ] Baseline files updated if a ratchet count dropped
```

## Development Setup

See [Getting Started](getting-started.md) for the full dev environment setup.

## Questions?

Open an issue or ask in the team channel. There are no stupid questions — email infrastructure is genuinely complex, and nobody knows all of it.
