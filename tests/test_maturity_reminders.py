"""Tests for P6 maturity reminders: payment entry updates maturity + daily cron notifications."""

import pytest
import os
from datetime import date, timedelta
from raas_tracker.sales import (
    record_sale_payment,
    get_sale_by_id,
    get_sale_invoice_total,
    get_sale_total_paid,
)
from raas_tracker.notifications import (
    notify_maturity_initial,
    notify_maturity_escalation,
    clear_maturity_dedupe,
    clear_dedupe,
    list_notifications_for,
    notify,
)
from chem_stock import add_sale, create_company, get_connection


def _create_company(db, name, code=None):
    """Helper to create company with correct signature."""
    return create_company(db, name=name, code=code)


class TestPaymentMaturityUpdate:
    """Tests for maturity_date handling in record_sale_payment."""

    def test_record_payment_updates_maturity_when_null(self, db):
        """Payment with maturity_date updates sale header when NULL."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-001",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Verify maturity_date is initially NULL
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] is None
        
        # Record payment with maturity_date
        result = record_sale_payment(db, sale_id, "2025-02-01", 1000, "First payment", maturity_date="2025-03-01")
        
        # Verify maturity_date was updated
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] == "2025-03-01"
        assert result["payment_id"] is not None

    def test_record_payment_updates_maturity_when_changed(self, db):
        """Payment with maturity_date updates sale header when different from existing."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-002",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",  # Existing maturity
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Record payment with DIFFERENT maturity_date
        result = record_sale_payment(db, sale_id, "2025-02-01", 1000, "First payment", maturity_date="2025-04-01")
        
        # Verify maturity_date was updated to new value
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] == "2025-04-01"

    def test_record_payment_keeps_maturity_when_same(self, db):
        """Payment with same maturity_date does not cause unnecessary update."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-003",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Record payment with SAME maturity_date
        result = record_sale_payment(db, sale_id, "2025-02-01", 1000, "First payment", maturity_date="2025-03-01")
        
        # Verify maturity_date unchanged
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] == "2025-03-01"

    def test_record_payment_ignores_empty_maturity(self, db):
        """Payment with empty maturity_date does not update existing."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-004",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Record payment with empty maturity_date
        result = record_sale_payment(db, sale_id, "2025-02-01", 1000, "First payment", maturity_date="")
        
        # Verify maturity_date unchanged
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] == "2025-03-01"

    def test_record_payment_none_maturity_no_update(self, db):
        """Payment with None maturity_date does not update existing."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-005",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Record payment with None maturity_date
        result = record_sale_payment(db, sale_id, "2025-02-01", 1000, "First payment", maturity_date=None)
        
        # Verify maturity_date unchanged
        sale = get_sale_by_id(db, sale_id)
        assert sale["maturity_date"] == "2025-03-01"


class TestFullPaymentClearsMaturityDedupe:
    """Tests that full payment clears maturity dedupe keys."""

    def test_full_payment_clears_initial_dedupe(self, db):
        """Full payment clears maturity initial dedupe key."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-006",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Create a maturity notification (sets dedupe key)
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        
        # Verify notification exists
        ident = {"id": 1, "role": "admin"}  # admin user from fixture
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 1
        
        # Full payment - should clear dedupe key (deletes the notification)
        record_sale_payment(db, sale_id, "2025-02-01", 5000, "Full payment")
        
        # Verify notification was deleted (dedupe key cleared)
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 0
        
        # Can now notify again - creates new notification
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 1  # New notification after clear

    def test_full_payment_clears_escalation_dedupe(self, db):
        """Full payment clears maturity escalation dedupe key."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-007",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-02-20",  # 8 days ago from test "today"
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Create escalation notification
        notify_maturity_escalation(db, sale_id, "Test Client", "2025-02-20", 8)
        
        # Verify notification exists
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        esc_notifs = [n for n in notifications if n["type"] == "maturity_escalated"]
        assert len(esc_notifs) == 1
        
        # Full payment - should clear dedupe key (deletes the notification)
        record_sale_payment(db, sale_id, "2025-02-01", 5000, "Full payment")
        
        # Verify notification was deleted
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        esc_notifs = [n for n in notifications if n["type"] == "maturity_escalated"]
        assert len(esc_notifs) == 0
        
        # Can now notify again - creates new notification
        notify_maturity_escalation(db, sale_id, "Test Client", "2025-02-20", 8)
        
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        esc_notifs = [n for n in notifications if n["type"] == "maturity_escalated"]
        assert len(esc_notifs) == 1  # New notification after clear

    def test_partial_payment_does_not_clear_dedupe(self, db):
        """Partial payment does NOT clear maturity dedupe keys."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-008",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": "2025-03-01",
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Create a maturity notification
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        
        # Partial payment (not enough to cover invoice)
        record_sale_payment(db, sale_id, "2025-02-01", 1000, "Partial payment")
        
        # Try to notify again - should be deduped (key NOT cleared)
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 1  # Still only 1, dedupe worked


