# Goodwill Pricing Management System

A Streamlit and MySQL application for managing Goodwill pricing guidelines and store-level item pricing decisions.

## Project overview

The system is designed to support two types of users:

- **Administrators** maintain pricing guidelines and review pricing activity.
- **Store staff** search for matching guidelines, review suggested prices, and record final pricing decisions.

The project's existing implementation also includes tag/barcode generation, pricing history, and role-specific dashboards. Features may depend on the current local source version and database configuration.

## Technology

- Python / Streamlit
- MySQL
- Other dependencies are specified by the project's own `requirements.txt`.

## Local setup

1. Clone or download the repository.
2. Create and activate a Python virtual environment.
3. Install dependencies: `pip install -r requirements.txt`.
4. Set up your own private MySQL connection settings; do **not** commit real credentials.
5. Start the application: `streamlit run app.py`.

The source files (`app.py`, business logic, pages, scripts, and any project-specific configuration) must be uploaded from the existing local project folder. This README alone is not a runnable application.

## Security

Never upload `.streamlit/secrets.toml`, `.env`, database credentials, private keys, database exports with sensitive records, or local virtual environments. A `.gitignore` helps command-line Git but **does not prevent manually uploading a secret through GitHub's website**.

## Deployment

The GitHub repository stores source code; it does not automatically host a running Streamlit application. A deployment service must also be configured, including protected database credentials and network access to the MySQL server.
