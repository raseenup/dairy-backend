# First Milk Venture portal sessions

Deploy the API changes first and wait for the Render deployment to become healthy. Then release the three portals. People using an older portal session will sign in with their password and email code once. Previously issued API sessions retain their normal expiry.

The production customer, admin and delivery sites call `https://backend.firstmilkventure.com`. Include the three exact HTTPS portal origins in `ALLOWED_ORIGINS` along with other origins the existing services use. The browser API uses a Secure, HttpOnly, SameSite=Strict cookie on the API host and a separate per-tab session identifier. The server checks the portal origin, session and role. Keep the sites on the same HTTPS site for this configuration. For a separately named same-site staging portal, configure its exact origin in both `ALLOWED_ORIGINS` and `FMV_CUSTOMER_ORIGINS`, `FMV_ADMIN_ORIGINS` or `FMV_DRIVER_ORIGINS` and enable `browserCookies` in that portal's public configuration. Existing native API clients continue to support bearer authentication.

Use test accounts to verify sign-in, sign-out, private downloads and each portal’s normal workflow before completing the rollout. Check the actual document response on each custom hostname for an enforcing Content-Security-Policy and Strict-Transport-Security. An HTML meta policy provides partial protection. HSTS and the CSP frame-ancestors directive require HTTP response headers.

## Portal response headers

The current portal CNAMEs point to GitHub Pages. The Render configuration in `infra/portal-hosting.yaml` defines three static sites using the same GitHub repositories and publishes only their runtime files. Each site sends the existing portal CSP as an HTTP header, disallows framing, sends HSTS for its own hostname, and retains access to the camera, microphone and location for portal features.

To use this hosting option, review the Render workspace's custom-domain allowance and proposed services, select the Blueprint file, and verify the three Render builds. Change the individual customer, delivery and admin CNAMEs at GoDaddy to the corresponding destinations shown by Render. Keep the existing DNS targets available for rollback. Check TLS, both response headers and each portal's login on its custom domain before retiring the previous host. No API, email or apex DNS changes are required for this cutover.
