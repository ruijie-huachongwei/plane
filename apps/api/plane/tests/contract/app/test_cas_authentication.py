# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
import requests

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from plane.authentication.views.app.cas import (
    CAS_NEXT_PATH_SESSION_KEY,
    CAS_SERVICE_SESSION_KEY,
    CAS_STATE_SESSION_KEY,
)
from plane.db.models import Account, User
from plane.license.models import Instance


@pytest.fixture
def configured_instance(db):
    instance_id = uuid.uuid4() if not Instance.objects.exists() else Instance.objects.first().id
    instance, _ = Instance.objects.update_or_create(
        id=instance_id,
        defaults={
            "instance_name": "Test Instance",
            "instance_id": str(uuid.uuid4()),
            "current_version": "1.0.0",
            "domain": "http://localhost:8000",
            "last_checked_at": timezone.now(),
            "is_setup_done": True,
        },
    )
    return instance


@pytest.fixture
def cas_environment(monkeypatch):
    monkeypatch.setenv("IS_CAS_ENABLED", "1")
    monkeypatch.setenv("CAS_LOGIN_URL", "https://cas.example.com/login")
    monkeypatch.setenv("CAS_CALLBACK_URL", "http://localhost:8000/auth/cas/callback/")
    monkeypatch.setenv("CAS_IDENTITY_EXCHANGE_URL", "https://manager.example.com/v1/auth/exchange-cas-ticket")
    monkeypatch.setenv("CAS_IDENTITY_EXCHANGE_SECRET", "server-secret")
    monkeypatch.delenv("CAS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CAS_RENEW", raising=False)


def _initiate_login(client, next_path="/workspaces"):
    response = client.get(reverse("cas-initiate"), {"next_path": next_path})
    assert response.status_code == 302

    service = parse_qs(urlparse(response.url).query)["service"][0]
    state = parse_qs(urlparse(service).query)["state"][0]
    return service, state


@pytest.mark.contract
@pytest.mark.django_db
class TestCASAuthentication:
    def test_initiate_stores_state_and_builds_exact_service(self, configured_instance, cas_environment):
        client = Client()

        service, state = _initiate_login(client)

        assert service == f"http://localhost:8000/auth/cas/callback/?state={state}"
        assert uuid.UUID(state)
        assert client.session[CAS_STATE_SESSION_KEY] == state
        assert client.session[CAS_SERVICE_SESSION_KEY] == service
        assert client.session[CAS_NEXT_PATH_SESSION_KEY] == "/workspaces"

    def test_initiate_rejects_callback_url_with_query(self, configured_instance, cas_environment, monkeypatch):
        monkeypatch.setenv("CAS_CALLBACK_URL", "http://localhost:8000/auth/cas/callback/?source=plane")

        response = Client().get(reverse("cas-initiate"))

        assert response.status_code == 302
        assert "error_code=5200" in response.url

    @patch("plane.authentication.adapter.base.user_activation_email.delay")
    @patch("plane.authentication.views.app.cas.post_user_auth_workflow")
    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_exchanges_ticket_and_creates_plane_session(
        self,
        mock_exchange,
        mock_auth_workflow,
        _mock_activation_email,
        configured_instance,
        cas_environment,
    ):
        client = Client(HTTP_USER_AGENT="CAS contract test")
        service, state = _initiate_login(client)
        mock_exchange.return_value.raise_for_status.return_value = None
        mock_exchange.return_value.json.return_value = {
            "code": 200,
            "message": "",
            "data": {
                "subject": "employee-1001",
                "employeeNo": "1001",
                "name": "Ada Lovelace",
                "email": "ada@example.com",
            },
        }

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert response.status_code == 302
        assert "error_code" not in response.url
        assert "_auth_user_id" in client.session
        user = User.objects.get(email="ada@example.com")
        assert user.first_name == "Ada Lovelace"
        assert Account.objects.filter(
            user=user,
            provider="cas",
            provider_account_id="employee-1001",
            access_token="",
            metadata={"employee_no": "1001"},
        ).exists()
        mock_auth_workflow.assert_called_once()
        mock_exchange.assert_called_once_with(
            "https://manager.example.com/v1/auth/exchange-cas-ticket",
            json={"ticket": "ST-valid", "service": service},
            headers={"Authorization": "Bearer server-secret", "Accept": "application/json"},
            timeout=(3.05, 10),
            verify=True,
            allow_redirects=False,
        )

    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_consumes_state_before_validation(
        self,
        mock_exchange,
        configured_instance,
        cas_environment,
    ):
        client = Client()
        _, state = _initiate_login(client)

        first_response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": "wrong-state"})
        second_response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert "error_code=5205" in first_response.url
        assert "error_code=5205" in second_response.url
        assert CAS_STATE_SESSION_KEY not in client.session
        mock_exchange.assert_not_called()

    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_rejects_login_when_cas_is_disabled(
        self,
        mock_exchange,
        configured_instance,
        cas_environment,
        monkeypatch,
    ):
        client = Client()
        _, state = _initiate_login(client)
        monkeypatch.setenv("IS_CAS_ENABLED", "0")

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert "error_code=5205" in response.url
        assert CAS_STATE_SESSION_KEY not in client.session
        mock_exchange.assert_not_called()

    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_handles_identity_exchange_timeout(
        self,
        mock_exchange,
        configured_instance,
        cas_environment,
    ):
        client = Client()
        _, state = _initiate_login(client)
        mock_exchange.side_effect = requests.Timeout("timed out")

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert "error_code=5205" in response.url
        assert "_auth_user_id" not in client.session

    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_rejects_empty_identity(
        self,
        mock_exchange,
        configured_instance,
        cas_environment,
    ):
        client = Client()
        _, state = _initiate_login(client)
        mock_exchange.return_value.raise_for_status.return_value = None
        mock_exchange.return_value.json.return_value = {"code": 200, "message": "", "data": {}}

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert "error_code=5205" in response.url
        assert "_auth_user_id" not in client.session

    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_rejects_invalid_ca_bundle(
        self,
        mock_exchange,
        configured_instance,
        cas_environment,
        monkeypatch,
    ):
        client = Client()
        _, state = _initiate_login(client)
        monkeypatch.setenv("CAS_CA_BUNDLE", "/missing/company-ca.pem")

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert "error_code=5200" in response.url
        mock_exchange.assert_not_called()

    @patch("plane.authentication.views.app.cas.post_user_auth_workflow")
    @patch("plane.authentication.provider.cas.requests.post")
    def test_callback_rejects_subject_bound_to_another_email(
        self,
        mock_exchange,
        _mock_auth_workflow,
        configured_instance,
        cas_environment,
    ):
        existing_user = User.objects.create(email="existing@example.com", is_active=True)
        Account.objects.create(
            user=existing_user,
            provider="cas",
            provider_account_id="employee-1001",
            access_token="",
        )
        client = Client()
        _, state = _initiate_login(client)
        mock_exchange.return_value.raise_for_status.return_value = None
        mock_exchange.return_value.json.return_value = {
            "code": 200,
            "message": "",
            "data": {
                "subject": "employee-1001",
                "employeeNo": "1001",
                "name": "Another User",
                "email": "another@example.com",
            },
        }

        response = client.get(reverse("cas-callback"), {"ticket": "ST-valid", "state": state})

        assert response.status_code == 302
        assert "error_code=5210" in response.url
        assert "_auth_user_id" not in client.session
        assert not User.objects.filter(email="another@example.com").exists()
