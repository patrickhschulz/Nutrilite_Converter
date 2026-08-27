# Hosted deployment: AWS Lightsail

This distribution deploys the installable Nutrilite Converter progressive web
app (PWA) as two containers on an Ubuntu 24.04 virtual server:

- the dependency-free Python analysis/comparison API and static PWA, with the
  public Amway catalog in SQLite on persistent host storage;
- Caddy, which obtains and renews HTTPS certificates and proxies public traffic.

GitHub Actions tests every deployment, builds and smoke-tests the container,
transfers an immutable source archive over verified SSH, and activates that
release. A server timer refreshes the complete Amway catalog daily when it is
at least seven days old. A second timer retains 14 daily compressed SQLite
backups. Friends need only a private invite link: the app runs in Safari,
Chrome, or another modern browser and can be installed on a Home Screen without
an app store, account, database, API key, or configuration on their device.

The server persists only the reproducible public product catalog and its
backups. Product photos, pasted URLs, extracted facts, demographic inputs,
comparison results, and history are not written to server storage. Browser
profiles and history stay on that device and can be exported by the user. An
analysis request is held in memory while it is processed and its submitted
content is sent to the configured OpenAI API; the provider's API data controls
and terms still apply. Do not treat this shared-token design as a health-record
system or add server-side client retention without a separate privacy and
security design.

## Why Lightsail is the primary target

Lightsail is the recommended starting point when future headroom matters. AWS
documents a supported path for exporting Lightsail instance and block-storage
snapshots to EC2, and Lightsail can connect to AWS services through VPC peering.
That gives the project a gradual path to EC2 or ECS, RDS PostgreSQL, S3, and
CloudFront without redesigning everything at once.

