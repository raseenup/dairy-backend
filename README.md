# First Milk Venture Backend (Original Verbose Master)

Production-Grade Enterprise Secured Backend for First Milk Venture.
Contains all original HTML email templates, full inline docstrings, rich multi-byte Unicode emojis (🎁, 🥛, 🔑, 🎉, 📦), and complete Pydantic request models.

## Complete Endpoint List:
- `POST /auth/request-otp` (`RequestOTP`)
- `POST /auth/verify-otp` (`VerifyOTP`)
- `GET /users/me`
- `GET /products`
- `POST /subscriptions` (`SubscriptionCreate`)
- `GET /subscriptions/{user_email}`
- `GET /deliveries/{user_email}`
- `GET /offers`
- `POST /admin/products` (`ProductCreate`)
- `PATCH /admin/products/{product_id}` (`ProductUpdate`)
- `DELETE /admin/products/{product_id}`
- `POST /admin/generate-deliveries` (`DeliveryGenerationRequest`)
- `PATCH /admin/deliveries/{delivery_id}/status` (`DeliveryStatusUpdate`)
- `POST /admin/offers` (`OfferCreate`)
- `POST /admin/offers/broadcast` (`BroadcastOffer`)

## Environment Variables:
- `ADMIN_SECRET_KEY`: `Firstmilk3211234`
- `SUPABASE_URL`: Your Supabase URL
- `SUPABASE_SERVICE_ROLE_KEY`: Your Supabase Service Role Key
- `RESEND_API_KEY`: Your Resend API Key