class TestCronEndpoint:
    """Tests for POST /api/cron/maturity-check endpoint."""

    def test_cron_endpoint_401_without_secret(self, client, monkeypatch):
        """No Authorization header -> 401 (or 500 if CRON_SECRET not configured)."""
        # Unset CRON_SECRET to test missing config
        monkeypatch.delenv("CRON_SECRET", raising=False)
        response = client.post("/api/cron/maturity-check")
        # Returns 500 when CRON_SECRET not configured (server config error)
        assert response.status_code == 500
        data = response.get_json()
        assert data["error"] == "CRON_SECRET not configured"

    def test_cron_endpoint_401_wrong_secret(self, client, monkeypatch):
        """Wrong Bearer token -> 401."""
        monkeypatch.setenv("CRON_SECRET", "correct-secret")
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer wrong-secret"}
        )
        assert response.status_code == 401

    def test_cron_endpoint_200_with_secret(self, client, monkeypatch, db):
        """Correct secret, no matching sales -> success."""
        # Need to set env var before the app reads it
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "checked" in data

    def test_cron_notifies_initial(self, client, monkeypatch, db):
        """Sale with maturity today, unpaid -> initial notification created."""
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        
        company_id = _create_company(db, "Test Client", code="TC")
        today = date.today().isoformat()
        sale_id = add_sale(db, {
            "pi_number": "PI-009",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": today,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        
        # Check notification was created
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 1
        assert f"Sale #{sale_id}" in initial_notifs[0]["body"]
        assert initial_notifs[0]["dedupe_key"] == f"maturity:{sale_id}:initial"

    def test_cron_notifies_escalation(self, client, monkeypatch, db):
        """Sale with maturity 8 days ago, unpaid -> both initial + escalation notifications."""
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        
        company_id = _create_company(db, "Test Client", code="TC")
        eight_days_ago = (date.today() - timedelta(days=8)).isoformat()
        sale_id = add_sale(db, {
            "pi_number": "PI-010",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": eight_days_ago,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        esc_notifs = [n for n in notifications if n["type"] == "maturity_escalated"]
        
        assert len(initial_notifs) == 1
        assert len(esc_notifs) == 1
        assert esc_notifs[0]["dedupe_key"] == f"maturity:{sale_id}:escalated"
        assert "8 days overdue" in esc_notifs[0]["body"] or "overdue" in esc_notifs[0]["body"].lower()

    def test_cron_no_notify_when_paid(self, client, monkeypatch, db):
        """Fully paid sale -> no notification."""
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        
        company_id = _create_company(db, "Test Client", code="TC")
        today = date.today().isoformat()
        sale_id = add_sale(db, {
            "pi_number": "PI-011",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": today,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Fully pay the sale
        record_sale_payment(db, sale_id, "2025-02-01", 5000, "Full payment")
        
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 0

    def test_cron_no_notify_future_maturity(self, client, monkeypatch, db):
        """Maturity in future -> no notification."""
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        
        company_id = _create_company(db, "Test Client", code="TC")
        future_date = (date.today() + timedelta(days=30)).isoformat()
        sale_id = add_sale(db, {
            "pi_number": "PI-012",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": future_date,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        response = client.post(
            "/api/cron/maturity-check",
            headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 0

    def test_cron_dedupe_prevents_duplicate_notifications(self, client, monkeypatch, db):
        """Running cron twice doesn't create duplicate notifications."""
        os.environ["CRON_SECRET"] = "test-secret"
        monkeypatch.setenv("CRON_SECRET", "test-secret")
        
        company_id = _create_company(db, "Test Client", code="TC")
        today = date.today().isoformat()
        sale_id = add_sale(db, {
            "pi_number": "PI-013",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
            "maturity_date": today,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # First run
        client.post("/api/cron/maturity-check", headers={"Authorization": "Bearer test-secret"})
        # Second run
        client.post("/api/cron/maturity-check", headers={"Authorization": "Bearer test-secret"})
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_notifs = [n for n in notifications if n["type"] == "maturity_due"]
        assert len(initial_notifs) == 1  # Only one due to dedupe


class TestNotificationHelpers:
    """Tests for the notification helper functions in notifications.py."""

    def test_notify_maturity_initial_creates_notification(self, db):
        """notify_maturity_initial creates notification with correct fields."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-014",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        notif = next(n for n in notifications if n["type"] == "maturity_due")
        
        assert notif["title"] == "Maturity due: Test Client"
        assert "2025-03-01" in notif["body"]
        assert notif["severity"] == "warning"
        assert notif["entity_type"] == "sale"
        assert notif["entity_id"] == sale_id
        assert notif["dedupe_key"] == f"maturity:{sale_id}:initial"

    def test_notify_maturity_escalation_creates_notification(self, db):
        """notify_maturity_escalation creates notification with correct fields."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-015",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        notify_maturity_escalation(db, sale_id, "Test Client", "2025-02-20", 10)
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        notif = next(n for n in notifications if n["type"] == "maturity_escalated")
        
        assert notif["title"] == "Maturity escalated: Test Client"
        assert "10 days overdue" in notif["body"] or "overdue" in notif["body"].lower()
        assert notif["severity"] == "critical"
        assert notif["entity_type"] == "sale"
        assert notif["entity_id"] == sale_id
        assert notif["dedupe_key"] == f"maturity:{sale_id}:escalated"

    def test_clear_maturity_dedupe_clears_both_keys(self, db):
        """clear_maturity_dedupe clears both initial and escalation keys."""
        company_id = _create_company(db, "Test Client", code="TC")
        sale_id = add_sale(db, {
            "pi_number": "PI-016",
            "pi_date": "2025-01-15",
            "client_name": "Test Client",
            "company_id": company_id,
        }, [
            {"product_name": "Product A", "quantity": 100, "unit_price": 50, "unit": "KG"}
        ])
        
        # Create both notifications
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        notify_maturity_escalation(db, sale_id, "Test Client", "2025-03-01", 7)
        
        ident = {"id": 1, "role": "admin"}
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_count = len([n for n in notifications if n["type"] == "maturity_due"])
        esc_count = len([n for n in notifications if n["type"] == "maturity_escalated"])
        assert initial_count == 1
        assert esc_count == 1
        
        # Clear dedupe - deletes both notifications
        clear_maturity_dedupe(db, sale_id)
        
        # Verify both notifications were deleted
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_count = len([n for n in notifications if n["type"] == "maturity_due"])
        esc_count = len([n for n in notifications if n["type"] == "maturity_escalated"])
        assert initial_count == 0
        assert esc_count == 0
        
        # Can now notify again for both
        notify_maturity_initial(db, sale_id, "Test Client", "2025-03-01")
        notify_maturity_escalation(db, sale_id, "Test Client", "2025-03-01", 7)
        
        notifications = list_notifications_for(db, ident["id"], ident["role"])
        initial_count = len([n for n in notifications if n["type"] == "maturity_due"])
        esc_count = len([n for n in notifications if n["type"] == "maturity_escalated"])
        assert initial_count == 1
        assert esc_count == 1