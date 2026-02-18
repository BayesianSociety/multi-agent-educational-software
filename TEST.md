# How to run tests
Run from the repository root.

```bash
npm --prefix frontend run test
```

# Environments
- Local deterministic environment: Node.js with local repository files.
- Optional containerized variant: start `sqlite` with Docker Compose before running tests.
- Tests are offline and deterministic and must exit with code `0` on pass.
