# Deploy SilentDeluge behind nginx

Run this unit on the same host and in the same network namespace as a trusted nginx instance. nginx terminates HTTPS; `127.0.0.1:18765` is the HTTP upstream, never an externally published port. Loopback is not a boundary against other local processes: deploy on a host where local workloads are trusted with upstream access.

Use Python 3.11+ at `/usr/bin/python3`, systemd 249+ with cgroup v2 and kernel support for systemd's IP and socket-bind filters, and a release whose `web_inventory.py serve` supports `--public-origin`. Do not use the direct-loopback CLI as a substitute. The service runs without capabilities, with a read-only app tree, private state and temporary files, and IPv4 loopback-only networking. Use approved saved provider inputs; this unit is not intended to reach remote collectors or local Unix sockets. See [WEB.md](../WEB.md) for the web config format and [COLLECTION.md](../COLLECTION.md) for provider contracts.

## Install

1. As root, create the dedicated nonlogin account and private state directory (once per host; use your distribution's `nologin` path):

   ```sh
   groupadd --system silentdeluge-web
   useradd --system --gid silentdeluge-web --home-dir /var/lib/silentdeluge-web \
     --no-create-home --shell /usr/sbin/nologin silentdeluge-web
   install -d -o silentdeluge-web -g silentdeluge-web -m 0700 /var/lib/silentdeluge-web
   install -d -o root -g root -m 0755 /opt/silentdeluge /opt/silentdeluge/releases
   ```

2. Install the complete application release (including `web_static/` and the Python modules) in `/opt/silentdeluge/releases/<release-id>`. The release and its parent directories must be root-owned, readable/traversable by the service, and not writable by the service account. Do not copy a working tree that contains local config or data. Set `release` to the installed release ID and create the initial link:

   ```sh
   ln -sT -- "releases/$release" /opt/silentdeluge/current
   ```

3. Create `/var/lib/silentdeluge-web/web.json` as a regular file owned by `silentdeluge-web:silentdeluge-web`, mode `0600`, with `"state_dir": "/var/lib/silentdeluge-web"`. For an empty source allowlist, use `"sources": {}` and configure at least one user. Generate each password verifier interactively with `/usr/bin/python3 -B /opt/silentdeluge/current/web_inventory.py hash-password`; put only the verifier in the private web config. Keep config, provider inputs, credentials, SQLite files and backups outside Git and the release tree. Use absolute paths under the private state directory for saved provider inputs. Stop the unit before replacing the web config.

4. Create `/etc/silentdeluge-web.env`, owned by `root:root`, mode `0600`. It contains only the public origin, not credentials. Use the exact browser HTTPS origin, including a nondefault port if applicable, with no path or trailing slash:

   ```ini
   PUBLIC_ORIGIN=https://inventory.example.com
   ```

5. In nginx, use a dedicated HTTPS virtual host for that authority, with a valid certificate and unmatched hosts routed elsewhere or rejected. Do not serve the app or accept sign-in over plaintext HTTP. Example `location` inside that HTTPS virtual host:

   ```nginx
   location / {
       proxy_pass http://127.0.0.1:18765;
       proxy_set_header Host $http_host;
       proxy_set_header Forwarded "";
       proxy_set_header X-Forwarded-For "";
       proxy_set_header X-Forwarded-Host "";
       proxy_set_header X-Forwarded-Proto "";
       proxy_set_header X-Forwarded-Port "";
       proxy_set_header X-Real-IP "";
       proxy_cache off;
   }
   ```

   Preserve the browser's `Origin` and `Cookie` request headers and the application's `Set-Cookie` response header; nginx passes them by default. Never synthesize `Origin` from the configured public origin: the application checks the browser's value on every POST, including sign-in and logout. Do not log cookies or request bodies. Apply the nginx configuration using your site's normal reload procedure.

6. Install and start the unit as root from the release source:

   ```sh
   install -o root -g root -m 0644 deploy/silentdeluge-web.service /etc/systemd/system/silentdeluge-web.service
   systemctl daemon-reload
   systemctl enable --now silentdeluge-web.service
   ```

   Open the configured HTTPS URL without a session. The app redirects to `/login`; submit a configured username and password and confirm the workspace shows only that account's permitted sources. Select **Log out** and confirm that the app returns to `/login` and a new workspace request redirects there again. Logout revokes that browser session. If startup fails, inspect `journalctl -u silentdeluge-web.service` on the server. Do not share private config or logs unredacted.

## Update and roll back

Stage the next release in a new root-owned directory under `releases/`; never modify the active release in place. Record the current link target and retain that release. Stop the unit and make a protected, consistent backup of the private state directory before the first start on the new version. Keep rollback copies of any unit, environment or nginx configuration you change.

Set `release` to the staged release ID. Replace the symlink atomically, then start the stopped unit. If the unit file changed, install it and run `systemctl daemon-reload` before starting. Deploy nginx and `PUBLIC_ORIGIN` changes together if the public authority changes.

```sh
ln -sT -- "releases/$release" /opt/silentdeluge/current.next &&
  mv -Tf -- /opt/silentdeluge/current.next /opt/silentdeluge/current &&
  systemctl start silentdeluge-web.service
```

Confirm HTTPS sign-in and logout as above. While signed in with a permitted account, confirm an inventory view can be enabled or disabled. Do not trigger a collection just to verify deployment.

To roll back, stop the unit, set `release` to the recorded previous release ID, and use the same link swap and start procedure. Restore the previous unit or matching origin/nginx settings before starting if they changed. Keep the on-disk state if it is compatible with the previous release. If the previous release cannot read it, preserve a separate copy of the stopped state and restore the pre-update backup with the original ownership and modes before starting. Restoring that backup discards writes made since it was taken. Confirm the same HTTPS flow after rollback.
