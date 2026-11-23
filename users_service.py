"""Server-enforced user management for Goodwill.

Users + Configuration is an Administrator-only capability. Every operation
rechecks MySQL state and the Administrator subtype; session flags never
authorize mutations. Store Staff can never use the management service.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
import secrets
from typing import Any, Callable

import mysql.connector

from auth import hash_password
from database import get_connection

LOCK_NAME = 'goodwill_user_management_v1'
ROLE_ADMIN = 'Administrator'
ROLE_STAFF = 'Store Staff'
MAX_USER_ID = 2_147_483_647


class UserManagementError(ValueError):
    """Safe, user-facing validation/business error."""


class UserManagementPermissionError(PermissionError):
    """Caller lacks freshly verified permissions."""


def has_management_access(user: dict | None) -> bool:
    """Navigation hint ONLY; DB revalidation is required for data access."""
    return bool(user and user.get('user_type') == ROLE_ADMIN
                and user.get('can_manage_users_config') is True)


def _connection(factory: Callable | None = None):
    return (factory or get_connection)()


def _read_actor(cur, actor: dict) -> str:
    uid = actor.get('user_id') if isinstance(actor, dict) else None
    role = actor.get('user_type') if isinstance(actor, dict) else None
    if type(uid) is not int or uid <= 0 or role != ROLE_ADMIN:
        raise UserManagementPermissionError('Users and Configuration access required.')
    cur.execute('''
        SELECT u.User_Type, u.User_Is_Active, a.Admin_Id, s.Staff_Id,
               p.Can_Manage_Users_Config AS can_manage,
               COALESCE(ps.Must_Change_Password, 0) AS must_change
        FROM USER_ACCOUNT u
        LEFT JOIN ADMINISTRATOR a ON a.Admin_Id=u.User_Id
        LEFT JOIN STORE_STAFF s ON s.Staff_Id=u.User_Id
        LEFT JOIN USER_MODULE_ACCESS p ON p.User_Id=a.Admin_Id
        LEFT JOIN USER_PASSWORD_STATE ps ON ps.User_Id=u.User_Id
        WHERE u.User_Id=%s LIMIT 1
    ''', (uid,))
    row = cur.fetchone()
    if (not row or row['User_Type'] != role or not bool(row['User_Is_Active'])
        or not bool(row.get('can_manage')) or bool(row.get('must_change'))
        or row['Admin_Id'] is None):
        raise UserManagementPermissionError('Users and Configuration access required.')
    return role


def _validate_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise UserManagementError(f'{field} is required.')
    cleaned = ' '.join(value.strip().split())
    if not cleaned or len(cleaned) > limit:
        raise UserManagementError(f'{field} must contain 1–{limit} characters.')
    return cleaned


def _validate_user(first_name, last_name, username, email):
    first = _validate_text(first_name, 'First name', 50)
    last = _validate_text(last_name, 'Last name', 50)
    account = _validate_text(username, 'Username', 50)
    email = _validate_text(email, 'Email address', 100).lower()
    # A conservative validation, not a claim of deliverability.
    if (parseaddr(email)[1] != email or email.count('@') != 1
        or ' ' in email or '.' not in email.split('@')[1]):
        raise UserManagementError('Enter a valid email address.')
    return first, last, account, email



def _match_text(value) -> str:
    """Case- and whitespace-insensitive matching for account previews."""
    return ' '.join(str(value or '').split()).casefold()


def find_account_duplicates(
    rows: list[dict], *, first_name: str, last_name: str,
    username: str, email: str, exclude_user_id: int | None = None,
) -> dict:
    """Preview likely duplicate users from the authorized directory.

    Username/email collisions are hard errors; name-only matches are review
    warnings, never proof that two individuals are the same person. The DB
    rechecks both rules transactionally on save; this preview is advisory.
    """
    name_first, name_last = _match_text(first_name), _match_text(last_name)
    login_name, mailbox = _match_text(username), _match_text(email)
    blocking, similar = [], []
    for row in rows:
        uid = row.get('User_Id')
        if uid == exclude_user_id:
            continue
        fields = []
        if login_name and login_name == _match_text(row.get('User_Username')):
            fields.append('Username')
        if mailbox and mailbox == _match_text(row.get('User_Email')):
            fields.append('Email')
        info = {'user_id': uid,
                'username': str(row.get('User_Username') or ''),
                'active': bool(row.get('User_Is_Active')),
                'fields': tuple(fields)}
        if fields:
            blocking.append(info)
        elif (name_first and name_last
              and name_first == _match_text(row.get('User_First_Name'))
              and name_last == _match_text(row.get('User_Last_Name'))):
            similar.append(info)
    return {'blocking': blocking, 'similar': similar}


def _conflict_message(row: dict | None, username: str, email: str) -> str:
    if not row:
        return 'Username or email is already registered.'
    matches = []
    if _match_text(row.get('User_Username')) == _match_text(username):
        matches.append('Username')
    if _match_text(row.get('User_Email')) == _match_text(email):
        matches.append('Email')
    which = ' and '.join(matches) if matches else 'Username or email'
    uid = row.get('User_Id')
    details = f' (User #{uid})' if type(uid) is int else ''
    return f'{which} already belongs to an existing account{details}. Select that account instead.'


def _require_similar_name_review(cur, first: str, last: str, *,
                                 excluding: int | None,
                                 confirmed: bool) -> None:
    """Acknowledge same-name accounts explicitly, without forbidding namesakes."""
    cur.execute("""SELECT User_Id, User_Username FROM USER_ACCOUNT
        WHERE LOWER(TRIM(User_First_Name))=LOWER(%s)
          AND LOWER(TRIM(User_Last_Name))=LOWER(%s)
          AND (%s IS NULL OR User_Id<>%s)
        ORDER BY User_Id LIMIT 1""", (first, last, excluding, excluding))
    match = cur.fetchone()
    if match and not confirmed:
        uid = match.get('User_Id')
        label = f'User #{uid}' if type(uid) is int else 'an existing account'
        raise UserManagementError(
            f'Potential duplicate account: {label} has the same first and last name. '
            'Review the existing user and confirm that this is a different person before saving.')


def _validate_id(value, label):
    if type(value) is not int or value < 1:
        raise UserManagementError(f'Choose a valid {label}.')
    return value


def load_user_directory(actor: dict, *, connection_factory: Callable | None = None) -> dict:
    """Read all accounts for an authorized Administrator only."""
    if not has_management_access(actor):
        raise UserManagementPermissionError('Users and Configuration access required.')
    conn = _connection(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        role = _read_actor(cur, actor)
        cur.execute('''
            SELECT u.User_Id, u.User_First_Name, u.User_Last_Name, u.User_Username,
                   u.User_Email, u.User_Type, u.User_Is_Active,
                   s.Branch_Id, b.Branch_Name,
                   COALESCE(p.Can_Manage_Users_Config, 0) AS Can_Manage_Users_Config
            FROM USER_ACCOUNT u
            LEFT JOIN STORE_STAFF s ON s.Staff_Id=u.User_Id
            LEFT JOIN BRANCH b ON b.Branch_Id=s.Branch_Id
            LEFT JOIN ADMINISTRATOR a ON a.Admin_Id=u.User_Id
            LEFT JOIN USER_MODULE_ACCESS p ON p.User_Id=a.Admin_Id
            ORDER BY u.User_Id DESC
        ''')
        users = cur.fetchall()
        cur.execute('SELECT Branch_Id, Branch_Name FROM BRANCH WHERE Branch_Is_Active=1 ORDER BY Branch_Name, Branch_Id')
        branches = cur.fetchall()
        return {'users': users, 'branches': branches, 'can_grant_access': True}
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def _execute_write(actor: dict, task: Callable, connection_factory: Callable | None = None):
    """One named lock + transaction; consume all SELECT results before cursor.close."""
    if not has_management_access(actor):
        raise UserManagementPermissionError('Users and Configuration access required.')
    conn = _connection(connection_factory)
    cur = None
    locked = False
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute('SELECT GET_LOCK(%s, 10) AS acquired', (LOCK_NAME,))
        lock_row = cur.fetchone()
        if not lock_row or lock_row.get('acquired') != 1:
            raise UserManagementError('User management is busy. Please retry.')
        locked = True
        conn.start_transaction()
        role = _read_actor(cur, actor)
        result = task(cur, role)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None and locked:
            try:
                cur.execute('SELECT RELEASE_LOCK(%s) AS released', (LOCK_NAME,))
                cur.fetchone()
            except mysql.connector.Error:
                pass
        if cur is not None:
            cur.close()
        conn.close()


def _target(cur, user_id: int) -> dict:
    cur.execute('''
        SELECT u.User_Id, u.User_Type, u.User_Is_Active,
               COALESCE(p.Can_Manage_Users_Config, 0) AS can_manage
        FROM USER_ACCOUNT u
        LEFT JOIN ADMINISTRATOR a ON a.Admin_Id=u.User_Id
        LEFT JOIN USER_MODULE_ACCESS p ON p.User_Id=a.Admin_Id
        WHERE u.User_Id=%s LIMIT 1 FOR UPDATE
    ''', (user_id,))
    row = cur.fetchone()
    if not row:
        raise UserManagementError('User account no longer exists.')
    return row


def _can_modify(actor: dict, actor_role: str, target: dict):
    if actor['user_id'] == target['User_Id']:
        raise UserManagementError('You cannot change your own account here.')
    if actor_role != ROLE_ADMIN:
        raise UserManagementPermissionError('Only authorized Administrators can manage accounts.')


def create_user_account(
    actor: dict, *, first_name: str, last_name: str, username: str,
    email: str, user_type: str, branch_id: int | None,
    active: bool = True, management_access: bool = False,
    connection_factory: Callable | None = None,
    password_factory: Callable | None = None,
    require_password_change: bool = False,
    confirm_similar_name: bool = False,
) -> dict:
    """Create a user and subtype in one transaction; generate password once.

    When require_password_change is true, state is stored in the same transaction.
    The email invitation must be sent only after the transaction has committed.
    """
    first, last, username, email = _validate_user(first_name, last_name, username, email)
    if user_type not in (ROLE_ADMIN, ROLE_STAFF):
        raise UserManagementError('Choose Administrator or Store Staff.')
    if type(require_password_change) is not bool:
        raise UserManagementError('Invalid password setup setting.')
    if type(confirm_similar_name) is not bool:
        raise UserManagementError('Invalid duplicate-account confirmation.')
    if type(active) is not bool or type(management_access) is not bool:
        raise UserManagementError('Invalid account status or access setting.')
    if user_type == ROLE_STAFF:
        if management_access:
            raise UserManagementError('Store Staff cannot receive Users + Configuration access.')
        branch_id = _validate_id(branch_id, 'branch')
    elif branch_id is not None:
        raise UserManagementError('Administrators do not belong to a store branch.')
    password = (password_factory or (lambda: secrets.token_urlsafe(24)))()
    if not isinstance(password, str) or not password or len(password.encode('utf-8')) > 72:
        raise UserManagementError('Unable to generate a valid temporary password.')
    hashed = hash_password(password)

    def write(cur, role):
        if role != ROLE_ADMIN:
            raise UserManagementPermissionError('Only authorized Administrators can create accounts.')
        if branch_id is not None:
            cur.execute('SELECT Branch_Id FROM BRANCH WHERE Branch_Id=%s AND Branch_Is_Active=1 LIMIT 1 LOCK IN SHARE MODE', (branch_id,))
            if not cur.fetchone():
                raise UserManagementError('Selected branch no longer exists.')
        cur.execute('''SELECT User_Id, User_Username, User_Email FROM USER_ACCOUNT
            WHERE LOWER(User_Username)=LOWER(%s) OR LOWER(User_Email)=LOWER(%s)
            LIMIT 1''', (username, email))
        conflict = cur.fetchone()
        if conflict:
            raise UserManagementError(_conflict_message(conflict, username, email))
        _require_similar_name_review(cur, first, last,
                                     excluding=None, confirmed=confirm_similar_name)
        cur.execute('SELECT COALESCE(MAX(User_Id), 0) + 1 AS next_id FROM USER_ACCOUNT')
        next_id = cur.fetchone()['next_id']
        if type(next_id) is not int or next_id > MAX_USER_ID:
            raise UserManagementError('No available user ID. Contact database administrator.')
        cur.execute('''INSERT INTO USER_ACCOUNT
            (User_Id, User_First_Name, User_Last_Name, User_Username,
             User_Password_Hash, User_Email, User_Is_Active, User_Type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
            (next_id, first, last, username, hashed, email, int(active), user_type))
        if user_type == ROLE_ADMIN:
            cur.execute('INSERT INTO ADMINISTRATOR (Admin_Id) VALUES (%s)', (next_id,))
        else:
            cur.execute('INSERT INTO STORE_STAFF (Staff_Id, Branch_Id) VALUES (%s, %s)',
                        (next_id, branch_id))
        if management_access:
            cur.execute('''INSERT INTO USER_MODULE_ACCESS
                (User_Id, Can_Manage_Users_Config, Granted_By_User_Id)
                VALUES (%s, 1, %s)''', (next_id, actor['user_id']))
        if require_password_change:
            cur.execute('''INSERT INTO USER_PASSWORD_STATE
                (User_Id, Must_Change_Password, Temp_Password_Expires_At)
                VALUES (%s, 1, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR))''',
                (next_id,))
        return {'user_id': next_id, 'username': username, 'temporary_password': password}

    try:
        return _execute_write(actor, write, connection_factory)
    except mysql.connector.IntegrityError as exc:
        raise UserManagementError('Account could not be created: a username, email, or ID conflicts with an existing record.') from exc



def update_user_profile(
    actor: dict, target_id: int, *, first_name: str, last_name: str,
    username: str, email: str, branch_id: int | None,
    expected_profile: dict, connection_factory: Callable | None = None,
    confirm_similar_name: bool = False,
) -> dict:
    '''Edit an existing profile; never change role, password, or privileges.

    Requires an original snapshot and prevents overwriting another admin's
    intervening changes. An email/username change revokes existing sessions;
    changing the recovery email also invalidates outstanding password links.
    '''
    target_id = _validate_id(target_id, 'user')
    first, last, account, address = _validate_user(
        first_name, last_name, username, email)
    if branch_id is not None:
        branch_id = _validate_id(branch_id, 'branch')
    if type(confirm_similar_name) is not bool:
        raise UserManagementError('Invalid duplicate-account confirmation.')
    if not isinstance(expected_profile, dict):
        raise UserManagementError('Reload the selected user before editing.')
    expected_first, expected_last, expected_name, expected_email = _validate_user(
        expected_profile.get('User_First_Name'),
        expected_profile.get('User_Last_Name'),
        expected_profile.get('User_Username'),
        expected_profile.get('User_Email'),
    )
    expected_branch = expected_profile.get('Branch_Id')
    if expected_branch is not None:
        expected_branch = _validate_id(expected_branch, 'branch')

    def write(cur, role):
        target = _target(cur, target_id)
        # SELF_PROFILE_ONLY_ALLOW: only authenticated, privileged Administrators
        # may edit their own basic identity. Status/role/access stay protected by
        # the unchanged _can_modify() checks in their dedicated service methods.
        if actor['user_id'] == target_id:
            if role != ROLE_ADMIN or target['User_Type'] != ROLE_ADMIN:
                raise UserManagementPermissionError('Administrator access required.')
        else:
            _can_modify(actor, role, target)
        cur.execute('''SELECT u.User_First_Name, u.User_Last_Name,
                   u.User_Username, u.User_Email, u.User_Type, s.Branch_Id
            FROM USER_ACCOUNT u
            LEFT JOIN STORE_STAFF s ON s.Staff_Id=u.User_Id
            WHERE u.User_Id=%s LIMIT 1 FOR UPDATE''', (target_id,))
        current = cur.fetchone()
        if current is None:
            raise UserManagementError('User account no longer exists.')
        actual = (' '.join(current['User_First_Name'].strip().split()),
                  ' '.join(current['User_Last_Name'].strip().split()),
                  current['User_Username'], current['User_Email'].lower(),
                  current['Branch_Id'])
        expected = (expected_first, expected_last, expected_name,
                    expected_email, expected_branch)
        if actual != expected:
            raise UserManagementError(
                'This profile was changed by someone else. Cancel and reopen it to reload the latest details.')

        if current['User_Type'] == ROLE_STAFF:
            if branch_id is None:
                raise UserManagementError('Store Staff must belong to a branch.')
            cur.execute('SELECT Branch_Id FROM BRANCH WHERE Branch_Id=%s AND Branch_Is_Active=1 LIMIT 1 LOCK IN SHARE MODE',
                        (branch_id,))
            if cur.fetchone() is None:
                raise UserManagementError('Selected branch no longer exists.')
        elif current['User_Type'] == ROLE_ADMIN:
            if branch_id is not None:
                raise UserManagementError('Administrators cannot be assigned to a branch.')
        else:
            raise UserManagementError('Invalid account type. Contact an administrator.')

        cur.execute('''SELECT User_Id, User_Username, User_Email FROM USER_ACCOUNT
            WHERE User_Id<>%s AND
            (LOWER(User_Username)=LOWER(%s) OR LOWER(User_Email)=LOWER(%s))
            LIMIT 1''', (target_id, account, address))
        conflict = cur.fetchone()
        if conflict:
            raise UserManagementError(_conflict_message(conflict, account, address))
        _require_similar_name_review(cur, first, last,
                                     excluding=target_id, confirmed=confirm_similar_name)

        email_changed = address != current['User_Email'].lower()
        username_changed = account != current['User_Username']
        branch_changed = (current['User_Type'] == ROLE_STAFF
                          and branch_id != current['Branch_Id'])
        if (first, last, account, address) != actual[:4]:
            cur.execute('''UPDATE USER_ACCOUNT
                SET User_First_Name=%s, User_Last_Name=%s,
                    User_Username=%s, User_Email=%s
                WHERE User_Id=%s''', (first, last, account, address, target_id))
        if branch_changed:
            cur.execute('UPDATE STORE_STAFF SET Branch_Id=%s WHERE Staff_Id=%s',
                        (branch_id, target_id))
        if email_changed:
            # Reset/invite links to the previous recovery address must die.
            cur.execute('''UPDATE USER_PASSWORD_TOKEN
                SET Consumed_At=UTC_TIMESTAMP()
                WHERE User_Id=%s AND Consumed_At IS NULL''', (target_id,))
        if email_changed or username_changed or branch_changed:
            # Prevent old sessions from retaining account access under stale
            # identifying information; preserve Must_Change_Password if present.
            cur.execute('''INSERT INTO USER_PASSWORD_STATE
                (User_Id, Must_Change_Password, Session_Version)
                VALUES (%s, 0, 1)
                ON DUPLICATE KEY UPDATE Session_Version=Session_Version+1''',
                (target_id,))
        return {'email_changed': email_changed, 'username_changed': username_changed,
                'branch_changed': branch_changed}

    try:
        return _execute_write(actor, write, connection_factory)
    except mysql.connector.IntegrityError as exc:
        raise UserManagementError(
            'Username or email conflicts with an existing account.') from exc



def set_account_active(actor: dict, target_id: int, active: bool,
                       *, connection_factory: Callable | None = None) -> None:
    target_id = _validate_id(target_id, 'user')
    if type(active) is not bool:
        raise UserManagementError('Invalid active setting.')
    def write(cur, role):
        t = _target(cur, target_id)
        _can_modify(actor, role, t)
        cur.execute('UPDATE USER_ACCOUNT SET User_Is_Active=%s WHERE User_Id=%s',
                    (int(active), target_id))
    _execute_write(actor, write, connection_factory)


def set_management_access(actor: dict, target_id: int, allowed: bool,
                          *, connection_factory: Callable | None = None) -> None:
    target_id = _validate_id(target_id, 'user')
    if type(allowed) is not bool:
        raise UserManagementError('Invalid management permission.')
    def write(cur, role):
        if role != ROLE_ADMIN:
            raise UserManagementPermissionError('Only authorized Administrators can change management access.')
        t = _target(cur, target_id)
        _can_modify(actor, role, t)
        if t['User_Type'] != ROLE_ADMIN:
            raise UserManagementError('Users + Configuration access is only available to Administrators.')
        cur.execute('''INSERT INTO USER_MODULE_ACCESS
            (User_Id, Can_Manage_Users_Config, Granted_By_User_Id)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
              Can_Manage_Users_Config=VALUES(Can_Manage_Users_Config),
              Granted_By_User_Id=VALUES(Granted_By_User_Id)''',
            (target_id, int(allowed), actor['user_id']))
    _execute_write(actor, write, connection_factory)


def reset_account_password(actor: dict, target_id: int,
                           *, connection_factory: Callable | None = None,
                           password_factory: Callable | None = None) -> str:
    target_id = _validate_id(target_id, 'user')
    password = (password_factory or (lambda: secrets.token_urlsafe(24)))()
    if not isinstance(password, str) or not password or len(password.encode('utf-8')) > 72:
        raise UserManagementError('Unable to generate a temporary password.')
    hashed = hash_password(password)
    def write(cur, role):
        target = _target(cur, target_id)
        _can_modify(actor, role, target)
        cur.execute('UPDATE USER_ACCOUNT SET User_Password_Hash=%s WHERE User_Id=%s',
                    (hashed, target_id))
        # Legacy admin reset is also forced-change and revokes active sessions.
        cur.execute('''INSERT INTO USER_PASSWORD_STATE
            (User_Id, Must_Change_Password, Temp_Password_Expires_At, Session_Version)
            VALUES (%s, 1, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR), 1)
            ON DUPLICATE KEY UPDATE Must_Change_Password=1,
                Temp_Password_Expires_At=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR),
                Session_Version=Session_Version+1''', (target_id,))
        cur.execute('''UPDATE USER_PASSWORD_TOKEN SET Consumed_At=UTC_TIMESTAMP()
            WHERE User_Id=%s AND Consumed_At IS NULL''', (target_id,))
    _execute_write(actor, write, connection_factory)
    return password


def change_staff_branch(
    actor: dict, target_id: int, branch_id: int, *,
    expected_branch_id: int | None, connection_factory: Callable | None = None,
) -> dict:
    """Reassign Store Staff, reject stale changes and revoke old sessions."""
    target_id = _validate_id(target_id, 'user')
    branch_id = _validate_id(branch_id, 'branch')
    if expected_branch_id is not None:
        expected_branch_id = _validate_id(expected_branch_id, 'previous branch')

    def write(cur, role):
        target = _target(cur, target_id)
        _can_modify(actor, role, target)
        if target['User_Type'] != ROLE_STAFF:
            raise UserManagementError('Branch can be changed for Store Staff only.')
        cur.execute('SELECT Branch_Id FROM STORE_STAFF WHERE Staff_Id=%s LIMIT 1 FOR UPDATE',
                    (target_id,))
        row = cur.fetchone()
        if row is None:
            raise UserManagementError('Store Staff record does not exist.')
        previous = row['Branch_Id']
        if previous != expected_branch_id:
            raise UserManagementError('Branch assignment changed. Reload the employee and try again.')
        cur.execute('SELECT Branch_Id FROM BRANCH WHERE Branch_Id=%s AND Branch_Is_Active=1 LIMIT 1 LOCK IN SHARE MODE',
                    (branch_id,))
        if cur.fetchone() is None:
            raise UserManagementError('Selected branch is inactive or does not exist.')
        if previous == branch_id:
            return {'branch_changed': False, 'branch_id': previous}
        cur.execute('UPDATE STORE_STAFF SET Branch_Id=%s WHERE Staff_Id=%s',
                    (branch_id, target_id))
        # Logged-in staff must not retain a session tied to their old branch.
        cur.execute('''INSERT INTO USER_PASSWORD_STATE
            (User_Id, Must_Change_Password, Session_Version)
            VALUES (%s, 0, 1)
            ON DUPLICATE KEY UPDATE Session_Version=Session_Version+1''',
                    (target_id,))
        return {'branch_changed': True, 'branch_id': branch_id}

    return _execute_write(actor, write, connection_factory)


def create_and_invite_user(actor: dict, *, connection_factory=None,
                           password_factory=None, email_sender=None, **user_fields) -> dict:
    """New Users UI entrypoint: atomically force first-change, then email.

    Returns a safe receipt without the plaintext password. A failed SMTP send
    does NOT roll back an already committed user; the admin can resend a link.
    """
    from password_recovery import send_new_user_invitation
    created = create_user_account(
        actor, connection_factory=connection_factory, password_factory=password_factory,
        require_password_change=True, **user_fields)
    sent = False
    if user_fields.get('active', True):
        try:
            sent = send_new_user_invitation(
                created['user_id'], created['temporary_password'],
                connection_factory=connection_factory, email_sender=email_sender)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'Invitation failed after user account was committed: user_id=%s',
                created['user_id'])
    return {'user_id': created['user_id'], 'username': created['username'],
            'email_sent': bool(sent)}
