"""One-time, hashed, expiring password links and mandatory first-login change.

All timestamps are compared using MySQL UTC_TIMESTAMP(); the raw token is never
stored in the database. Password changes are one transaction with token burn.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

import bcrypt

from auth import hash_password
from database import get_connection
from email_delivery import send_account_email

LOG = logging.getLogger(__name__)
INVITE_SECONDS = 24 * 60 * 60
RESET_SECONDS = 30 * 60
COOLDOWN_SECONDS = 5 * 60


class PasswordRecoveryError(ValueError):
    """Safe-to-display error; do not expose whether an email exists."""


def _connect(factory=None):
    return (factory or get_connection)()


def _validated_password(value):
    if not isinstance(value, str) or not value.strip():
        raise PasswordRecoveryError('Enter a non-empty password.')
    if len(value.encode('utf-8')) > 72:
        raise PasswordRecoveryError('Password must be at most 72 UTF-8 bytes.')
    return value


def _digest(token: str) -> str:
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        raise PasswordRecoveryError('This link is invalid or expired.')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _close(conn, cur):
    if cur is not None:
        cur.close()
    conn.close()


def _send_link(user_id: int, *, purpose: str, temporary_password=None,
               connection_factory=None, email_sender=None) -> bool:
    """Send a link. Only mark it redeemable after SMTP acknowledges delivery.

    If SMTP fails, previously sent tokens remain usable. Intended as an
    application-internal API, not an unprotected browser endpoint.
    """
    if purpose not in ('invite', 'reset') or type(user_id) is not int or user_id <= 0:
        raise PasswordRecoveryError('Invalid link request.')
    send = email_sender or send_account_email
    token = secrets.token_urlsafe(32)
    token_hash = _digest(token)
    conn = _connect(connection_factory)
    cur = None
    account = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        conn.start_transaction()
        cur.execute('''SELECT User_Id, User_Email, User_First_Name, User_Is_Active
            FROM USER_ACCOUNT WHERE User_Id=%s LIMIT 1 FOR UPDATE''', (user_id,))
        account = cur.fetchone()
        if not account or not bool(account['User_Is_Active']):
            conn.rollback()
            return False
        if purpose == 'reset':
            cur.execute('''SELECT COUNT(*) AS recent FROM USER_PASSWORD_TOKEN
                WHERE User_Id=%s AND Sent_At >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s SECOND)''',
                        (user_id, COOLDOWN_SECONDS))
            if int(cur.fetchone()['recent']):
                conn.rollback()
                return False
        cur.execute('''INSERT INTO USER_PASSWORD_TOKEN
            (User_Id, Token_Hash, Purpose, Created_At, Expires_At, Sent_At)
            VALUES (%s, %s, %s, UTC_TIMESTAMP(),
                    DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND), NULL)''',
            (user_id, token_hash, purpose,
             INVITE_SECONDS if purpose == 'invite' else RESET_SECONDS))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _close(conn, cur)

    try:
        send(to_email=account['User_Email'], first_name=account['User_First_Name'],
             token=token, purpose=purpose, temporary_password=temporary_password)
    except Exception:
        # Do not log the raw token or the temporary password.
        LOG.exception('Goodwill account email delivery failed for user_id=%s', user_id)
        _discard_unsent_token(token_hash, connection_factory=connection_factory)
        return False

    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        conn.start_transaction()
        # Revoke older sent links only once the replacement has been delivered.
        cur.execute('''UPDATE USER_PASSWORD_TOKEN SET Consumed_At=UTC_TIMESTAMP()
            WHERE User_Id=%s AND Token_Hash<>%s
              AND Sent_At IS NOT NULL AND Consumed_At IS NULL''', (user_id, token_hash))
        cur.execute('''UPDATE USER_PASSWORD_TOKEN SET Sent_At=UTC_TIMESTAMP()
            WHERE Token_Hash=%s AND Sent_At IS NULL AND Consumed_At IS NULL''',
            (token_hash,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        _close(conn, cur)


def _discard_unsent_token(token_hash, *, connection_factory=None):
    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute('DELETE FROM USER_PASSWORD_TOKEN WHERE Token_Hash=%s AND Sent_At IS NULL',
                    (token_hash,))
        conn.commit()
    except Exception:
        conn.rollback()
        LOG.exception('Could not discard unsent password token')
    finally:
        _close(conn, cur)


def send_new_user_invitation(user_id: int, temporary_password: str,
                             *, connection_factory=None, email_sender=None) -> bool:
    """Called only after the authorized account-create transaction has committed."""
    return _send_link(user_id, purpose='invite', temporary_password=temporary_password,
                      connection_factory=connection_factory, email_sender=email_sender)


def request_password_reset(email: str, *, connection_factory=None, email_sender=None) -> None:
    """Public endpoint: always returns None, whether account exists or not."""
    if not isinstance(email, str) or len(email) > 100 or '\n' in email or '\r' in email:
        return
    address = email.strip().lower()
    if '@' not in address:
        return
    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute('''SELECT User_Id FROM USER_ACCOUNT
            WHERE LOWER(User_Email)=LOWER(%s) AND User_Is_Active=1 LIMIT 2''', (address,))
        rows = cur.fetchall()
    finally:
        _close(conn, cur)
    if len(rows) == 1:
        _send_link(int(rows[0]['User_Id']), purpose='reset',
                   connection_factory=connection_factory, email_sender=email_sender)


def send_admin_reset_link(actor: dict, target_id: int, *, connection_factory=None,
                          email_sender=None) -> bool:
    """Admin-only resend. Enforces live management access in MySQL."""
    from users_service import has_management_access, _read_actor
    if not has_management_access(actor) or type(target_id) is not int or target_id < 1:
        raise PermissionError('Administrator management permission required.')
    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        _read_actor(cur, actor)
        cur.execute('SELECT User_Id FROM USER_ACCOUNT WHERE User_Id=%s AND User_Is_Active=1',
                    (target_id,))
        account = cur.fetchone()
    finally:
        _close(conn, cur)
    if not account:
        raise PasswordRecoveryError('Account is inactive or unavailable.')
    return _send_link(target_id, purpose='reset',
                      connection_factory=connection_factory, email_sender=email_sender)


def complete_password_reset(raw_token: str, new_password: str,
                            *, connection_factory=None) -> None:
    """Atomically update password and consume all user tokens."""
    token_hash = _digest(raw_token)
    new_hash = hash_password(_validated_password(new_password))
    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        conn.start_transaction()
        cur.execute('''SELECT t.User_Id FROM USER_PASSWORD_TOKEN t
            JOIN USER_ACCOUNT u ON u.User_Id=t.User_Id
            WHERE t.Token_Hash=%s AND t.Consumed_At IS NULL
              AND t.Sent_At IS NOT NULL AND t.Expires_At > UTC_TIMESTAMP()
              AND u.User_Is_Active=1 LIMIT 1 FOR UPDATE''', (token_hash,))
        row = cur.fetchone()
        if row is None:
            raise PasswordRecoveryError('This link is invalid, expired, or already used.')
        user_id = int(row['User_Id'])
        cur.execute('UPDATE USER_ACCOUNT SET User_Password_Hash=%s WHERE User_Id=%s',
                    (new_hash, user_id))
        cur.execute('''INSERT INTO USER_PASSWORD_STATE
            (User_Id, Must_Change_Password, Temp_Password_Expires_At, Session_Version)
            VALUES (%s, 0, NULL, 1)
            ON DUPLICATE KEY UPDATE Must_Change_Password=0,
                                    Temp_Password_Expires_At=NULL,
                                    Session_Version=Session_Version+1''', (user_id,))
        cur.execute('''UPDATE USER_PASSWORD_TOKEN SET Consumed_At=UTC_TIMESTAMP()
            WHERE User_Id=%s AND Consumed_At IS NULL''', (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _close(conn, cur)


def change_temporary_password(user_id: int, current_password: str,
                              new_password: str, *, connection_factory=None) -> None:
    """Forced first-login flow; cannot bypass old-password validation."""
    if type(user_id) is not int or user_id <= 0:
        raise PasswordRecoveryError('Invalid account.')
    new_password = _validated_password(new_password)
    if not isinstance(current_password, str) or not current_password:
        raise PasswordRecoveryError('Enter your temporary password.')
    # Hash outside transaction to minimize lock duration.
    new_hash = hash_password(new_password)
    conn = _connect(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        conn.start_transaction()
        cur.execute('''SELECT u.User_Password_Hash FROM USER_ACCOUNT u
            JOIN USER_PASSWORD_STATE s ON s.User_Id=u.User_Id
            WHERE u.User_Id=%s AND u.User_Is_Active=1
              AND s.Must_Change_Password=1
              AND (s.Temp_Password_Expires_At IS NULL
                   OR s.Temp_Password_Expires_At > UTC_TIMESTAMP())
            LIMIT 1 FOR UPDATE''', (user_id,))
        row = cur.fetchone()
        matches = False
        if row:
            try:
                matches = bcrypt.checkpw(current_password.encode('utf-8'),
                                         row['User_Password_Hash'].encode('utf-8'))
            except (ValueError, TypeError, AttributeError):
                matches = False
        if not matches:
            raise PasswordRecoveryError('Temporary password is invalid or expired.')
        cur.execute('UPDATE USER_ACCOUNT SET User_Password_Hash=%s WHERE User_Id=%s',
                    (new_hash, user_id))
        cur.execute('''UPDATE USER_PASSWORD_STATE SET Must_Change_Password=0,
            Temp_Password_Expires_At=NULL,
            Session_Version=Session_Version+1 WHERE User_Id=%s''', (user_id,))
        cur.execute('''UPDATE USER_PASSWORD_TOKEN SET Consumed_At=UTC_TIMESTAMP()
            WHERE User_Id=%s AND Consumed_At IS NULL''', (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _close(conn, cur)
