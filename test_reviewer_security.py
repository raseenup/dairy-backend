import base64
import hashlib
import unittest
from unittest.mock import patch
import reviewer_security as reviewer

class ReviewerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.password = "Test-only!Password123"
        salt = bytes(range(16))
        digest = hashlib.scrypt(self.password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
        self.env = {"FMV_REVIEWER_ENABLED": "true", "FMV_REVIEWER_EMAIL": "reviewer@example.invalid",
                    "FMV_REVIEWER_PASSWORD_HASH": "scrypt-v2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()}
    def test_valid_and_invalid_credentials(self):
        with patch.dict("os.environ", self.env):
            self.assertTrue(reviewer.authenticate("reviewer@example.invalid", self.password, "customer"))
            self.assertFalse(reviewer.authenticate("reviewer@example.invalid", "wrong-password", "customer"))
            self.assertFalse(reviewer.authenticate("other@example.invalid", self.password, "customer"))
            for portal in ("admin", "driver"):
                self.assertFalse(reviewer.authenticate("reviewer@example.invalid", self.password, portal))
    def test_disabled_and_malformed_hash(self):
        with patch.dict("os.environ", dict(self.env, FMV_REVIEWER_ENABLED="false")):
            self.assertFalse(reviewer.authenticate("reviewer@example.invalid", self.password, "customer"))
        with patch.dict("os.environ", dict(self.env, FMV_REVIEWER_PASSWORD_HASH="invalid")):
            self.assertFalse(reviewer.authenticate("reviewer@example.invalid", self.password, "customer"))
    def test_synthetic_state_independence(self):
        template = {"users": {}, "orders": {}, "products": {"demo": {"stock_quantity": 10}}}
        with patch.dict("os.environ", self.env):
            state = reviewer.isolated_state(lambda: template)
            state["products"]["demo"]["stock_quantity"] = 0
            self.assertEqual(template["products"]["demo"]["stock_quantity"], 10)
            self.assertEqual(template["users"], {})
            self.assertEqual(state["users"]["customer:reviewer@example.invalid"]["role"], "customer")

if __name__ == "__main__":
    unittest.main()
