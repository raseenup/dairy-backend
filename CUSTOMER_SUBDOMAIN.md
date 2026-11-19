# First Milk Venture — customer portal subdomain

Target customer portal origin: `https://customer.firstmilkventure.com`

The backend's default `ALLOWED_ORIGINS` now includes the exact origin above. This allows the existing customer portal (with bearer-token authentication and OTP) to call `https://backend.firstmilkventure.com` from the new hostname. It does **not** weaken token validation, access control, database RLS, or permit arbitrary origins.

**Important:** `secure_backend.py` reads the `ALLOWED_ORIGINS` environment variable, overriding defaults when it is set. If Render has this variable configured, add `https://customer.firstmilkventure.com` to the comma-separated list while retaining existing trusted admin/delivery/backend origins. Never use `*`.

Suggested check after deployment:

```sh
curl -i -X OPTIONS 'https://backend.firstmilkventure.com/auth/signin' \
  -H 'Origin: https://customer.firstmilkventure.com' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

Confirm that the response allows the exact customer origin and that a request from an unapproved origin is denied. A successful CORS preflight does not mean authentication has been bypassed.

DNS, TLS, GitHub Pages `CNAME` and Render static-site custom domains must be configured and verified separately. Do not switch the existing `www` hostname until the marketing site is ready to take traffic.
