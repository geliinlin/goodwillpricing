"""Secure master-data maintenance for the Goodwill Pricing System.

Only an Administrator with live Users + Configuration permissions may use it.
Names/IDs are preserved for historical records. No physical deletes exist.
Requires migration 003_master_data_management.sql.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Callable

import mysql.connector

from database import get_connection
from users_service import has_management_access, _read_actor, UserManagementPermissionError

LOCK_NAME = 'goodwill.master_data.v1'
MAX_ID = 2_147_483_647
BRANCH_ADDRESS_FIELDS = ('Branch_Street', 'Branch_City', 'Branch_State', 'Branch_Zip')


class ConfigurationError(ValueError):
    """Expected user-facing validation / consistency error."""


@dataclass(frozen=True)
class MasterSpec:
    kind: str
    label: str
    table: str
    key: str
    name: str
    active: str
    detail: tuple[str, ...] = ()
    related_guideline: bool = False


SPECS = {
    'item_types': MasterSpec('item_types', 'Item Types', 'ITEM_TYPE', 'Item_Type_Id', 'Item_Type_Name', 'Item_Type_Is_Active', ('Item_Type_Description',), related_guideline=True),
    'categories': MasterSpec('categories', 'Categories', 'CATEGORY', 'Category_Id', 'Category_Name', 'Category_Is_Active', ('Category_Description',), True),
    'brands': MasterSpec('brands', 'Brand Tiers', 'BRAND', 'Brand_Id', 'Brand_Name', 'Brand_Is_Active', ('Brand_Description',), True),
    'conditions': MasterSpec('conditions', 'Item Conditions', 'ITEM_CONDITION', 'Condition_Id', 'Condition_Name', 'Condition_Is_Active', ('Condition_Description',), True),
    'branches': MasterSpec('branches', 'Branches', 'BRANCH', 'Branch_Id', 'Branch_Name', 'Branch_Is_Active',
                           ('Branch_Street', 'Branch_City', 'Branch_State', 'Branch_Zip', 'Branch_Description')),
}


def _spec(kind: str) -> MasterSpec:
    if not isinstance(kind, str) or kind not in SPECS:
        raise ConfigurationError('Unknown configuration section.')
    return SPECS[kind]


def _id(value) -> int:
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise ConfigurationError('Select a valid master-data ID.')
    return value


def _clean(value, label: str, limit: int, optional=False) -> str:
    if value is None and optional:
        return ''
    if not isinstance(value, str):
        raise ConfigurationError(f'{label} must be text.')
    text = ' '.join(value.strip().split())
    if not text and not optional:
        raise ConfigurationError(f'{label} is required.')
    if len(text) > limit:
        raise ConfigurationError(f'{label} must be at most {limit} characters.')
    return text


def _norm(value) -> str:
    return ' '.join(str(value or '').strip().split()).casefold()


def _validated(spec: MasterSpec, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ConfigurationError('Enter valid master-data details.')
    out = {spec.name: _clean(payload.get(spec.name), 'Name', 100 if spec.kind == 'branches' else 50)}
    if spec.kind == 'categories':
        out['Item_Type_Id'] = _id(payload.get('Item_Type_Id'))
    if spec.kind == 'branches':
        out.update({
            'Branch_Street': _clean(payload.get('Branch_Street'), 'Street', 100),
            'Branch_City': _clean(payload.get('Branch_City'), 'City', 50),
            'Branch_State': _clean(payload.get('Branch_State'), 'State', 2).upper(),
            'Branch_Zip': _clean(payload.get('Branch_Zip'), 'ZIP', 10),
            'Branch_Description': _clean(payload.get('Branch_Description'), 'Description', 255, optional=True),
        })
        if not re.fullmatch(r'[A-Z]{2}', out['Branch_State']):
            raise ConfigurationError('State must be a two-letter abbreviation.')
        if not re.fullmatch(r'\d{5}(?:-\d{4})?', out['Branch_Zip']):
            raise ConfigurationError('Enter a valid US ZIP code (12345 or 12345-6789).')
    else:
        for field in spec.detail:
            out[field] = _clean(payload.get(field), 'Description', 255, optional=True)
    return out


def _snapshot(spec: MasterSpec, row: dict) -> str:
    fields = (spec.key, spec.name, spec.active, *spec.detail, *(("Item_Type_Id",) if spec.kind == 'categories' else ()))
    content = json.dumps([str(row.get(field, '')) for field in fields], ensure_ascii=False)
    return sha256(content.encode('utf-8')).hexdigest()


def _require_actor(cur, actor: dict) -> None:
    # Session permissions are only an early rejection. Always verify live DB.
    _read_actor(cur, actor)


def _connection(factory):
    return (factory or get_connection)()


def _base_row(cur, spec: MasterSpec, entity_id: int, lock=False):
    columns = ', '.join((spec.key, spec.name, spec.active, *spec.detail, *(("Item_Type_Id",) if spec.kind == 'categories' else ())))
    suffix = ' FOR UPDATE' if lock else ''
    cur.execute(f'SELECT {columns} FROM {spec.table} WHERE {spec.key}=%s LIMIT 1{suffix}', (entity_id,))
    return cur.fetchone()


def _duplicates(cur, spec: MasterSpec, fields: dict, exclude_id=None):
    # Read every name; canonicalization also catches internal whitespace and case.
    select_cols = ', '.join([spec.key, spec.name] + list(spec.detail) + (['Item_Type_Id'] if spec.kind == 'categories' else []))
    cur.execute(f'SELECT {select_cols} FROM {spec.table} FOR UPDATE')
    name = _norm(fields[spec.name])
    for row in cur.fetchall():
        if row[spec.key] == exclude_id:
            continue
        if (_norm(row[spec.name]) == name
                and (spec.kind != 'categories' or row['Item_Type_Id'] == fields['Item_Type_Id'])):
            raise ConfigurationError(
                f'{spec.label} entry #{row[spec.key]} already uses this name. Review the existing entry.')
        if spec.kind == 'branches' and all(
            _norm(row[f]) == _norm(fields[f]) for f in BRANCH_ADDRESS_FIELDS
        ):
            raise ConfigurationError(
                f'Branch #{row[spec.key]} already uses this address. Review the existing branch.')


def _require_active_type(cur, item_type_id: int) -> None:
    cur.execute('''SELECT Item_Type_Id FROM ITEM_TYPE
                   WHERE Item_Type_Id=%s AND Item_Type_Is_Active=1 LOCK IN SHARE MODE''',
                (item_type_id,))
    if cur.fetchone() is None:
        raise ConfigurationError('Select an active Item Type for this Category.')


def _ensure_category_unreferenced(cur, category_id: int) -> None:
    for table in ('PRICING_GUIDELINE', 'ITEM'):
        cur.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE Category_Id=%s', (category_id,))
        if (cur.fetchone() or {}).get('n', 0) > 0:
            raise ConfigurationError('Cannot change Item Type for a Category used by existing Items or Guidelines.')


def _audit(cur, actor_id: int, spec: MasterSpec, entity_id: int,
           action: str, before: dict | None, after: dict | None):
    def dump(row):
        return json.dumps(row, ensure_ascii=False, default=str) if row is not None else None
    cur.execute('''INSERT INTO MASTER_DATA_AUDIT
        (Actor_User_Id, Entity_Type, Entity_Id, Action_Type, Before_Json, After_Json, Changed_At)
        VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())''',
        (actor_id, spec.kind, entity_id, action, dump(before), dump(after)))


def _run_write(actor: dict, task, connection_factory=None):
    if not has_management_access(actor):
        raise UserManagementPermissionError('Users + Configuration permission required.')
    conn = _connection(connection_factory)
    cur = None
    locked = False
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute('SELECT GET_LOCK(%s, 10) AS acquired', (LOCK_NAME,))
        locked = bool((cur.fetchone() or {}).get('acquired') == 1)
        if not locked:
            raise ConfigurationError('Configuration is busy. Try again shortly.')
        conn.start_transaction()
        _require_actor(cur, actor)
        result = task(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            if locked:
                try:
                    cur.execute('SELECT RELEASE_LOCK(%s) AS released', (LOCK_NAME,))
                    cur.fetchone()
                except mysql.connector.Error:
                    pass
            cur.close()
        conn.close()


def load_master_data(actor: dict, kind: str, *, connection_factory: Callable | None = None) -> list[dict]:
    spec = _spec(kind)
    if not has_management_access(actor):
        raise UserManagementPermissionError('Users + Configuration permission required.')
    conn = _connection(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        _require_actor(cur, actor)
        if spec.kind == 'branches':
            dependent = '''(SELECT COUNT(*) FROM STORE_STAFF ss WHERE ss.Branch_Id=t.Branch_Id) AS staff_count,
                (SELECT COUNT(*) FROM STORE_STAFF ss JOIN USER_ACCOUNT ua ON ua.User_Id=ss.Staff_Id
                 WHERE ss.Branch_Id=t.Branch_Id AND ua.User_Is_Active=1) AS active_staff_count,
                0 AS guideline_count, 0 AS item_count'''
        else:
            dependent = f'''(SELECT COUNT(*) FROM PRICING_GUIDELINE g WHERE g.{spec.key}=t.{spec.key}) AS guideline_count,
                (SELECT COUNT(*) FROM PRICING_GUIDELINE g WHERE g.{spec.key}=t.{spec.key} AND g.Guideline_Is_Active=1) AS active_guideline_count,
                (SELECT COUNT(*) FROM ITEM i WHERE i.{spec.key}=t.{spec.key}) AS item_count,
                0 AS staff_count'''
        join_type = (' LEFT JOIN ITEM_TYPE it ON it.Item_Type_Id=t.Item_Type_Id'
                     if spec.kind == 'categories' else '')
        type_label = ', it.Item_Type_Name' if spec.kind == 'categories' else ''
        if spec.kind == 'item_types':
            dependent += ''', (SELECT COUNT(*) FROM CATEGORY c
                             WHERE c.Item_Type_Id=t.Item_Type_Id) AS category_count'''
        cur.execute(f'''SELECT t.*{type_label}, {dependent} FROM {spec.table} t{join_type}
                     ORDER BY t.{spec.active} DESC, t.{spec.name}, t.{spec.key}''')
        return cur.fetchall()
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def create_master_data(actor: dict, kind: str, payload: dict, *, connection_factory=None) -> int:
    spec = _spec(kind)
    fields = _validated(spec, payload)

    def task(cur):
        if spec.kind == 'categories':
            _require_active_type(cur, fields['Item_Type_Id'])
        _duplicates(cur, spec, fields)
        cur.execute(f'SELECT COALESCE(MAX({spec.key}), 0) + 1 AS next_id FROM {spec.table}')
        new_id = (cur.fetchone() or {}).get('next_id')
        if type(new_id) is not int or not 1 <= new_id <= MAX_ID:
            raise ConfigurationError('No available master-data ID.')
        col_names = [spec.key, *fields, spec.active]
        placeholders = ', '.join(['%s'] * len(col_names))
        cur.execute(f'INSERT INTO {spec.table} ({", ".join(col_names)}) VALUES ({placeholders})',
                    (new_id, *fields.values(), 1))
        created = {spec.key: new_id, **fields, spec.active: 1}
        _audit(cur, actor['user_id'], spec, new_id, 'CREATE', None, created)
        return new_id

    try:
        return _run_write(actor, task, connection_factory)
    except mysql.connector.IntegrityError as exc:
        raise ConfigurationError('An identical master-data value or ID already exists. Refresh and retry.') from exc


def update_master_data(actor: dict, kind: str, entity_id: int, payload: dict,
                       expected_snapshot: str, *, connection_factory=None) -> None:
    spec = _spec(kind)
    entity_id = _id(entity_id)
    fields = _validated(spec, payload)
    if not isinstance(expected_snapshot, str) or len(expected_snapshot) != 64:
        raise ConfigurationError('Reopen the entry before saving changes.')

    def task(cur):
        prior = _base_row(cur, spec, entity_id, lock=True)
        if not prior:
            raise ConfigurationError('Entry no longer exists.')
        if _snapshot(spec, prior) != expected_snapshot:
            raise ConfigurationError('Entry changed since you opened it. Reload before saving.')
        if spec.kind == 'categories':
            _require_active_type(cur, fields['Item_Type_Id'])
            if int(prior['Item_Type_Id']) != fields['Item_Type_Id']:
                _ensure_category_unreferenced(cur, entity_id)
        _duplicates(cur, spec, fields, exclude_id=entity_id)
        if any(str(prior.get(k) or '') != str(v or '') for k, v in fields.items()):
            sets = ', '.join(f'{key}=%s' for key in fields)
            cur.execute(f'UPDATE {spec.table} SET {sets} WHERE {spec.key}=%s',
                        (*fields.values(), entity_id))
            _audit(cur, actor['user_id'], spec, entity_id, 'UPDATE', prior,
                   {**prior, **fields})

    try:
        _run_write(actor, task, connection_factory)
    except mysql.connector.IntegrityError as exc:
        raise ConfigurationError('Another record already has this name or address.') from exc


def set_master_active(actor: dict, kind: str, entity_id: int, active: bool,
                      expected_snapshot: str, *, connection_factory=None) -> None:
    spec = _spec(kind)
    entity_id = _id(entity_id)
    if type(active) is not bool:
        raise ConfigurationError('Invalid active status.')
    if not isinstance(expected_snapshot, str) or len(expected_snapshot) != 64:
        raise ConfigurationError('Reopen the entry before changing status.')

    def task(cur):
        prior = _base_row(cur, spec, entity_id, lock=True)
        if not prior:
            raise ConfigurationError('Entry no longer exists.')
        if _snapshot(spec, prior) != expected_snapshot:
            raise ConfigurationError('Entry changed since you opened it. Reload before saving.')
        if bool(prior[spec.active]) == active:
            return
        if active and spec.kind == 'categories':
            _require_active_type(cur, prior['Item_Type_Id'])
        if not active:
            if spec.kind == 'branches':
                cur.execute('SELECT COUNT(*) AS n FROM STORE_STAFF WHERE Branch_Id=%s', (entity_id,))
                if (cur.fetchone() or {}).get('n', 0) > 0:
                    raise ConfigurationError('Cannot deactivate a branch with assigned staff. Reassign them first.')
            else:
                cur.execute(f'''SELECT COUNT(*) AS n FROM PRICING_GUIDELINE
                    WHERE {spec.key}=%s AND Guideline_Is_Active=1''', (entity_id,))
                if (cur.fetchone() or {}).get('n', 0) > 0:
                    raise ConfigurationError('Cannot deactivate while active pricing guidelines use this value.')
        cur.execute(f'UPDATE {spec.table} SET {spec.active}=%s WHERE {spec.key}=%s',
                    (int(active), entity_id))
        _audit(cur, actor['user_id'], spec, entity_id,
               'ACTIVATE' if active else 'DEACTIVATE', prior,
               {**prior, spec.active: int(active)})

    _run_write(actor, task, connection_factory)


def load_audit(actor: dict, *, limit: int = 30, connection_factory=None) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ConfigurationError('Invalid audit limit.')
    if not has_management_access(actor):
        raise UserManagementPermissionError('Users + Configuration permission required.')
    conn = _connection(connection_factory)
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        _require_actor(cur, actor)
        cur.execute('''SELECT a.Audit_Id, a.Entity_Type, a.Entity_Id, a.Action_Type, a.Changed_At,
            u.User_Username AS Actor_Name
            FROM MASTER_DATA_AUDIT a
            JOIN USER_ACCOUNT u ON u.User_Id=a.Actor_User_Id
            ORDER BY a.Audit_Id DESC LIMIT %s''', (limit,))
        return cur.fetchall()
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def snapshot_for_row(kind: str, row: dict) -> str:
    return _snapshot(_spec(kind), row)
