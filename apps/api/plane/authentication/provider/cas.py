# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import os
from urllib.parse import urlparse

import requests

from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.authentication.adapter.base import Adapter
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.db.models import Account, User


class CASProvider(Adapter):
    provider = "cas"

    def __init__(self, request, ticket, service, callback=None):
        super().__init__(request=request, provider=self.provider, callback=callback)
        self.ticket = ticket
        self.service = service
        self.exchange_url = os.environ.get("CAS_IDENTITY_EXCHANGE_URL", "").strip()
        self.exchange_secret = os.environ.get("CAS_IDENTITY_EXCHANGE_SECRET", "").strip()
        self.ca_bundle = os.environ.get("CAS_CA_BUNDLE", "").strip()
        self.subject = None
        self.employee_no = None

        try:
            parsed_exchange_url = urlparse(self.exchange_url)
            parsed_exchange_url.port
            is_exchange_url_valid = (
                parsed_exchange_url.scheme in ("http", "https") and parsed_exchange_url.hostname is not None
            )
        except ValueError:
            is_exchange_url_valid = False

        if (
            not self.exchange_secret
            or not is_exchange_url_valid
            or (self.ca_bundle and not os.path.isfile(self.ca_bundle))
        ):
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_NOT_CONFIGURED"],
                error_message="CAS_NOT_CONFIGURED",
            )

    def authenticate(self):
        identity = self.exchange_ticket()
        self.subject = self._required_identity_value(identity, "subject", max_length=255)
        self.employee_no = self._required_identity_value(identity, "employeeNo", max_length=255)
        email = self.sanitize_email(self._required_identity_value(identity, "email"))
        name = self._optional_identity_value(identity, "name", max_length=255)

        self.set_user_data(
            {
                "email": email,
                "user": {
                    "provider_id": self.subject,
                    "email": email,
                    "avatar": "",
                    "first_name": name,
                    "last_name": "",
                    "is_password_autoset": True,
                },
            }
        )

        try:
            with transaction.atomic():
                self._validate_account_binding(email=email)
                user = self.complete_login_or_signup()
                self.create_update_account(user=user)
                return user
        except IntegrityError as exc:
            raise self._account_conflict_exception(email=email) from exc

    def exchange_ticket(self):
        try:
            response = requests.post(
                self.exchange_url,
                json={"ticket": self.ticket, "service": self.service},
                headers={
                    "Authorization": f"Bearer {self.exchange_secret}",
                    "Accept": "application/json",
                },
                timeout=(3.05, 10),
                verify=self.ca_bundle or True,
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (OSError, requests.RequestException, ValueError) as exc:
            self.logger.warning("CAS identity exchange request failed")
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            ) from exc

        if (
            not isinstance(payload, dict)
            or payload.get("code") != 200
            or not isinstance(payload.get("data"), dict)
            or not payload["data"]
        ):
            self.logger.warning("CAS identity exchange returned an invalid response")
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            )

        return payload["data"]

    def create_update_account(self, user):
        account = (
            Account.objects.select_for_update()
            .filter(
                provider=self.provider,
                provider_account_id=self.subject,
            )
            .first()
        )
        if account:
            if account.user_id != user.id:
                raise self._account_conflict_exception(email=user.email)
            account.last_connected_at = timezone.now()
            account.metadata = {**account.metadata, "employee_no": self.employee_no}
            account.save(update_fields=["last_connected_at", "metadata", "updated_at"])
            return account

        return Account.objects.create(
            user=user,
            provider=self.provider,
            provider_account_id=self.subject,
            access_token="",
            last_connected_at=timezone.now(),
            metadata={"employee_no": self.employee_no},
        )

    def _validate_account_binding(self, email):
        account = (
            Account.objects.select_for_update()
            .select_related("user")
            .filter(provider=self.provider, provider_account_id=self.subject)
            .first()
        )
        if account and account.user.email.strip().lower() != email:
            raise self._account_conflict_exception(email=email)

        user = User.objects.select_for_update().filter(email=email).first()
        if (
            user
            and Account.objects.filter(user=user, provider=self.provider)
            .exclude(provider_account_id=self.subject)
            .exists()
        ):
            raise self._account_conflict_exception(email=email)

    def _required_identity_value(self, identity, key, max_length=None):
        value = identity.get(key) if isinstance(identity, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            )
        value = value.strip()
        if max_length and len(value) > max_length:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            )
        return value

    def _optional_identity_value(self, identity, key, max_length=None):
        value = identity.get(key) if isinstance(identity, dict) else None
        value = value.strip() if isinstance(value, str) else ""
        if max_length and len(value) > max_length:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            )
        return value

    def _account_conflict_exception(self, email):
        return AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["CAS_ACCOUNT_CONFLICT"],
            error_message="CAS_ACCOUNT_CONFLICT",
            payload={"email": email},
        )
