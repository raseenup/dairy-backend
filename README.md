# First Milk Venture Backend (Master Edition)

Enterprise Secured Backend for First Milk Venture.

## Requirements (`requirements.txt` - 8 Packages):
```txt
fastapi>=0.110.0
uvicorn>=0.28.0
pydantic[email]>=2.7.0
pydantic-settings>=2.2.1
resend>=0.8.0
supabase>=2.3.4
email-validator>=2.0.0
apscheduler>=3.10.4
```

## Complete Endpoints & Schemas:
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
