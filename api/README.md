# Namma Uru Cab API

This repository previously contained only a static GitHub Pages demo and an Android WebView shell: it had no API framework, data models, persistence, or authentication. `app.py` adds a Python WSGI JSON API with SQLite persistence and uses Python's standard library for local development. Run it from the repository root with Python 3.10 or newer:

```sh
python -m api.app
```

The development server listens on `http://127.0.0.1:8080`; the SQLite database defaults to `api/nammaurucab.sqlite3`. Set `NAMMAURU_DB` to select another database file, `NAMMAURU_HOST` / `NAMMAURU_PORT` to change the bind address, and `NAMMAURU_CORS_ORIGINS` to a comma-separated origin allowlist (the default includes localhost and `https://jnravr-del.github.io`). To bootstrap the first administrator, set both `NAMMAURU_ADMIN_EMAIL` and `NAMMAURU_ADMIN_PASSWORD` before the first start. The admin password must be at least 16 characters. Existing administrator credentials are not reset when the service restarts.

The built-in WSGI server is for local development only. Production deployment uses the pinned Waitress WSGI server from `requirements.txt`. GitHub Pages cannot host this API; production use requires a separately hosted WSGI service, a durable/private SQLite volume or a production database, HTTPS, backups, and appropriate request rate limits.

## Online deployment (Render)

`render.yaml` defines the API service and a 1 GB persistent disk so booking records survive restarts. Render's `starter` service and persistent disk are paid resources; review current pricing before deploying. For a public booking service, a durable database and administrator bootstrap credentials must be configured.

