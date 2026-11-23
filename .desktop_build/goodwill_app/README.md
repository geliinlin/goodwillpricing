# Goodwill Pricing Decision Support System

> **Reconstructed README (2026-11-23).** The original `README.md` is not available in the supplied snapshots. This document was recreated from the project's confirmed module structure and recent development history; it is not a byte-for-byte copy of the earlier README.

## Overview

Streamlit + MySQL application for Goodwill donation pricing. Head-office administrators manage pricing guidelines, review performance reports and manage accounts. Store staff look up pricing guidance, submit final pricing decisions and create item labels.

## Current modules

| Module | Main files | Purpose |
| --- | --- | --- |
| Authentication | `app.py`, `auth.py`, `password_recovery.py` | Login, initial password setup, password reset |
| Navigation and dashboards | `dashboard_router.py`, `dashboard_page.py`, `dashboard_data.py` | Admin and Store Staff navigation and KPI views |
| Guidelines | `guideline_page.py`, `guideline_table.py`, `guidelines.py` | Guideline search, filtering, versioned edits, duplicate detection |
| Pricing | `item_search_page.py`, `item_search.py`, `pricing_decision.py` | Attribute search, final price, override reason, tag and print workflow |
| Reports | `reports_page.py`, `reports_data.py` | Filtered metrics, charts, staff activity and PDF export |
| Users | `users_page.py`, `users_service.py`, `email_delivery.py` | Admin-only user management, invitation email, profile changes and duplicate checks |
| Configuration | `configuration_page.py`, `configuration_service.py` | Master data for item types, categories, brands, conditions and branches |

`styles/` holds shared and per-page CSS. `tests/` contains offline unit tests. `scripts/` contains optional maintenance tools. `migrations/` holds incremental MySQL schema updates.

## Local setup

1. Use Python 3.12+ and a virtual environment. Install the project dependencies from `requirements.txt`.
2. Configure the real MySQL connection privately in `.streamlit/secrets.toml` or via supported environment variables. Keep SMTP credentials and any public app URL private as appropriate.
3. Ensure your database has the expected schema and any necessary incremental migrations **before** running the app. Check the migration status rather than blindly re-running SQL scripts.
4. Start from the project root:

```bash
python -m unittest discover -s tests -v
streamlit run app.py
```

For configuration schema checks:

```bash
python -m scripts.check_configuration_schema
```

## Access controls

- Store Staff: Dashboard, Price an Item and History. No Users or Configuration access.
- Administrator: Admin Dashboard, Guidelines and Reports. Users and Configuration require separately granted management access.
- Access restrictions must be validated in the backend, not only by hiding navigation items.

## Migration files

- `migrations/001_user_management_access.sql`
- `migrations/002_account_email_recovery.sql`
- `migrations/003_master_data_management.sql`

These are incremental schema changes. **Do not rerun the original G3 schema-creation script over an existing populated database.** Back up data and confirm selected schema first.

## Current limitations

- Historical Reports branch attribution relies on staff's currently assigned branch unless a historical branch snapshot is later added to pricing decisions.
- Actual email delivery depends on SMTP configuration, recipient server acceptance and a reachable public URL for password links.
- Unit tests do not replace real MySQL integration tests or browser UI tests.

## Security

Never commit `.streamlit/secrets.toml`, `.env`, database passwords, email application passwords, database exports, or private certificates to a public repository. Keep the local `.venv/`, `.idea/` and `__pycache__/` folders out of source control.
