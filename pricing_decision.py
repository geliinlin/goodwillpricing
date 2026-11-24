"""Atomic final pricing and printable tags for the Goodwill G3 MySQL schema.

This is the submission half of the two-screen pricing workflow.  All user,
guideline and price checks are repeated on the server before any database write.
No schema changes are required.  Existing non-AUTO_INCREMENT IDs are allocated
under a MySQL named lock shared by every writer using this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import logging
import re
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ConnectionFactory = Callable[[], Any]
LOG = logging.getLogger(__name__)
LOCK_NAME = 'goodwill_pricing_final_submission_v1'
TZ = ZoneInfo('America/Detroit')
MONEY_MAX = Decimal('999999.99')


class PricingValidationError(ValueError):
    """Expected user-action error: explain it and allow a corrected submission."""


class StalePricingDraftError(PricingValidationError):
    """The draft no longer matches the authorized staff, store, or current guideline."""


class PricingSaveError(RuntimeError):
    """An unexpected database/backend failure with a non-sensitive diagnostic stage."""

    def __init__(self, stage: str, original: Exception, *, uncertain_tag: str | None = None):
        self.stage = stage
        self.uncertain_tag = uncertain_tag
        self.errno = getattr(original, 'errno', None)
        self.sqlstate = getattr(original, 'sqlstate', None)
        super().__init__(f'Pricing save failed at: {stage}')


@dataclass(frozen=True)
class PricingReceipt:
    decision_id: int
    item_id: int
    tag_number: str
    branch_id: int
    branch_name: str
    staff_id: int
    description: str
    item_type: str
    category: str
    brand: str
    condition: str
    guideline_id: int
    suggested_price: str
    final_price: str
    override_reason: str | None
    saved_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def money(value: Any) -> Decimal:
    """Parse a money input with no more than two decimal places."""
    try:
        raw = str(value).strip()
        if raw.startswith('$'):
            raw = raw[1:].strip()
        if ',' in raw and not re.fullmatch(r'[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?', raw):
            raise ValueError
        result = Decimal(raw.replace(',', ''))
    except (InvalidOperation, ValueError, TypeError):
        raise PricingValidationError('Enter a valid final price, for example $12.00.') from None
    if not result.is_finite() or result <= 0 or result > MONEY_MAX:
        raise PricingValidationError('Final price must be between $0.01 and $999,999.99.')
    if result.as_tuple().exponent < -2 and result != result.quantize(Decimal('0.01')):
        raise PricingValidationError('Final price cannot have more than two decimal places.')
    return result.quantize(Decimal('0.01'))


def validate_override(price: Decimal, suggested: Decimal, reason: str | None) -> str | None:
    reason = (reason or '').strip()
    if len(reason) > 255:
        raise PricingValidationError('Override reason cannot exceed 255 characters.')
    if price != suggested and not reason:
        raise PricingValidationError('A reason is required when the final price differs from the suggested price.')
    return reason if price != suggested else None


def _city_code(city: str) -> str:
    # Mount Pleasant -> MP, Lansing -> LA. Only plain ASCII enters barcode.
    words = re.findall(r'[A-Za-z]+', str(city or '').upper())
    if len(words) >= 2:
        return (words[0][0] + words[1][0])
    return ((words[0][:2] if words else 'GW') + 'X')[:2]


def generate_tag_number(city: str, when: datetime, decision_id: int) -> str:
    # Match the sample layout, e.g. MP26-100001 for first decision.
    serial = str(100000 + decision_id)
    value = f'{_city_code(city)}{when:%y}-{serial}'
    if len(value) > 20:
        raise PricingValidationError('Tag ID exceeded database character limit.')
    return value


def _connection(factory: ConnectionFactory | None):
    if factory is None:
        from database import get_connection
        factory = get_connection
    return factory()


def submitted_tag_exists(tag: str, connection_factory=None) -> bool:
    """Reconcile on the writer, after the previous submission releases its lock."""
    with _connection(connection_factory) as conn, conn.cursor(buffered=True) as cursor:
        cursor.execute('SELECT GET_LOCK(%s, 10)', (LOCK_NAME,))
        if cursor.fetchone()[0] != 1:
            raise RuntimeError('Submission status is still pending.')
        try:
            cursor.execute('SELECT 1 FROM PRICING_DECISION WHERE Decision_Tag_Number=%s', (tag,))
            return cursor.fetchone() is not None
        finally:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (LOCK_NAME,))
            cursor.fetchone()


def save_pricing_decision(
    user_id: int,
    draft: Mapping[str, Any],
    final_price: Any,
    reason: str | None,
    connection_factory: ConnectionFactory | None = None,
) -> PricingReceipt:
    """Insert one ITEM and one PRICING_DECISION in a single transaction.

    Staff and active guideline must still match at submission time.  Use the
    actual logged-in user_id, not a user_id supplied through a form.  This
    function assumes all app writes allocating these IDs use this named lock.
    """
    from item_search import ACTIVE_GUIDELINE_SQL, validate_item_entry

    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise PricingValidationError('Please log in again to submit a price.')
    if not isinstance(draft, Mapping):
        raise PricingValidationError('Search for a pricing guideline before submitting.')
    try:
        keys = ('item_type_id', 'category_id', 'brand_id', 'condition_id', 'guideline_id', 'staff_id', 'branch_id')
        # Accept integer IDs and their text form without silently truncating floats.
        ids = [int(str(draft[key])) for key in keys]
        item_type_id, category_id, brand_id, condition_id, guideline_id, staff_id, branch_id = ids
        received_date = date.fromisoformat(str(draft['item_received_date']))
        displayed_price = money(draft['suggested_price'])
    except (KeyError, TypeError, ValueError) as exc:
        raise PricingValidationError('Incomplete item information. Search guidelines again.') from exc
    if min(ids) <= 0:
        raise PricingValidationError('Invalid item attributes. Search again.')
    description = draft.get('item_description')
    validate_item_entry(description, received_date, datetime.now(TZ).date())
    description = (description or '').strip()
    price = money(final_price)
    # Validate form input before starting a transaction. Revalidate after
    # re-reading the guideline under the DB lock to prevent stale prices.
    validate_override(price, displayed_price, reason)

    try:
        conn = _connection(connection_factory)
    except Exception as exc:
        LOG.error('Pricing save failed at database connection')
        raise PricingSaveError('database connection', exc) from exc
    cursor = None
    locked = False
    stage = 'starting transaction'
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True, buffered=True)
        stage = 'acquiring submission lock'
        cursor.execute('SELECT GET_LOCK(%s, 10) AS got_lock', (LOCK_NAME,))
        row = cursor.fetchone()
        if not row or row['got_lock'] != 1:
            raise PricingValidationError('Another item is being saved. Please try again.')
        locked = True

        stage = 'checking staff authorization'
        cursor.execute(
            """
            SELECT u.User_Id, s.Staff_Id, b.Branch_Id, b.Branch_Name, b.Branch_City
            FROM USER_ACCOUNT u
            JOIN STORE_STAFF s ON s.Staff_Id = u.User_Id
            JOIN BRANCH b ON b.Branch_Id = s.Branch_Id
            WHERE u.User_Id = %s AND u.User_Is_Active = 1
                  AND b.Branch_Is_Active = 1
                  AND u.User_Type IN ('Employee', 'Store Staff')
            FOR UPDATE
            """,
            (user_id,),
        )
        staff = cursor.fetchone()
        if not staff or int(staff['Staff_Id']) != staff_id:
            raise StalePricingDraftError('Your staff account is no longer authorized. Sign in again.')
        if int(staff['Branch_Id']) != branch_id:
            raise StalePricingDraftError('Your assigned store has changed. Search again.')

        stage = 'checking active pricing guideline'
        cursor.execute(
            ACTIVE_GUIDELINE_SQL + ' FOR UPDATE',
            (item_type_id, category_id, brand_id, condition_id),
        )
        guidelines = cursor.fetchall()
        if len(guidelines) != 1:
            raise StalePricingDraftError(
                'The guideline is missing or ambiguous. Ask an administrator to review it, then search again.'
            )
        guideline = guidelines[0]
        current_price = money(guideline['Guideline_Suggested_Price'])
        if guideline_id != int(guideline['Guideline_Id']) or displayed_price != current_price:
            raise StalePricingDraftError('The suggested price has changed. Click Search Guidelines again.')
        override = validate_override(price, current_price, reason)

        # The G3 script defines integer PKs without AUTO_INCREMENT.  The named
        # lock serializes compliant writers until commit; unique DB keys remain
        # the final protection against noncompliant concurrent writers.
        stage = 'allocating item and decision IDs'
        cursor.execute('SELECT COALESCE(MAX(Item_Id), 0) + 1 AS next_id FROM ITEM')
        item_id = int(cursor.fetchone()['next_id'])
        cursor.execute('SELECT COALESCE(MAX(Decision_Id), 0) + 1 AS next_id FROM PRICING_DECISION')
        decision_id = int(cursor.fetchone()['next_id'])
        if item_id > 2147483647 or decision_id > 2147483647:
            raise PricingValidationError('Database ID limit reached.')
        now = datetime.now(TZ).replace(tzinfo=None, microsecond=0)
        tag = generate_tag_number(str(staff['Branch_City']), now, decision_id)

        stage = 'inserting ITEM'
        cursor.execute(
            """
            INSERT INTO ITEM
              (Item_Id, Item_Description, Item_Received_Date,
               Category_Id, Brand_Id, Condition_Id, Item_Type_Id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (item_id, description, received_date, category_id, brand_id, condition_id, item_type_id),
        )
        stage = 'inserting PRICING_DECISION'
        cursor.execute(
            """
            INSERT INTO PRICING_DECISION
              (Decision_Id, Decision_Datetime, Decision_Reason, Decision_Final_Price,
               Decision_Tag_Number, Guideline_Id, Staff_Id, Item_Id, Decision_Branch_Id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (decision_id, now, override, price, tag, guideline_id, int(staff['Staff_Id']), item_id, int(staff['Branch_Id'])),
        )
        receipt = PricingReceipt(
            decision_id=decision_id,
            item_id=item_id,
            tag_number=tag,
            branch_id=int(staff['Branch_Id']),
            branch_name=str(staff['Branch_Name']),
            staff_id=int(staff['Staff_Id']),
            description=description,
            item_type=str(guideline['Item_Type_Name']),
            category=str(guideline['Category_Name']),
            brand=str(guideline['Brand_Name']),
            condition=str(guideline['Condition_Name']),
            guideline_id=guideline_id,
            suggested_price=f'{current_price:.2f}',
            final_price=f'{price:.2f}',
            override_reason=override,
            saved_at=now.isoformat(sep=' '),
        )
        stage = 'committing transaction'
        conn.commit()
        return receipt
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            LOG.error('Transaction rollback also failed (original stage: %s)', stage)
        if isinstance(exc, PricingValidationError):
            raise
        LOG.error('Pricing save failed at stage: %s (errno=%s)', stage, getattr(exc, 'errno', None))
        uncertain_tag = tag if stage == 'committing transaction' else None
        raise PricingSaveError(stage, exc, uncertain_tag=uncertain_tag) from exc
    finally:
        if cursor is not None:
            if locked:
                try:
                    cursor.execute('SELECT RELEASE_LOCK(%s) AS released', (LOCK_NAME,))
                    cursor.fetchone()
                except Exception:
                    LOG.warning('Could not release advisory lock before closing the connection')
            try:
                cursor.close()
            except Exception:
                LOG.warning('Could not close pricing cursor')
        try:
            conn.close()
        except Exception:
            # A disconnect after COMMIT must not turn a saved receipt into a retry.
            LOG.warning('Could not close pricing connection')


def barcode_svg(tag_number: str) -> bytes:
    """An actual machine-readable Code128 barcode, not decorative stripes."""
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.graphics import renderSVG

    graphic = createBarcodeDrawing(
        'Code128', value=tag_number, barHeight=42, barWidth=1.05,
        humanReadable=False, quiet=True,
    )
    output = renderSVG.drawToString(graphic)
    return output.encode("utf-8") if isinstance(output, str) else output


def printable_label_pdf(receipt: Mapping[str, Any]) -> bytes:
    """Create a 4 x 2.5-inch printable label PDF."""
    from reportlab.graphics.barcode.code128 import Code128
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.pdfbase.pdfmetrics import stringWidth

    data = BytesIO()
    w, h = 4 * inch, 2.5 * inch
    pdf = canvas.Canvas(data, pagesize=(w, h))
    pdf.setTitle(f"Goodwill tag {receipt['tag_number']}")
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(16, h-30, 'goodwill')
    pdf.setFont('Helvetica', 9)
    branch = receipt.get("branch_id")
    store = "Unknown" if branch is None else f"{int(branch):03d}"
    pdf.drawRightString(w-16, h-27, f"STORE {store}")
    pdf.line(16, h-40, w-16, h-40)

    def fit_text(text: Any, font: str, size: int, max_width: float) -> str:
        value = ' '.join(str(text).split())
        if stringWidth(value, font, size) <= max_width:
            return value
        while value and stringWidth(value + '...', font, size) > max_width:
            value = value[:-1]
        return value + '...'

    pdf.setFont('Helvetica', 10)
    cat = f"{receipt['category']}  /  {receipt['brand']}  /  {receipt['condition']}"
    pdf.drawString(16, h-58, fit_text(cat, 'Helvetica', 10, w-32))
    pdf.setFont('Helvetica-Bold', 11)
    # A blank description is allowed and should not create an empty text line.
    if receipt.get('description'):
        pdf.drawString(16, h-76, fit_text(receipt['description'], 'Helvetica-Bold', 11, w-32))
    price_text = f"${receipt['final_price']}"
    tag = str(receipt['tag_number'])
    price_width = w - 32 - 8 - max(
        stringWidth('TAG NUMBER', 'Helvetica', 9),
        stringWidth(tag, 'Helvetica-Bold', 10),
    )
    price_size = 23
    while price_size > 10 and stringWidth(price_text, 'Helvetica-Bold', price_size) > price_width:
        price_size -= 1
    pdf.setFont('Helvetica-Bold', price_size)
    pdf.drawString(16, h-107, price_text)
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(w-16, h-101, 'TAG NUMBER')
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawRightString(w-16, h-113, tag)

    code = Code128(tag, barHeight=36, barWidth=1.20)
    if not code.validate() or not code.valid:
        raise PricingValidationError('This Tag ID cannot be encoded as Code 128.')
    horizontal_scale = min(1.0, (w - 32) / code.width)
    pdf.saveState()
    pdf.translate((w - code.width * horizontal_scale) / 2, 24)
    pdf.scale(horizontal_scale, 1)
    code.drawOn(pdf, 0, 0)
    pdf.restoreState()
    pdf.setFont('Helvetica', 8)
    pdf.drawCentredString(w/2, 12, tag)
    pdf.showPage()
    pdf.save()
    return data.getvalue()
