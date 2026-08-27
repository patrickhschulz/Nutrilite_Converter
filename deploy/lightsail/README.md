# Hosted deployment: AWS Lightsail

This distribution deploys Nutrilite Converter as two containers on an Ubuntu
24.04 virtual server:

- the dependency-free Python catalog UI/API, with SQLite on persistent host
  storage;
- Caddy, which obtains and renews HTTPS certificates and proxies public traffic.

GitHub Actions tests every deployment, builds and smoke-tests the container,
transfers an immutable source archive over verified SSH, and activates that
release. A server timer refreshes the complete Amway catalog daily when it is
at least seven days old. A second timer retains 14 daily compressed SQLite
backups.

The hosted interface intentionally exposes only public product-catalog data.
Do not add client demographics, supplement histories, or uploaded label images
to this public service until authentication, authorization, encrypted private
storage, retention controls, and a privacy policy have been added.

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
apply an equivalent cloud firewall, run the same provisioner, and put the
Droplet's IP and SSH user in the four GitHub secrets below. The `LIGHTSAIL_*`
secret names are labels only; the deployment protocol is ordinary SSH and
Docker Compose.

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

The provisioner installs Docker from Docker's Ubuntu repository, enables
unattended package updates, disables password and root SSH login, creates
`/opt/nutrilite-converter`, and writes the protected server environment file.
Log out and reconnect so membership in the Docker group takes effect.

To change the domain, email, persistent data location, or image tag later, edit
`/opt/nutrilite-converter/.env` on the server. Never commit that file.

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

Backups are stored under
`/opt/nutrilite-converter/shared/data/backups`. Copy them to a separate system
or object store; backups on the same virtual server are not disaster recovery.
The catalog itself is reproducible, but any future private user data will not be.

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
