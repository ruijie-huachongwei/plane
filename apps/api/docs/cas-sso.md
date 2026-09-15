# Company CAS single sign-on

Plane uses the Manager service as a server-to-server CAS identity broker. The browser receives only the Plane Django
session cookie; Manager tokens and the identity-exchange secret are never exposed to the browser.

## Plane API configuration

Set these values in the Plane API runtime environment:

```dotenv
IS_CAS_ENABLED=1
CAS_LOGIN_URL=https://cas.example.com/login
CAS_CALLBACK_URL=https://plane.example.com/auth/cas/callback/
CAS_IDENTITY_EXCHANGE_URL=https://manager.internal.example.com/v1/auth/exchange-cas-ticket
CAS_IDENTITY_EXCHANGE_SECRET=replace-with-a-shared-server-secret
```

Optional values:

```dotenv
CAS_RENEW=0
CAS_CA_BUNDLE=/etc/ssl/certs/company-ca.pem
```

`CAS_CALLBACK_URL` must be the externally reachable Plane API callback URL. It must not contain a query string or
fragment. Plane appends a one-time UUID as the sole `state` query parameter and sends that exact service URL both to
CAS and to the Manager identity-exchange endpoint.

Keep `CAS_IDENTITY_EXCHANGE_SECRET` in the API environment only. When `CAS_CA_BUNDLE` is set, the file must be mounted
inside the API container at the configured path. TLS certificate verification is always enabled.

## Manager service configuration

Keep the existing general CAS allowlist unchanged if other applications depend on it:

```yaml
employee:
  sid:
    auth:
      allowed-service-urls:
        - "*"
```

Configure Plane separately for the identity-exchange endpoint. This allowlist entry is the callback base URL without
the dynamic state query parameter:

```yaml
employee:
  sid:
    auth:
      identity-exchange-allowed-service-urls:
        - https://plane.example.com/auth/cas/callback/
      identityExchangeSecret: ${CAS_IDENTITY_EXCHANGE_SECRET:}
```

The Manager and Plane secrets must have the same value. The Manager endpoint must be reachable from the Plane API,
but it does not need to be exposed to the browser.

## Scope

This integration enables CAS on the main Plane Web login screen. Plane validates and consumes its own login state,
binds the stable CAS subject to a local `Account`, applies the existing signup and deactivation policies, and creates
its own Django session.

CAS login for Space and Admin, CAS single logout, role mapping, and ongoing profile synchronization are not included.
