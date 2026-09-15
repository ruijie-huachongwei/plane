# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.authentication.views.app.cas import CASCallbackEndpoint, CASInitiateEndpoint

urlpatterns = [
    path("", CASInitiateEndpoint.as_view(), name="cas-initiate"),
    path("callback/", CASCallbackEndpoint.as_view(), name="cas-callback"),
]