1. In Render, choose **New + > Blueprint**, connect this repository, and select `main` after the deployment changes have been merged. You can also open [Render's Blueprint deploy page](https://render.com/deploy?repo=https://github.com/jnravr-del/NammaUruCab).
2. Set `NAMMAURU_ADMIN_EMAIL` and a unique 16–256 character `NAMMAURU_ADMIN_PASSWORD` when prompted. Keep the password private.
3. Deploy and wait for the service health check at `https://<your-render-service>/api/v1/health`.
4. Configure the deployed static site API URL. The repository's Pages URL is preconfigured as `https://nammaurucab-api.onrender.com/api/v1`; if Render assigns another host, change the `nammaurucab-api-url` meta tag in `index.html` to that service URL.

The Blueprint configures CORS for the GitHub Pages origin. If you use a different website domain, change `NAMMAURU_CORS_ORIGINS` in `render.yaml` to that exact origin (no path or trailing slash) and redeploy.

Render installs the pinned Waitress production server; it uses Render's `PORT`, binds to `0.0.0.0`, and runs four request threads. The API is not live until a Render account provisions this service; GitHub Pages only publishes the static website. Admin sign-in is the email/password set during first deployment. Vendor accounts must add cabs and wait for administrator approval before customer search will show any results.

## API conventions

All routes use `/api/v1`. JSON success responses have the form `{"data": ...}`; failures use `{"error":{"code":"...","message":"..."}}`. Authenticated routes use `Authorization: Bearer <access_token>`. Access tokens are random, expire after 12 hours, and only their SHA-256 hashes are stored. Passwords are stored using PBKDF2-HMAC-SHA256 with per-user random salts. JSON request bodies are limited to 64 KB. CORS permits the documented localhost origins and the GitHub Pages origin; requests from other browser origins are rejected.

The main cab-booking sequence is:

1. A customer signs up or signs in and searches approved vehicles by `pickup_city`, timezone-qualified ISO-8601 `pickup_at`, optional `duration_minutes` (30–10080, default 120), and optional `vehicle_class`. Bengaluru/Bangalore and Mysuru/Mysore are treated as city aliases.
2. The customer reads cab details and submits a booking request with `vehicle_id`, `trip_type`, `pickup_city`, `drop_city`, `pickup_at`, `dropoff_at`, and `passengers` (optional `notes`). The server checks vehicle approvals, passenger capacity, and overlapping bookings in one transaction. The request reserves the vehicle and creates in-app notifications for the vendor and each configured administrator.
3. An administrator quotes `quoted_fare` or rejects the request. A quote changes the state to `awaiting_customer_confirmation` and notifies the customer and vendor.
4. The customer confirms the quote; the booking becomes `confirmed`.
5. An administrator assigns an approved driver; the booking becomes `assigned`, and the driver and customer receive in-app notifications.

Booking statuses are `requested`, `awaiting_customer_confirmation`, `confirmed`, `assigned`, `cancelled`, `rejected`, and `completed`. Admins can complete an assigned booking or cancel a booking before completion. Customers can cancel their own bookings. Cabs and assigned drivers are excluded from overlapping active bookings; the app uses the submitted pickup/dropoff interval rather than estimating route duration. Changing a vendor profile or vehicle details resets its approval to pending and creates an admin notification.

## Endpoint contracts

| Method | Route | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | Public | Liveness check |
| `POST` | `/api/v1/auth/signup` | Public | Customer signup: `name`, `email`, `phone`, `password` |
| `POST` | `/api/v1/auth/vendor-signup` | Public | Vendor signup plus `business_name`, `partner_type` (`single` or `fleet`), `base_city`; starts pending approval |
| `POST` | `/api/v1/auth/driver-signup` | Public | Driver signup plus `base_city`; starts pending approval |
| `POST` | `/api/v1/auth/signin` | Public | `email`, `password`; returns bearer token |
| `POST` | `/api/v1/auth/signout` | Any signed-in role | Revoke current token |
| `GET`, `PATCH` | `/api/v1/me` | Any signed-in role | Read account or update `name` and/or `phone` |
| `GET` | `/api/v1/cabs/search` | Public | Search approved, available cabs; requires `pickup_city`, `pickup_at`; optional `duration_minutes`, `vehicle_class`, `limit` |
| `GET` | `/api/v1/cabs/{vehicle_id}` | Public | Approved cab details and rates |
| `POST` | `/api/v1/bookings` | Customer | Request a booking |
| `GET` | `/api/v1/bookings`, `/api/v1/bookings/{booking_id}` | Customer / admin | Customer bookings or admin-wide bookings; detail is private to the customer, owning vendor, assigned driver, or admin |
| `POST` | `/api/v1/bookings/{booking_id}/confirm` | Owning customer | Confirm an admin quote |
| `POST` | `/api/v1/bookings/{booking_id}/cancel` | Owning customer / admin | Cancel a booking |
| `GET`, `PATCH` | `/api/v1/vendors/me` | Vendor | Read/update vendor business name, partner type, and base city |
| `GET`, `POST` | `/api/v1/vendors/me/vehicles` | Vendor | List/add cabs; a new cab requires `vehicle_class`, `make_model`, `registration_number`, `seats`, `rate_per_km`, `driver_allowance` and starts pending approval |
| `PATCH` | `/api/v1/vendors/me/vehicles/{vehicle_id}` | Owning vendor | Update cab details |
| `GET` | `/api/v1/vendors/me/bookings` | Vendor | List bookings for the vendor's fleet |
| `GET`, `PATCH` | `/api/v1/drivers/me` | Driver | Read/update driver's base city |
| `GET` | `/api/v1/drivers/me/bookings` | Driver | List bookings assigned to the driver |
| `GET` | `/api/v1/notifications` | Any signed-in role | Read own in-app notifications; optional `limit` (1–100) |
| `POST` | `/api/v1/notifications/{notification_id}/read` | Any signed-in role | Mark own notification read |
| `GET` | `/api/v1/admin/bookings` | Admin | List/manage bookings; optional status filter and `limit` |
| `POST` | `/api/v1/admin/bookings/{booking_id}/quote` | Admin | Submit `quoted_fare` (whole rupees) |
| `POST` | `/api/v1/admin/bookings/{booking_id}/reject` | Admin | Reject a new request |
| `POST` | `/api/v1/admin/bookings/{booking_id}/assign` | Admin | Assign an approved `driver_id` to a customer-confirmed booking |
| `POST` | `/api/v1/admin/bookings/{booking_id}/complete` | Admin | Complete an assigned booking |
| `GET` | `/api/v1/admin/vendors`, `/api/v1/admin/drivers` | Admin | Review vendor/driver accounts |
| `GET` | `/api/v1/admin/vehicles` | Admin | Review cab listings before approval |
| `PATCH` | `/api/v1/admin/vendors/{user_id}/status`, `/api/v1/admin/drivers/{user_id}/status`, `/api/v1/admin/vehicles/{vehicle_id}/status` | Admin | Set `status` to `pending`, `approved`, `rejected`, or `suspended` |
| `POST` | `/api/v1/contact` | Public | Persist contact request: `name`, `email`, `message`, optional `phone` |
| `GET` | `/api/v1/admin/contact` | Admin | Review persisted contact requests |

All signup passwords must be 12–256 characters. Booking intervals must be between 30 minutes and 7 days; cab search timestamps must include a timezone. Lists default to 50 records and accept `limit=1..100`. Customer email/phone are returned to administrators, and only customer name/phone to the vendor and assigned driver on a booking. There is no SMS, email, WhatsApp, payment, maps, document-upload, or external push integration: notifications are persisted per account for the authenticated UI to poll. Vendor/driver approval and dispatch are manual admin actions. Driver-to-vendor association and dispatch policy are not modeled; administrators select any approved, non-conflicting driver.

There is no seeded cab inventory: vendors add vehicles and an administrator must approve them before they appear in search. Admin quotes are explicit whole-rupee amounts, avoiding a fabricated distance or fare calculation until routing/pricing rules exist.
