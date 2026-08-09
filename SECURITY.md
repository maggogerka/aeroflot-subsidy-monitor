# Security

Do not publish `.env`, `config.yaml`, browser profiles, cookies, logs,
screenshots, or monitor state. They are excluded by `.gitignore`.

If a Telegram token is accidentally committed, revoke it immediately through
`@BotFather`, create a new token, and remove the secret from the full Git
history before pushing again.

The application never needs passport, payment-card, or Aeroflot account
credentials. Do not add them to configuration or diagnostic files.

To report a security issue, open a private GitHub security advisory instead of
a public issue containing secrets.
