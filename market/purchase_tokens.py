"""Signed one-click purchase identifiers; no money or database writes on GET."""
from uuid import uuid4
from django.core import signing
from .services import MarketError

SALT = "market.one-click-purchase.v1"


def issue(trader, question):
    return signing.dumps({"trader": trader.pk, "question": question.pk, "key": str(uuid4())}, salt=SALT)


def read(token, trader, question):
    try:
        data = signing.loads(token or "", salt=SALT)
        if data["trader"] != trader.pk or data["question"] != question.pk:
            raise ValueError
        return data["key"]
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise MarketError("Форма покупки устарела или недействительна. Обновите страницу и повторите покупку.") from None
