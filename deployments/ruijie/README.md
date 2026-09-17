# Plane Ruijie production deployment

This deployment builds the current source tree and serves every browser-facing component from one origin:

- Web: `https://plane-test.ruijie.com.cn/`
- Admin: `https://plane-test.ruijie.com.cn/god-mode/`
- Space: `https://plane-test.ruijie.com.cn/spaces/`
- API and authentication: `/api/` and `/auth/`
- Realtime collaboration: `/live/`

Valkey, RabbitMQ, API, workers, and Live remain private on the Docker network. PostgreSQL is published on the host loopback interface at `127.0.0.1:5432` by default, while ports 80 and 443 serve the web application. Alibaba OSS is used directly through presigned URLs, so MinIO is disabled.

## Prerequisites

1. Client DNS must resolve `plane-test.ruijie.com.cn` to `192.168.85.164`.
2. TCP ports 80 and 443 must be available on the server and permitted by its firewall.
3. Install Docker Engine and the Docker Compose plugin.
4. Obtain a trusted certificate whose SAN contains `plane-test.ruijie.com.cn`.
5. Configure the Manager CAS identity-exchange allowlist with this exact callback base URL:

   ```text
   https://plane-test.ruijie.com.cn/auth/cas/callback/
   ```

Because the domain resolves to an RFC1918 address, public HTTP-based ACME validation normally cannot reach it. Use a certificate issued by the company CA, or terminate TLS at an existing corporate gateway. The included Nginx configuration expects a company-issued certificate on this server.

## Install the certificate

Create the certificate directory and install the full chain and unencrypted private key:

```bash
sudo install -d -m 750 /etc/plane/certs
sudo install -m 644 /path/to/fullchain.pem /etc/plane/certs/fullchain.pem
sudo install -m 600 /path/to/privkey.pem /etc/plane/certs/privkey.pem
openssl x509 -in /etc/plane/certs/fullchain.pem -noout -subject -issuer -dates -ext subjectAltName
```

## Configure Plane

From the repository root on the server:

```bash
cd /data/plane
cp deployments/ruijie/.env.example deployments/ruijie/.env
chmod 600 deployments/ruijie/.env
vi deployments/ruijie/.env
```

Replace every `CHANGE_ME_*` value. Generate hexadecimal secrets so they are safe inside PostgreSQL and AMQP URLs:

```bash
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
python3 -c 'from secrets import token_urlsafe; print(token_urlsafe(64))'
```

Use the generated values for the PostgreSQL password, RabbitMQ password, Live secret, and Django secret. Set the real OSS credentials, bucket, and Manager shared secret directly on the server. Never commit `deployments/ruijie/.env`.

The PostgreSQL host mapping can be customized without changing Compose:

```dotenv
POSTGRES_BIND_ADDRESS=127.0.0.1
POSTGRES_HOST_PORT=5432
```

For access from another machine, bind to the server's specific private-network address and restrict that port to trusted clients with the host firewall. Avoid `0.0.0.0` unless network-level access controls are already in place.

For a trusted webhook receiver that resolves to an internal address, allow its exact hostname in the deployment environment:

```dotenv
WEBHOOK_ALLOWED_HOSTS=tianshu-test.ruijie.com.cn
```

Do not allow the entire `172.16.0.0/12` range. If hostname-based trust is not suitable, use the narrowest stable address instead, for example `WEBHOOK_ALLOWED_IPS=172.16.3.82/32`. Recreate both `api` and `worker` after changing either setting; URL validation runs in the API and delivery runs in the worker.

For an existing Plane database, retain the current PostgreSQL password. Changing only the environment value does not change the password stored in an initialized PostgreSQL volume. The same applies to an existing RabbitMQ volume.

Fail the deployment if a placeholder remains:

```bash
if grep -n 'CHANGE_ME' deployments/ruijie/.env; then
  echo 'Replace every CHANGE_ME value before deployment' >&2
  exit 1
fi
```

## Configure OSS CORS

The bucket must allow direct browser requests from Plane. Configure a CORS rule with:

```text
Allowed origin:  https://plane-test.ruijie.com.cn
Allowed methods: GET, HEAD, PUT, POST
Allowed headers: *
Expose headers:  ETag, x-oss-request-id
```

Do not enable public write access. Plane uses time-limited signed requests.

## Preserve existing data

Before replacing the current development stack, create a database backup:

```bash
cd /data/plane
docker compose -f docker-compose-local.yml exec -T plane-db \
  pg_dump -U plane -d plane -Fc > plane-before-production.dump
```

Stop the old stack without deleting volumes:

```bash
docker compose -f docker-compose-local.yml down
```

Never add `-v` to this command. Running both Compose definitions from `/data/plane` preserves the existing `plane_pgdata`, `plane_redisdata`, and `plane_rabbitmq_data` named volumes.

## Validate and start

All paths and the Compose project name are explicit, so these commands can be run from any directory.

Validate before building:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  config --quiet

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  config --services

sudo ss -lntp | grep -E ':(80|443)\b' || true
```

If another reverse proxy already owns port 80 or 443, stop it or configure that proxy to terminate TLS and forward all Plane paths instead of starting the bundled proxy. Ensure the host firewall permits inbound TCP 80 and 443 from the intended client networks.

Build and migrate in controlled stages:

The Ruijie proxy, Web, and Admin images use Nginx and do not compile Caddy or any Go modules.

```bash
DOCKER_BUILDKIT=1 docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  build

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  run --rm --no-deps proxy nginx -t

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  up -d
```

MinIO is behind the optional `minio` profile and is not started by these commands.

## Verify

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  ps

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  logs --tail=100 api live proxy

curl -I http://plane-test.ruijie.com.cn/
curl -fsS https://plane-test.ruijie.com.cn/api/instances/ | python3 -m json.tool
curl -sS -D - -o /dev/null https://plane-test.ruijie.com.cn/auth/cas/
```

Expected results:

- HTTP redirects to HTTPS.
- `/api/instances/` reports `is_cas_enabled: true`.
- `/auth/cas/` redirects to `https://sid.ruijie.com.cn/login?...`.
- `web`, `admin`, `space`, `api`, `worker`, `beat-worker`, `live`, `plane-db`, `plane-redis`, `plane-mq`, and `proxy` are running; `migrator` exits successfully.

If `/api/instances/` still shows stale configuration after the API was recreated:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  exec api python manage.py clear_cache
```

If it remains false, inspect a database override without printing secrets:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  exec api python manage.py shell -c \
  "from plane.license.models import InstanceConfiguration as C; print(list(C.objects.filter(key='IS_CAS_ENABLED').values('key','value')))"
```

Only when that command returns an existing row with value `0`, update it and clear the cache:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  exec api python manage.py shell -c \
  "from plane.license.models import InstanceConfiguration as C; C.objects.filter(key='IS_CAS_ENABLED').update(value='1')"

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  exec api python manage.py clear_cache
```

## Updates

After pulling a new revision:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  build

docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  up -d --remove-orphans
```

Renew the company certificate before expiry, replace the two files under `/etc/plane/certs`, and reload Nginx with:

```bash
docker compose -p plane \
  --env-file /data/plane/deployments/ruijie/.env \
  -f /data/plane/docker-compose.yml \
  -f /data/plane/deployments/ruijie/docker-compose.yml \
  exec proxy nginx -s reload
```
