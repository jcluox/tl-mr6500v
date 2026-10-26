# Security Policy

## What this project handles

TL-MR6500v logs in to your router and reads your SMS. That means it deals with your **router password**, your **SMS text** (which may include one-time login codes), and the **tokens** for Telegram or LINE.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private reporting instead: open the **Security** tab of this repository and choose **Report a vulnerability**. Describe what you found and how to reproduce it. I will answer as soon as I can, and I will credit you in the fix if you want.

## Supported versions

Only the latest release (or the `main` branch) gets security fixes.

## Safe use

- Keep the Dashboard on a trusted network. It uses plain HTTP, so do not expose its port to the internet. If you need remote access, put it behind an HTTPS reverse proxy or a VPN.
- Set a strong, unique `ADMIN_PASSWORD`. The app refuses to start if it is empty or still the example value.
- Protect `.env` and the `data/` folder. They hold your passwords and your SMS history in plain text.
- If a token or password leaks, change it right away (BotFather for Telegram, the LINE console for LINE, the router admin page for the router).
