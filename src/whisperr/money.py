"""ISO 4217 minor units for catalog prices."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Union

# The ISO 4217 minor units that are not 2. The same table as the Whisperr server.
_MINOR_DIGITS = {
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0, "KRW": 0, "PYG": 0,
    "RWF": 0, "UGX": 0, "VND": 0, "VUV": 0, "XAF": 0, "XOF": 0, "XPF": 0,
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
    "CLF": 4, "UYW": 4,
}
_CURRENCY = re.compile(r"^[A-Za-z]{3}$")
_DECIMAL = re.compile(r"^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_MAX_MINOR = 2**53 - 1

Amount = Union[str, int, float, Decimal]


def currency_exponent(currency: str) -> int:
    """The number of minor-unit digits for an ISO 4217 code (2 unless listed)."""
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise ValueError(f"invalid currency {currency!r}: expected an ISO 4217 code")
    return _MINOR_DIGITS.get(currency.upper(), 2)


def to_minor_units(amount: Amount, currency: str) -> int:
    """Convert a price in major units to integer minor units.

    ``to_minor_units("15.00", "USD") == 1500``, ``to_minor_units(1500, "JPY") ==
    1500``, ``to_minor_units("1.234", "KWD") == 1234``. A float is read through
    its shortest decimal form, so ``19.99`` is ``1999``. An amount with more
    precision than the currency has (``"9.999"`` USD), a negative amount, or a
    non-number raises ``ValueError``: the helper never rounds money silently.
    """
    exponent = currency_exponent(currency)
    if isinstance(amount, bool):
        raise _invalid(amount)
    if isinstance(amount, Decimal):
        text = format(amount, "f") if amount.is_finite() else ""
    elif isinstance(amount, int):
        text = str(amount)
    elif isinstance(amount, float):
        text = repr(amount)
    elif isinstance(amount, str):
        text = amount.strip()
    else:
        raise _invalid(amount)
    if not _DECIMAL.match(text):
        raise _invalid(amount)
    try:
        scaled = Decimal(text).scaleb(exponent)
    except InvalidOperation:
        raise _invalid(amount) from None
    if scaled != scaled.to_integral_value() or scaled > _MAX_MINOR:
        raise _invalid(amount)
    return int(scaled)


def _invalid(amount: object) -> ValueError:
    return ValueError(
        f"invalid amount {amount!r}: expected a non-negative decimal with at most the currency's minor digits"
    )
