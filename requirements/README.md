# Reproducible Python dependencies

The committed lock files target Python 3.12 and are the only dependency inputs
used by the container build:

- `runtime.lock` contains the complete application runtime graph;
- `dev.lock` contains the runtime graph and all tools from the `dev` extra;
- `build.lock` contains the exact PEP 517 build tools.

Every resolved distribution is pinned with `==` and protected by one or more
SHA-256 hashes. Regenerate all three files only with Python 3.12 and
`pip-tools==7.5.1`:

```bash
python3.12 -m venv .lock-venv
.lock-venv/bin/python -m pip install 'pip==25.2' 'pip-tools==7.5.1'

CUSTOM_COMPILE_COMMAND='pip-compile --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras --output-file=requirements/runtime.lock pyproject.toml' \
  .lock-venv/bin/pip-compile --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras \
  --output-file=requirements/runtime.lock pyproject.toml

CUSTOM_COMPILE_COMMAND='pip-compile --extra=dev --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras --output-file=requirements/dev.lock pyproject.toml' \
  .lock-venv/bin/pip-compile --extra=dev --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras \
  --output-file=requirements/dev.lock pyproject.toml

CUSTOM_COMPILE_COMMAND='pip-compile --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras --output-file=requirements/build.lock requirements/build.in' \
  .lock-venv/bin/pip-compile --generate-hashes --resolver=backtracking --allow-unsafe --strip-extras \
  --output-file=requirements/build.lock requirements/build.in
```

Review the diff, then verify a clean install before committing regenerated
locks. Do not edit a lock file manually.
