"""
services/notification_service.py — Dashin Research Platform
In-portal notifications + email alerts.
"""

import smtplib
import os
import logging
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from core.db import get_connection

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_NAME = os.getenv("FROM_NAME", "Dashin Research")


def create(org_id: int, user_id: int, ntype: str,
           title: str, body: str = "", link_to: str = "",
           client_id: int = None, send_email: bool = False):
    """Create an in-portal notification. Optionally send email."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO notifications
            (org_id, user_id, client_id, type, title, body,
             link_to, is_read, created_at)
        VALUES (?,?,?,?,?,?,?,0,?)
    """, (org_id, user_id, client_id, ntype, title, body,
          link_to, datetime.utcnow().isoformat()))
    conn.commit()

    if send_email and SMTP_HOST:
        user = conn.execute(
            "SELECT email, name FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if user:
            _send_email(user["email"], user["name"], title, body)

    conn.close()


def notify_campaign_ready(org_id: int, campaign_id: int,
                           campaign_name: str, client_id: int):
    """Notify all client users when a campaign is marked ready to view."""
    conn = get_connection()
    client_users = conn.execute("""
        SELECT id, email, name FROM users
        WHERE org_id=? AND client_id=? AND is_active=1
          AND role IN ('client_admin','client_user')
    """, (org_id, client_id)).fetchall()

    now = datetime.utcnow().isoformat()
    for u in client_users:
        conn.execute("""
            INSERT INTO notifications
                (org_id, user_id, client_id, type, title, body,
                 link_to, is_read, created_at)
            VALUES (?,?,?,?,?,?,?,0,?)
        """, (org_id, u["id"], client_id,
              "campaign_ready",
              f"Campaign ready: {campaign_name}",
              "Your campaign data is now available to view.",
              f"/campaigns/{campaign_id}",
              now))

        if SMTP_HOST:
            _send_email(
                u["email"], u["name"],
                f"Your campaign '{campaign_name}' is ready",
                f"Hi {u['name']},\n\nYour campaign '{campaign_name}' "
                f"has been marked as ready to view.\n\n"
                f"Log in to your Dashin portal to see the latest data.\n\n"
                f"— {FROM_NAME}"
            )

    conn.commit()
    conn.close()


def notify_meeting_booked(org_id: int, campaign_id: int,
                           lead_name: str, meeting_date: str,
                           client_id: int):
    """Notify client users when a meeting is confirmed."""
    conn = get_connection()
    client_users = conn.execute("""
        SELECT id FROM users
        WHERE org_id=? AND client_id=? AND is_active=1
          AND role IN ('client_admin','client_user')
    """, (org_id, client_id)).fetchall()

    now = datetime.utcnow().isoformat()
    for u in client_users:
        conn.execute("""
            INSERT INTO notifications
                (org_id, user_id, client_id, type, title, body,
                 link_to, is_read, created_at)
            VALUES (?,?,?,?,?,?,?,0,?)
        """, (org_id, u["id"], client_id,
              "meeting_booked",
              f"Meeting confirmed: {lead_name}",
              f"Meeting scheduled for {meeting_date}.",
              f"/campaigns/{campaign_id}",
              now))

    conn.commit()
    conn.close()


def get_unread(user_id: int, limit: int = 20) -> list:
    """Get unread notifications for a user."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id=? AND is_read=0
        ORDER BY created_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all(user_id: int, limit: int = 50) -> list:
    """Get all notifications for a user."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_read(notification_id: int, user_id: int):
    conn = get_connection()
    conn.execute("""
        UPDATE notifications SET is_read=1
        WHERE id=? AND user_id=?
    """, (notification_id, user_id))
    conn.commit()
    conn.close()


def mark_all_read(user_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def unread_count(user_id: int) -> int:
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
        (user_id,)
    ).fetchone()["c"]
    conn.close()
    return n


def _build_email(to_name: str, to_email: str, subject: str,
                 body_text: str, body_html: str = None) -> MIMEMultipart:
    """Build a properly encoded MIME email message."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"{FROM_NAME} <{SMTP_USER}>"
    msg['To']      = f"{to_name} <{to_email}>"
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    return msg


def _send_email(to_email: str, to_name: str, subject: str, body: str):
    """Send email asynchronously. Fails gracefully if SMTP not configured."""
    if not SMTP_HOST or not SMTP_USER:
        return

    def _do_send():
        try:
            msg = _build_email(to_name, to_email, subject, body)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, to_email, msg.as_string())
        except Exception as e:
            logging.warning(f"[notification_service] Email failed to {to_email}: {e}")

    thread = threading.Thread(target=_do_send, daemon=True)
    thread.start()
