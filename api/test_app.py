import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

from api.app import API_PREFIX, create_app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app(
            self.database,
            admin_email="admin@example.test",
            admin_password="correct horse battery staple",
            allowed_origins={"https://site.example.test"},
        )
        self.admin = self.post(
            "/auth/signin",
            {"email": "admin@example.test", "password": "correct horse battery staple"},
        )[1]["access_token"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, token=None, origin=None):
        path_info, _, query = path.partition("?")
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        environ = {}
        setup_testing_defaults(environ)
        environ.update(
            REQUEST_METHOD=method,
            PATH_INFO=f"{API_PREFIX}{path_info}",
            QUERY_STRING=query,
            CONTENT_TYPE="application/json",
            CONTENT_LENGTH=str(len(body)),
            **{"wsgi.input": io.BytesIO(body)},
        )
        if token:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if origin:
            environ["HTTP_ORIGIN"] = origin
        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = dict(headers)

        response = b"".join(self.app(environ, start_response))
        return captured["status"], json.loads(response) if response else None, captured["headers"]

    def post(self, path, payload, token=None):
        status, response, _ = self.request("POST", path, payload, token)
        return status, response["data"] if status < 400 else response["error"]

    def patch(self, path, payload, token=None):
        status, response, _ = self.request("PATCH", path, payload, token)
        return status, response["data"] if status < 400 else response["error"]

    def signup(self, path, email, **extra):
        return self.post(
            path,
            {
                "name": extra.pop("name", "Test Person"),
                "email": email,
                "phone": "+919876543210",
                "password": "long test password",
                **extra,
            },
        )

    def test_booking_lifecycle_search_notifications_and_authorization(self):
        status, vendor = self.signup(
            "/auth/vendor-signup",
            "vendor@example.test",
            business_name="Mysuru Cars",
            partner_type="fleet",
            base_city="Bengaluru",
        )
        self.assertEqual(status, 201)
        vendor_token = vendor["access_token"]
        self.assertEqual(vendor["user"]["role"], "vendor")
        status, _, _ = self.request("GET", "/admin/bookings", token=vendor_token)
        self.assertEqual(status, 403)

        status, _ = self.patch(
            f"/admin/vendors/{vendor['user']['id']}/status", {"status": "approved"}, self.admin
        )
        self.assertEqual(status, 200)
        status, vehicle = self.post(
            "/vendors/me/vehicles",
            {
                "vehicle_class": "sedan",
                "make_model": "Dzire 2024",
                "registration_number": "KA-01-AA-1234",
                "seats": 4,
                "rate_per_km": 13,
                "driver_allowance": 400,
            },
            vendor_token,
        )
        self.assertEqual(status, 201)
        status, vehicle = self.patch(
            f"/admin/vehicles/{vehicle['id']}/status", {"status": "approved"}, self.admin
        )
        self.assertEqual(status, 200)
        vehicle_id = vehicle["id"]
        pickup = datetime.now(timezone.utc) + timedelta(days=3)
        dropoff = pickup + timedelta(hours=3)
        iso = lambda value: value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        search = "/cabs/search?" + urlencode({"pickup_city": "Bangalore (Bengaluru)", "pickup_at": iso(pickup)})
        status, vendor_profile = self.patch("/vendors/me", {"business_name": "Mysuru Cars Ltd"}, vendor_token)
        self.assertEqual(status, 200)
        self.assertEqual(vendor_profile["status"], "pending")
        status, search_result, _ = self.request("GET", search)
        self.assertEqual(status, 200)
        self.assertEqual(search_result["data"], [])
        status, vendor_profile = self.patch(
            f"/admin/vendors/{vendor['user']['id']}/status", {"status": "approved"}, self.admin
        )
        self.assertEqual(status, 200)
        status, updated_vehicle = self.patch(
            f"/vendors/me/vehicles/{vehicle_id}", {"rate_per_km": 14}, vendor_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated_vehicle["status"], "pending")
        status, results, _ = self.request("GET", search)
        self.assertEqual(status, 200)
        self.assertEqual(results["data"], [])
        status, _ = self.patch(f"/admin/vehicles/{vehicle_id}/status", {"status": "approved"}, self.admin)
        self.assertEqual(status, 200)
        status, results, _ = self.request("GET", search)
        self.assertEqual(status, 200)
        self.assertEqual([cab["id"] for cab in results["data"]], [vehicle_id])

        status, results, _ = self.request("GET", search)
        self.assertEqual(status, 200)
        self.assertEqual([cab["id"] for cab in results["data"]], [vehicle_id])

        status, customer = self.signup("/auth/signup", "customer@example.test")
        self.assertEqual(status, 201)
        customer_token = customer["access_token"]
        booking_body = {
            "vehicle_id": vehicle_id,
            "trip_type": "oneway",
            "pickup_city": "Bengaluru",
            "drop_city": "Mysuru",
            "pickup_at": iso(pickup),
            "dropoff_at": iso(dropoff),
            "passengers": 2,
        }
        status, booking = self.post("/bookings", booking_body, customer_token)
        self.assertEqual(status, 201)
        booking_id = booking["id"]
        self.assertEqual(booking["status"], "requested")
        status, results, _ = self.request("GET", search)
        self.assertEqual(results["data"], [])
        status, _, _ = self.request("POST", "/bookings", booking_body, customer_token)
        self.assertEqual(status, 409)

        status, other_customer = self.signup("/auth/signup", "other@example.test")
        self.assertEqual(status, 201)
        status, _, _ = self.request("GET", f"/bookings/{booking_id}", token=other_customer["access_token"])
        self.assertEqual(status, 404)

        status, quoted = self.post(
            f"/admin/bookings/{booking_id}/quote", {"quoted_fare": 5000}, self.admin
        )
        self.assertEqual(status, 200)
        self.assertEqual(quoted["status"], "awaiting_customer_confirmation")
        status, confirmed = self.post(f"/bookings/{booking_id}/confirm", {}, customer_token)
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "confirmed")

        status, driver = self.signup("/auth/driver-signup", "driver@example.test", base_city="Bengaluru")
        self.assertEqual(status, 201)
        driver_token = driver["access_token"]
        status, _ = self.patch(f"/admin/drivers/{driver['user']['id']}/status", {"status": "approved"}, self.admin)
        self.assertEqual(status, 200)
        status, assigned = self.post(
            f"/admin/bookings/{booking_id}/assign", {"driver_id": driver["user"]["id"]}, self.admin
        )
        self.assertEqual(status, 200)
        self.assertEqual(assigned["status"], "assigned")
        status, driver_bookings, _ = self.request("GET", "/drivers/me/bookings", token=driver_token)
        self.assertEqual(status, 200)
        self.assertEqual(driver_bookings["data"][0]["id"], booking_id)
        self.assertEqual(driver_bookings["data"][0]["customer"]["phone"], "+919876543210")

        status, vendor_notifications, _ = self.request("GET", "/notifications", token=vendor_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "booking_requested" for item in vendor_notifications["data"]))
        status, driver_notifications, _ = self.request("GET", "/notifications", token=driver_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "booking_assigned" for item in driver_notifications["data"]))
        status, admin_notifications, _ = self.request("GET", "/notifications", token=self.admin)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "booking_requested" for item in admin_notifications["data"]))
        status, signed_out = self.post("/auth/signout", {}, customer_token)
        self.assertEqual(status, 200)
        self.assertTrue(signed_out["signed_out"])
        status, _, _ = self.request("GET", "/me", token=customer_token)
        self.assertEqual(status, 401)

    def test_validation_public_contact_authentication_and_cors(self):
        status, response, _ = self.request("POST", "/auth/signup", {
            "name": "No Pass",
            "email": "invalid",
            "phone": "123",
            "password": "short",
        })
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "invalid_request")
        status, response = self.signup(
            "/auth/vendor-signup",
            "bad-vendor@example.test",
            partner_type=[],
            business_name="Bad",
            base_city="Bengaluru",
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "invalid_request")
        status, response = self.patch("/admin/vendors/" + "0" * 36 + "/status", {"status": []}, self.admin)
        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "invalid_request")

        status, response, _ = self.request(
            "POST", "/contact", {"name": "Visitor", "email": "visitor@example.test", "message": "Please call me."}
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["data"]["status"], "received")
        status, response, _ = self.request(
            "POST", "/contact", {"name": "Visitor", "email": "visitor@example.test", "message": "Hi"}, origin="https://evil.example"
        )
        self.assertEqual(status, 403)

        status, response, _ = self.request("GET", "/me")
        self.assertEqual(status, 401)
        status, response, _ = self.request("GET", "/health", origin="https://site.example.test")
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["status"], "ok")
        status, response, _ = self.request("GET", "/cabs/search?pickup_city=Bengaluru&pickup_at=2026-11-01T12%3A00%3A00Z&duration_minutes=invalid")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
