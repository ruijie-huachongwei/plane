# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import os
import secrets
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.http import HttpResponseRedirect
from django.views import View

from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.authentication.provider.cas import CASProvider
from plane.authentication.utils.host import base_host
from plane.authentication.utils.login import user_login
from plane.authentication.utils.redirection_path import get_redirection_path
from plane.authentication.utils.user_auth_workflow import post_user_auth_workflow
from plane.license.models import Instance
from plane.utils.path_validator import get_safe_redirect_url, validate_next_path

CAS_STATE_SESSION_KEY = "cas_state"
CAS_SERVICE_SESSION_KEY = "cas_service"
CAS_NEXT_PATH_SESSION_KEY = "cas_next_path"


def _authentication_error_redirect(request, exception, next_path=""):
    return HttpResponseRedirect(
        get_safe_redirect_url(
            base_url=base_host(request=request, is_app=True),
            next_path=next_path,
            params=exception.get_error_dict(),
        )
    )


def _cas_configuration():
    enabled = os.environ.get("IS_CAS_ENABLED", "0") == "1"
    login_url = os.environ.get("CAS_LOGIN_URL", "").strip()
    callback_url = os.environ.get("CAS_CALLBACK_URL", "").strip()

    try:
        login_url_parts = urlparse(login_url)
        callback_url_parts = urlparse(callback_url)
        login_url_parts.port
        callback_url_parts.port
    except ValueError as exception:
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["CAS_NOT_CONFIGURED"],
            error_message="CAS_NOT_CONFIGURED",
        ) from exception

    if (
        not enabled
        or login_url_parts.scheme not in ("http", "https")
        or login_url_parts.hostname is None
        or login_url_parts.fragment
        or callback_url_parts.scheme not in ("http", "https")
        or callback_url_parts.hostname is None
        or callback_url_parts.query
        or callback_url_parts.fragment
    ):
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["CAS_NOT_CONFIGURED"],
            error_message="CAS_NOT_CONFIGURED",
        )

    return login_url, callback_url


def _url_with_query(url, params):
    parsed_url = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed_url.query, keep_blank_values=True) if key not in params]
    query.extend(params.items())
    return urlunparse(parsed_url._replace(query=urlencode(query)))


class CASInitiateEndpoint(View):
    def get(self, request):
        next_path = str(validate_next_path(request.GET.get("next_path", "")))

        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            exception = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INSTANCE_NOT_CONFIGURED"],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            return _authentication_error_redirect(request, exception, next_path)

        try:
            login_url, callback_url = _cas_configuration()
            state = str(uuid.uuid4())
            service = _url_with_query(callback_url, {"state": state})

            request.session[CAS_STATE_SESSION_KEY] = state
            request.session[CAS_SERVICE_SESSION_KEY] = service
            request.session[CAS_NEXT_PATH_SESSION_KEY] = next_path

            login_params = {"service": service}
            if os.environ.get("CAS_RENEW", "0").lower() in ("1", "true", "yes", "on"):
                login_params["renew"] = "true"
            return HttpResponseRedirect(_url_with_query(login_url, login_params))
        except AuthenticationException as exception:
            return _authentication_error_redirect(request, exception, next_path)


class CASCallbackEndpoint(View):
    def get(self, request):
        expected_state = request.session.pop(CAS_STATE_SESSION_KEY, "")
        service = request.session.pop(CAS_SERVICE_SESSION_KEY, "")
        next_path = request.session.pop(CAS_NEXT_PATH_SESSION_KEY, "")
        state = request.GET.get("state", "")
        ticket = request.GET.get("ticket", "")

        if (
            os.environ.get("IS_CAS_ENABLED", "0") != "1"
            or not expected_state
            or not state
            or not secrets.compare_digest(str(expected_state), str(state))
            or not service
            or not ticket
            or len(ticket) > 1024
        ):
            exception = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["CAS_AUTHENTICATION_FAILED"],
                error_message="CAS_AUTHENTICATION_FAILED",
            )
            return _authentication_error_redirect(request, exception, next_path)

        try:
            provider = CASProvider(
                request=request,
                ticket=ticket,
                service=service,
                callback=post_user_auth_workflow,
            )
            user = provider.authenticate()
            user_login(request=request, user=user, is_app=True)
            path = next_path or get_redirection_path(user=user)
            return HttpResponseRedirect(
                get_safe_redirect_url(
                    base_url=base_host(request=request, is_app=True),
                    next_path=path,
                    params={},
                )
            )
        except AuthenticationException as exception:
            return _authentication_error_redirect(request, exception, next_path)
