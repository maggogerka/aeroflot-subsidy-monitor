# Contributing

1. Do not add CAPTCHA bypassing, ticket purchasing, payment, or credential
   collection.
2. Keep selectors semantic and based on visible labels where possible.
3. Treat an ambiguous page as `UNKNOWN`, never as `AVAILABLE`.
4. Add fixtures and tests for detector or configuration changes.
5. Run `python -m pytest -q` and `python -m compileall -q app tests`.
6. Never commit `.env`, `config.yaml`, browser profiles, logs, or artifacts.

The live Aeroflot interface changes independently of this project. Describe the
interface date and the tested subsidy program in selector-related pull requests.
