# Contributing

Thanks for helping! This project is small, so the rules are simple.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. Node.js is optional; one test uses it to check the Dashboard translations.

```bash
uv sync
cp .env.example .env     # fill in TPLINK_PASSWORD and ADMIN_PASSWORD
uv run pytest
uv run uvicorn tl_mr6500v.main:app --reload --port 8000
```

With Docker, `docker compose watch` syncs code changes into the container.

## Before you open a pull request

- Run `uv run pytest`. New behavior needs a test.
- Keep the change small and focused. Match the style of the code around it.
- If you change what users see, update the README too.

## Common changes

- **A new provider** (for example Discord or email): see "Add a provider" in the README.
- **A new Dashboard language**: add it to `LANGUAGES` and `STRINGS` in `src/tl_mr6500v/static/i18n.js`. A test checks that it has every key that English has.
- **Support for another router model**: the router code is in `src/tl_mr6500v/router.py`. Please say which model and firmware you tested.

## Reporting problems

Open an issue and include your router model and firmware, how you run the app (Docker or not), and the relevant lines from the App Log.

**Never paste real passwords, tokens, phone numbers, or SMS text.** Remove or blur them first. This includes screenshots of the Dashboard.

To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
