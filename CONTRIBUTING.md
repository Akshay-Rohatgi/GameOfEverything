# Contributing to Game of Everything v2

Use a focused branch and conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, or `chore:`). Keep changes scoped to the v2 codebase in `goe/` and its supporting atoms, Docker images, fixtures, and tests.

Before submitting a change, run:

```bash
uv run pytest -m "not docker and not llm"
```

Changes that require Docker or Amazon Bedrock should include the relevant verification steps and note any credentials or services they require.