- [Export Lightsail snapshots to EC2](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-export-to-ec2.html)
- [Connect Lightsail to other AWS services](https://docs.aws.amazon.com/lightsail/latest/userguide/using-lightsail-with-other-aws-services.html)
- [Current Lightsail pricing](https://aws.amazon.com/lightsail/pricing/)

DigitalOcean is usually the simpler operating experience. This deployment is
also compatible with an Ubuntu 24.04 DigitalOcean Droplet: assign a reserved IP,
apply an equivalent cloud firewall, and create a non-root deployment account
with SSH-key and passwordless `sudo` access before running the same provisioner.
If provisioning while logged in as `root`, pass that account as the optional
third argument. Put the Droplet's IP and deployment user in the four GitHub
secrets below. The `LIGHTSAIL_*` secret names are labels only; the deployment
protocol is ordinary SSH and Docker Compose.

## 1. Create the server and DNS

1. Create an Ubuntu 24.04 LTS Lightsail instance. Use at least 2 GB RAM for the
   application, proxy, image builds, and operating-system overhead. A larger
   plan can be selected later.
2. Attach a Lightsail static IP before creating DNS records.
3. In the Lightsail networking firewall, allow TCP 80 and TCP 443 from all
   IPv4/IPv6 addresses. Allow TCP 22 for deployment. GitHub-hosted runners have
   changing outbound addresses, so a fixed-IP SSH rule requires a self-hosted
   runner; otherwise keep port 22 public and use the provisioner's key-only SSH
   configuration.
4. Create an `A` record such as `converter.example.com` pointing to the static
   IP. Wait for it to resolve before the first deployment so Caddy can issue the
   certificate.

The service is small, but image builds need temporary memory. If a 2 GB instance
runs out of memory during builds, resize it or add a small swap file according
to your server policy.

## 2. Create a dedicated deployment key

On a trusted administrator computer, create a key with no passphrase because
GitHub Actions must use it non-interactively:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/nutrilite_actions -C nutrilite-github-actions
ssh-copy-id -i ~/.ssh/nutrilite_actions.pub ubuntu@SERVER_STATIC_IP
```

Keep the private key out of this repository. Confirm that a new terminal can
connect with the dedicated key before continuing:

```sh
ssh -i ~/.ssh/nutrilite_actions ubuntu@SERVER_STATIC_IP
```

## 3. Provision the server once

While signed in as `ubuntu`, clone the public repository and inspect the
provisioning script before running it:

```sh
git clone https://github.com/patrickhschulz/Nutrilite_Converter.git
cd Nutrilite_Converter
sudo bash deploy/lightsail/provision-ubuntu.sh converter.example.com owner@example.com
```

On a DigitalOcean image where a non-root account is not already the SSH user,
create and test that account first, then invoke the script as:

```sh
sudo bash deploy/lightsail/provision-ubuntu.sh converter.example.com owner@example.com deploy
```

Do not allow the script to disable root SSH until the `deploy` account can log
in with its key and run `sudo`.

The provisioner installs Docker from Docker's Ubuntu repository, enables
unattended package updates, disables password and root SSH login, creates
`/opt/nutrilite-converter`, generates a 256-bit app access token, and writes the
server environment file with mode `0600`. Neither the access token nor an API
key is printed. Log out and reconnect so membership in the Docker group takes
effect.

Add the server's OpenAI API key once before the first deployment:

```sh
sudoedit /opt/nutrilite-converter/.env
```

Replace only `REPLACE_WITH_OPENAI_API_KEY` on the `OPENAI_API_KEY=` line. Leave
`OPENAI_MODEL=gpt-5.4-mini` unless another supported model has been deliberately
tested. The key belongs only in this protected server file: never put it in the
PWA, an invite link, GitHub Actions, a screenshot, or this repository. Friends
do not need their own OpenAI accounts or keys; API usage is billed to the server
owner's API account. A ChatGPT subscription is separate from API billing.

To change the domain, email, persistent data location, model, or operational
limits later, edit `/opt/nutrilite-converter/.env`. Never commit that file.

## 4. Pin the SSH host identity

While connected to the server, record its authoritative Ed25519 fingerprint:

```sh
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the trusted administrator computer, collect the corresponding public host
key and verify its fingerprint matches before trusting it:

```sh
ssh-keyscan -t ed25519 -H SERVER_STATIC_IP > nutrilite_known_hosts
ssh-keygen -lf nutrilite_known_hosts
```

Do not skip the comparison. It prevents the deployment workflow from silently
connecting to an impersonated server.

## 5. Configure GitHub

In the GitHub repository, create an environment named `production`. Add
required reviewers if the account plan supports them, then add these repository
or environment secrets:

| Secret | Value |
| --- | --- |
| `LIGHTSAIL_HOST` | Static IP or deployment hostname |
| `LIGHTSAIL_USER` | `ubuntu` for the standard Lightsail image |
| `LIGHTSAIL_SSH_PRIVATE_KEY` | Complete contents of `~/.ssh/nutrilite_actions` |
| `LIGHTSAIL_KNOWN_HOSTS` | Complete verified `nutrilite_known_hosts` line |

The workflow is manual by design; ordinary pushes cannot deploy production.
Run **Actions → Deploy hosted converter → Run workflow**. A successful run ends
with a server-side health check. Then visit:

```text
https://converter.example.com/
https://converter.example.com/health
https://converter.example.com/api/products?q=vitamin%20d&comparison_only=true
```

The release build and CI smoke test deliberately use no live OpenAI key and make
no AI network request. They verify that the catalog, PWA, authentication shell,
and analysis endpoint configuration can start; a real analysis is an explicit
post-deployment check against the owner's API account.

## 6. Create the private friend link

After deployment, print the invite link in the server terminal:

```sh
sudo awk -F= '$1=="DOMAIN"{d=$2} $1=="APP_ACCESS_TOKEN"{t=$2} END{print "https://" d "/#invite=" t}' \
  /opt/nutrilite-converter/.env
```

Send that URL through a private channel. The secret is after `#`, so browsers do
not send it in the HTTP request, Caddy access log, or referrer. On first load the
PWA stores it on that device and removes it from the visible address. It is
still a bearer secret: anyone who obtains it can use the service and incur API
costs. Do not post it publicly or place it in a query parameter such as
`?token=`.

If the link leaks, replace `APP_ACCESS_TOKEN` with a new 64-character hex value
from `openssl rand -hex 32`, then redeploy. Existing installations will ask for
the new link; no client database migration is required.

## Using the PWA

Open the invite link once in the device's browser. On iPhone or iPad, use
Safari's Share menu and **Add to Home Screen**. On supported Android and desktop
browsers, use the browser's **Install** action. The installed app and ordinary
browser tab use the same server and current catalog.

A user can take or upload a supplement-label photo, paste a public product URL,
or enter product facts; confirm extracted data; then compare foundational and
targeted products with the Nutrilite/XS catalog. Optional demographics and
saved comparison history stay in that browser. Use the app's export function
before clearing site data or moving devices because there is no server account
from which to restore them.

## Operations

Inspect the active release and containers:

```sh
readlink /opt/nutrilite-converter/current
docker compose --env-file /opt/nutrilite-converter/.env \
  -f /opt/nutrilite-converter/current/deploy/lightsail/compose.production.yaml ps
```

Inspect the automated jobs:

```sh
systemctl list-timers 'nutrilite-*'
journalctl -u nutrilite-refresh.service -n 100 --no-pager
journalctl -u nutrilite-backup.service -n 100 --no-pager
```

### Cost, abuse, and upload controls

The defaults permit 20 analysis/comparison requests per hour for each invite
token/client-address pair and reject any request body larger than 8.5 MB at both
Caddy and the Python application. Decoded images are capped at 6 MB. They
are intentionally conservative for a small circle of friends. Change these
paired settings only after checking memory use and API spend:

```dotenv
ANALYSIS_RATE_LIMIT_PER_HOUR=20
MAX_REQUEST_BYTES=8500000
MAX_IMAGE_BYTES=6000000
CADDY_MAX_REQUEST_BODY_SIZE=8500000
```

After editing `/opt/nutrilite-converter/.env`, rerun the active release's
`deploy.sh` so the containers receive the new values. Keep the Caddy and Python
body limits aligned. A larger upload limit increases memory pressure because
requests and encoded images are processed in memory.

Set usage notifications and a project budget in the OpenAI platform, then
review API usage after sharing with new people. The app limit reduces accidental
bursts; it is not a billing guarantee. If usage is unexpected, rotate
`APP_ACCESS_TOKEN`, lower the hourly limit, or temporarily remove the API
key and redeploy. Catalog browsing remains local to the server, while AI-backed
photo and URL analysis will report that it is unavailable without a valid key.

Application and Caddy logs should contain request paths and operational errors,
not request bodies or bearer-token fragments. Treat logs as sensitive anyway,
keep Docker log rotation enabled, and do not add body/header debug logging in
production.

### Privacy and retention boundary

- The SQLite volume contains the public product catalog, refresh metadata, and
  catalog backups only.
- The application does not save submitted photos, pasted URLs, extracted label
  facts, demographics, or comparison results on the server.
- The user's browser owns locally saved profile/history data. Removing the PWA
  or clearing site storage can remove it; export first.
- Submitted analysis content leaves the server for the configured OpenAI API.
  Tell users this before they submit an image, and avoid names, prescriptions,
  diagnoses, or other unnecessary personal information.
- This tool provides informational product comparisons, not medical diagnosis
  or treatment. Pregnancy, medications, conditions, and known deficiencies
  need qualified clinical review.

Backups are stored under
`/opt/nutrilite-converter/shared/data/backups`. Copy them to a separate system
or object store; backups on the same virtual server are not disaster recovery.
These backups contain only the catalog in the current architecture. If private
server-side data is added later, establish encryption, retention, deletion,
access control, consent, and off-server recovery before enabling it.

To roll back application code, choose a previous 40-character directory under
`/opt/nutrilite-converter/releases` and redeploy it:

```sh
bash /opt/nutrilite-converter/releases/PREVIOUS_COMMIT/deploy/lightsail/deploy.sh \
  /opt/nutrilite-converter/releases/PREVIOUS_COMMIT
```

The SQLite database is deliberately outside release directories, so a code
rollback does not silently replace catalog data. Validate database-schema
compatibility before rolling back across a future schema migration.

## Growth path

The first scaling boundary will be features, not catalog size. Keep SQLite for
the read-heavy public catalog while traffic is modest. As private accounts,
image uploads, or concurrent writes are introduced:

1. move uploaded images to private S3 storage with short-lived access URLs;
2. move user and comparison data to RDS PostgreSQL;
3. put authentication and per-user authorization in front of every private
   endpoint;
4. run stateless application containers on ECS/Fargate or EC2 behind a load
   balancer;
5. use a queue for OCR and product-matching jobs.

Only Lightsail instance and block-storage snapshots can be exported to EC2;
Lightsail managed databases, containers, disks, and load balancers cannot be
exported as EC2 snapshots. Keep application state in portable formats and plan
the database transition explicitly.
