# Security and private data

Money Keeper processes financial documents and should be deployed as a private,
self-hosted service.

- Never commit `.env`, credentials, private keys, bank statements, database dumps,
  transaction exports, or Telegram user IDs.
- Use a long random `API_ADMIN_TOKEN` and restrict `CORS_ALLOWED_ORIGINS` in deployed
  environments.
- Restrict database access and use a dedicated database role with the minimum
  required privileges.
- Rotate a credential immediately if it is ever committed, even if the commit is
  later removed.
- Review imported records before using reports for financial decisions.

If you discover a vulnerability in your deployment, rotate affected credentials
first and then investigate logs and database access.
