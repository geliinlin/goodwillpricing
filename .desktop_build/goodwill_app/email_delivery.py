"""SMTP email delivery for Goodwill account setup/recovery.

No SMTP credentials or reset tokens are logged. Configure either the [email]
section of Streamlit secrets or EMAIL_* environment variables. TLS is required.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import parseaddr
import os
import smtplib
import ssl
from urllib.parse import urlsplit, urlencode


class EmailConfigurationError(RuntimeError):
    pass


def _email_config() -> dict:
    # Treat presence of ANY SMTP environment variable as opting into env mode.
    names = ('EMAIL_SMTP_HOST', 'EMAIL_SMTP_PORT', 'EMAIL_SMTP_USER',
             'EMAIL_SMTP_PASSWORD', 'EMAIL_FROM_ADDRESS', 'APP_BASE_URL')
    if any(key in os.environ for key in names):
        d = dict(host=os.environ.get('EMAIL_SMTP_HOST'),
                 port=os.environ.get('EMAIL_SMTP_PORT', '587'),
                 username=os.environ.get('EMAIL_SMTP_USER'),
                 password=os.environ.get('EMAIL_SMTP_PASSWORD'),
                 from_address=os.environ.get('EMAIL_FROM_ADDRESS'),
                 security=os.environ.get('EMAIL_SMTP_SECURITY', 'starttls'),
                 base_url=os.environ.get('APP_BASE_URL'))
    else:
        try:
            import streamlit as st
            mail = dict(st.secrets['email'])
            app = dict(st.secrets['app'])
            d = dict(host=mail.get('host'), port=mail.get('port', 587),
                     username=mail.get('username'), password=mail.get('password'),
                     from_address=mail.get('from_address'),
                     security=mail.get('security', 'starttls'),
                     base_url=app.get('base_url'))
        except (KeyError, FileNotFoundError, AttributeError, TypeError) as exc:
            raise EmailConfigurationError('Email service is not configured.') from exc
    if not all(d.get(key) for key in ('host', 'username', 'password', 'from_address', 'base_url')):
        raise EmailConfigurationError('Incomplete email configuration.')
    try:
        d['port'] = int(d['port'])
        if not 1 <= d['port'] <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise EmailConfigurationError('Invalid email server port.') from None
    if d['security'] not in ('ssl', 'starttls'):
        raise EmailConfigurationError('SMTP requires SSL or STARTTLS.')
    base = str(d['base_url']).rstrip('/')
    parts = urlsplit(base)
    local = parts.hostname in ('localhost', '127.0.0.1', '::1')
    if ((parts.scheme != 'https' and not (parts.scheme == 'http' and local))
            or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment):
        raise EmailConfigurationError('APP_BASE_URL must be HTTPS (except localhost).')
    d['base_url'] = base
    return d


def _valid_address(address: str) -> bool:
    return (isinstance(address, str) and '\r' not in address and '\n' not in address
            and parseaddr(address)[1] == address and address.count('@') == 1)


def make_reset_url(token: str, config: dict | None = None) -> str:
    cfg = config if config is not None else _email_config()
    return cfg['base_url'] + '/?' + urlencode({'reset_token': token})


def send_account_email(*, to_email: str, first_name: str, token: str,
                       purpose: str, temporary_password: str | None = None) -> None:
    """Send a one-time setup/reset URL; only invitations include temporary passwords."""
    if purpose not in ('invite', 'reset') or not _valid_address(to_email):
        raise ValueError('Invalid email recipient or purpose.')
    cfg = _email_config()
    if not _valid_address(cfg['from_address']):
        raise EmailConfigurationError('Invalid sender email.')
    link = make_reset_url(token, cfg)
    msg = EmailMessage()
    msg['From'] = cfg['from_address']
    msg['To'] = to_email
    msg['Subject'] = ('Your Goodwill account invitation' if purpose == 'invite'
                      else 'Reset your Goodwill password')
    name = (first_name or 'there').replace('\r', '').replace('\n', ' ').strip()
    if purpose == 'invite':
        body = (f'Hello {name},\n\nYour Goodwill Pricing account has been created.\n'
                'Use this one-time link to choose your own password (valid for 24 hours):\n'
                f'{link}\n\n')
        if temporary_password:
            body += ('Temporary initial password (expires after 24 hours):\n'
                     f'{temporary_password}\n\n'
                     'If you log in with this temporary password, you must change it '
                     'before accessing the system. Using the link is recommended.\n\n')
    else:
        body = (f'Hello {name},\n\nWe received a Goodwill password reset request.\n'
                'Use this one-time link within 30 minutes:\n'
                f'{link}\n\n')
    body += ('If you did not request this, you can ignore the message. '
             'Do not forward the link or your password.\n\nGoodwill Pricing Support\n')
    msg.set_content(body)
    context = ssl.create_default_context()
    if cfg['security'] == 'ssl':
        with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=15,
                              context=context) as server:
            server.login(cfg['username'], cfg['password'])
            server.send_message(msg)
    else:
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(cfg['username'], cfg['password'])
            server.send_message(msg)
