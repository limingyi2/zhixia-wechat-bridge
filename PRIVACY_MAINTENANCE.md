# Repository privacy maintenance (2026-10-16)

The owner requested an account-wide privacy audit before making repositories public.
All remote branches and tags were scanned with Gitleaks 8.30.1 and additional checks
for private keys, literal authentication strings, private emails and personal paths.
Test credentials, checksums and upstream license attribution were reviewed separately.

Vigil conversation backups and real WeChat configuration were removed from tracked
history. Custom private persona files were removed from the private-config repository;
the remaining preset structure was retained with private biography fields blanked.
Historical credentials and private deployment addresses were replaced with placeholders.
Deployment credentials must come from the environment. UI fixture screenshots remain.

History rewriting changes commit IDs. Use a fresh clone and do not push old branches
or tags back. Private recovery bundles remain outside version control on the owner's
machine. Original installer checksums are not altered by a source history rewrite.

Before publication, live exposed credentials must be revoked or rotated, and release
assets checked independently. GitHub may retain unreachable commits and cached views;
removing those requires GitHub Support. A clean branch scan does not prove cache erasure.
