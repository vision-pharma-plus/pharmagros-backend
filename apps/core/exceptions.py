"""
Domain exceptions and a uniform API error envelope.

Business rule violations are expressed as typed exceptions in the service
layer, then translated once here. Services stay free of HTTP concerns, and
the frontend receives a predictable shape it can render in either language.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ServiceUnavailable(APIException):
    """
    A dependency the server needs is not installed or reachable.

    Lives here rather than in one app because more than one subsystem raises
    it — invoicing and reporting both depend on the PDF engine, and a missing
    native library is a deployment fault (503) rather than a bad request.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "service_unavailable"
    default_detail = _("A service this request depends on is unavailable.")


class BusinessRuleViolation(Exception):
    """Base for domain rule failures. Maps to HTTP 422."""

    default_message = _("The operation violates a business rule.")
    code = "business_rule_violation"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    def __init__(self, message=None, *, code: str | None = None, details: dict | None = None):
        self.message = message or self.default_message
        if code:
            self.code = code
        self.details = details or {}
        super().__init__(self.message)


class InsufficientStock(BusinessRuleViolation):
    default_message = _("Insufficient stock available to fulfil this request.")
    code = "insufficient_stock"


class ExpiredBatchError(BusinessRuleViolation):
    default_message = _("This batch has expired and cannot be issued.")
    code = "expired_batch"


class CreditLimitExceeded(BusinessRuleViolation):
    """
    A credit sale was refused.

    Carries the specific `code` from `Customer.can_buy_on_credit` rather than
    always reporting a limit breach: "blocked", "cash only", "inactive" and
    "no limit set" are different problems with different remedies, and only a
    true breach can be overridden by a supervisor. Clients branch on the code,
    so it must survive the trip through the error envelope.
    """

    default_message = _("This sale would exceed the customer's credit limit.")
    code = "credit_limit_exceeded"


class InvalidStateTransition(BusinessRuleViolation):
    default_message = _("This action is not permitted in the document's current state.")
    code = "invalid_state_transition"


class DocumentLocked(BusinessRuleViolation):
    default_message = _("This document has been posted and can no longer be modified.")
    code = "document_locked"


def api_exception_handler(exc, context):
    """
    Normalise every error into: {"error": {code, message, details}}.

    Unhandled exceptions deliberately return a generic message — internal
    detail (SQL fragments, file paths, stack context) leaking to an API
    client is an information-disclosure vector.
    """
    if isinstance(exc, BusinessRuleViolation):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": str(exc.message),
                    "details": exc.details,
                }
            },
            status=exc.status_code,
        )

    if isinstance(exc, DjangoValidationError):
        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": _("The submitted data is invalid."),
                    "details": getattr(exc, "message_dict", {"non_field_errors": exc.messages}),
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, Http404):
        return Response(
            {
                "error": {
                    "code": "not_found",
                    "message": _("The requested resource was not found."),
                    "details": {},
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            {
                "error": {
                    "code": "permission_denied",
                    "message": _("You do not have permission to perform this action."),
                    "details": {},
                }
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    response = drf_exception_handler(exc, context)

    if response is not None:
        detail = response.data
        code = "error"
        message = _("The request could not be completed.")
        if isinstance(detail, dict) and "detail" in detail:
            message = detail["detail"]
            code = getattr(detail["detail"], "code", "error")
            detail = {}
        response.data = {
            "error": {"code": code, "message": message, "details": detail}
        }
        return response

    # Nothing matched: an unexpected server error.
    request = context.get("request")
    logger.exception(
        "Unhandled API exception",
        extra={"request_id": getattr(request, "request_id", ""), "path": getattr(request, "path", "")},
    )
    return Response(
        {
            "error": {
                "code": "internal_error",
                "message": _("An unexpected error occurred. Please contact support."),
                "details": {"request_id": getattr(request, "request_id", "")},
            }
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
