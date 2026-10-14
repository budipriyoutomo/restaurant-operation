"""Integration — photos on an Issue (Todo-Pilot §20 / UX-7).

Staff attach a photo when they report a problem. Same storage pipeline as
work-order photos: validated, EXIF/GPS stripped, thumbnailed, served only
through authenticated, outlet-scoped routes; uploads are idempotent so the
offline queue can retry them.
"""

import io

import piexif
import pytest
from PIL import Image

from app.models.outlet import Outlet
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_iph@test.test", "admin")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_iph@test.test", "staff")


@pytest.fixture()
def staff_bdg(client, db):
    bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
    return seed_user_headers(db, "staff_bdg_iph@test.test", "staff", outlets=[bdg])


def _jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (900, 700), (120, 140, 160))
    exif = piexif.dump({"GPS": {piexif.GPSIFD.GPSLatitudeRef: b"S", piexif.GPSIFD.GPSLatitude: [(6, 1), (12, 1), (0, 1)]}})
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _issue(client, headers):
    res = client.post("/api/issues", json={
        "title": "Atap bocor", "description": "Air menetes di dapur", "category": "Maintenance",
        "priority": "medium", "outlet": "Jakarta", "generateTask": False,
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _upload(client, headers, issue_id, key=None, caption="Bocor di atas kompor"):
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    return client.post(f"/api/issues/{issue_id}/photos",
                       files={"file": ("bocor.jpg", _jpeg_with_gps(), "image/jpeg")},
                       data={"caption": caption}, headers=h)


def test_reporter_attaches_a_photo(client, staff):
    issue = _issue(client, staff)
    res = _upload(client, staff, issue["id"])
    assert res.status_code == 201, res.text
    photo = res.json()
    assert photo["caption"] == "Bocor di atas kompor"
    assert photo["fileUrl"].endswith("/file") and photo["thumbnailUrl"].endswith("/thumbnail")
    listed = client.get(f"/api/issues/{issue['id']}/photos", headers=staff).json()
    assert [p["id"] for p in listed] == [photo["id"]]
    assert client.get(f"/api/issues/{issue['id']}", headers=staff).json()["photoCount"] == 1


def test_gps_is_stripped_and_thumbnail_is_small(client, staff):
    issue = _issue(client, staff)
    photo = _upload(client, staff, issue["id"]).json()
    full = Image.open(io.BytesIO(client.get(photo["fileUrl"], headers=staff).content))
    assert not (full.getexif().get_ifd(0x8825) if full.getexif() else {})
    thumb = Image.open(io.BytesIO(client.get(photo["thumbnailUrl"], headers=staff).content))
    assert max(thumb.size) <= 400


def test_retried_upload_is_not_duplicated(client, staff):
    issue = _issue(client, staff)
    a = _upload(client, staff, issue["id"], key="photo-key-1").json()
    b = _upload(client, staff, issue["id"], key="photo-key-1").json()
    assert a["id"] == b["id"]
    assert len(client.get(f"/api/issues/{issue['id']}/photos", headers=staff).json()) == 1


def test_other_outlet_cannot_see_or_add(client, staff, staff_bdg):
    issue = _issue(client, staff)
    photo = _upload(client, staff, issue["id"]).json()
    assert client.get(f"/api/issues/{issue['id']}/photos", headers=staff_bdg).status_code == 404
    assert client.get(photo["fileUrl"], headers=staff_bdg).status_code == 404
    assert _upload(client, staff_bdg, issue["id"]).status_code == 404


def test_not_an_image_is_rejected(client, staff):
    issue = _issue(client, staff)
    res = client.post(f"/api/issues/{issue['id']}/photos",
                      files={"file": ("x.txt", b"hello", "text/plain")}, headers=staff)
    assert res.status_code in (415, 422)
