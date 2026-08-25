"""Integration tests — real work-order photo uploads (Tier 5.1)."""

import io

import piexif
import pytest
from PIL import Image
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_upload@test.test", "admin")


@pytest.fixture()
def wo_id(client, admin):
    asset = client.post("/api/assets", json={
        "name": "Chiller Upload", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    return client.post("/api/work-orders", json={
        "assetId": asset["id"], "type": "corrective", "title": "Servis", "priority": "high",
    }, headers=admin).json()["id"]


def _jpeg_with_gps(width=800, height=600) -> bytes:
    """A JPEG carrying an EXIF GPS tag — the exact privacy hazard we must strip."""
    img = Image.new("RGB", (width, height), (120, 140, 160))
    gps = {piexif.GPSIFD.GPSLatitudeRef: b"S",
           piexif.GPSIFD.GPSLatitude: [(6, 1), (12, 1), (0, 1)]}
    exif_bytes = piexif.dump({"GPS": gps, "0th": {piexif.ImageIFD.Make: b"FieldPhone"}})
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_bytes)
    return buf.getvalue()


def _png(width=300, height=200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 200, 10)).save(buf, format="PNG")
    return buf.getvalue()


class TestUpload:
    def test_upload_returns_serve_urls(self, client, admin, wo_id):
        res = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("kerja.jpg", _jpeg_with_gps(), "image/jpeg")},
            data={"caption": "Setelah perbaikan"},
            headers=admin,
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["isUpload"] is True
        assert body["caption"] == "Setelah perbaikan"
        assert body["fileUrl"].endswith("/file")
        assert body["thumbnailUrl"].endswith("/thumbnail")
        assert body["mimeType"] == "image/jpeg"

    def test_exif_gps_is_stripped(self, client, admin, wo_id):
        raw = _jpeg_with_gps()
        # sanity: the source really does carry GPS
        assert Image.open(io.BytesIO(raw)).getexif(), "test fixture should have EXIF"

        att = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("gps.jpg", raw, "image/jpeg")}, headers=admin,
        ).json()

        served = client.get(att["fileUrl"], headers=admin)
        assert served.status_code == 200
        stored = Image.open(io.BytesIO(served.content))
        exif = stored.getexif()
        gps = exif.get_ifd(0x8825) if exif else {}
        assert not gps, "GPS EXIF must not survive upload (employee location is personal data)"

    def test_thumbnail_is_smaller(self, client, admin, wo_id):
        att = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("big.jpg", _jpeg_with_gps(2000, 1500), "image/jpeg")}, headers=admin,
        ).json()
        thumb = client.get(att["thumbnailUrl"], headers=admin)
        assert thumb.status_code == 200
        w, h = Image.open(io.BytesIO(thumb.content)).size
        assert max(w, h) <= 400

    def test_png_accepted(self, client, admin, wo_id):
        res = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("x.png", _png(), "image/png")}, headers=admin,
        )
        assert res.status_code == 201

    def test_shows_up_in_wo_detail(self, client, admin, wo_id):
        client.post(f"/api/work-orders/{wo_id}/attachments/upload",
                    files={"file": ("a.jpg", _jpeg_with_gps(), "image/jpeg")}, headers=admin)
        detail = client.get(f"/api/work-orders/{wo_id}", headers=admin).json()
        assert len(detail["attachments"]) == 1
        assert detail["attachments"][0]["isUpload"] is True


class TestValidation:
    def test_rejects_non_image_mime(self, client, admin, wo_id):
        res = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")}, headers=admin,
        )
        assert res.status_code == 422

    def test_rejects_renamed_non_image(self, client, admin, wo_id):
        """A text file relabelled as image/jpeg must be rejected on decode."""
        res = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("fake.jpg", b"this is not an image", "image/jpeg")}, headers=admin,
        )
        assert res.status_code == 422

    def test_rejects_oversize(self, client, admin, wo_id, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 0)  # nothing passes
        res = client.post(
            f"/api/work-orders/{wo_id}/attachments/upload",
            files={"file": ("big.jpg", _jpeg_with_gps(), "image/jpeg")}, headers=admin,
        )
        assert res.status_code == 413


class TestScoping:
    def test_manager_cannot_upload_to_other_outlet_wo(self, client, db, admin):
        from app.models.outlet import Outlet
        bdg = db.query(Outlet).filter(Outlet.name == "Bandung").first()
        jkt = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
        mgr_jkt = seed_user_headers(db, "mgr_up_jkt@test.test", "manager", outlets=[jkt])

        asset = client.post("/api/assets", json={
            "name": "Aset Bandung", "category": "HVAC", "outlet": "Bandung", "status": "operational",
        }, headers=admin).json()
        wo = client.post("/api/work-orders", json={
            "assetId": asset["id"], "type": "corrective", "title": "X", "priority": "low",
        }, headers=admin).json()

        res = client.post(
            f"/api/work-orders/{wo['id']}/attachments/upload",
            files={"file": ("a.jpg", _jpeg_with_gps(), "image/jpeg")}, headers=mgr_jkt,
        )
        assert res.status_code == 404   # WO invisible to this outlet → can't attach
