import json
import stat

import pytest

from model_router import grants
from tests.helpers import NOW


def issue(state_dir, mode="write", now=NOW, cwd="/work", model=None, effort="xhigh"):
    return grants.issue(state_dir, mode, effort, model, cwd, now)


def grant_file(state_dir, nonce):
    return state_dir / "grants" / (nonce + ".json")


def test_a_grant_is_redeemed_exactly_once(tmp_path):
    nonce = issue(tmp_path)
    assert grants.redeem(tmp_path, nonce, NOW + 5) == {
        "mode": "write", "effort": "xhigh", "model": None,
        "cwd": "/work", "issued_at": NOW,
    }
    assert grants.redeem(tmp_path, nonce, NOW + 6) is None


def test_nonces_are_random_and_well_formed(tmp_path):
    first, second = issue(tmp_path), issue(tmp_path)
    assert first != second
    assert grants.NONCE_PATTERN.match(first) and len(first) == 32


def test_grant_files_are_owner_only(tmp_path):
    nonce = issue(tmp_path)
    assert stat.S_IMODE(grant_file(tmp_path, nonce).stat().st_mode) == 0o600


def test_an_expired_grant_is_refused_and_removed(tmp_path):
    nonce = issue(tmp_path)
    assert grants.redeem(tmp_path, nonce, NOW + grants.GRANT_TTL_SECONDS + 1) is None
    assert not grant_file(tmp_path, nonce).exists()


def test_a_grant_still_works_just_inside_its_lifetime(tmp_path):
    nonce = issue(tmp_path)
    assert grants.redeem(tmp_path, nonce, NOW + grants.GRANT_TTL_SECONDS) is not None


@pytest.mark.parametrize(
    "nonce", ["", None, 5, "../../etc/passwd", "g" * 32, "a" * 31, "A" * 32]
)
def test_malformed_nonces_are_refused(tmp_path, nonce):
    issue(tmp_path)
    assert grants.redeem(tmp_path, nonce, NOW) is None


def test_an_unknown_nonce_is_refused(tmp_path):
    issue(tmp_path)
    assert grants.redeem(tmp_path, "0" * 32, NOW) is None


@pytest.mark.parametrize("field,value", [
    ("mode", "admin"), ("effort", "max"), ("cwd", "relative/path"), ("cwd", None),
    ("model", "bad model"), ("model", 7), ("issued_at", "soon"),
])
def test_a_grant_with_a_bad_field_is_refused(tmp_path, field, value):
    nonce = issue(tmp_path)
    path = grant_file(tmp_path, nonce)
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    assert grants.redeem(tmp_path, nonce, NOW) is None


def test_issuing_prunes_expired_grants(tmp_path):
    old = issue(tmp_path, now=NOW - grants.GRANT_TTL_SECONDS - 10)
    fresh = issue(tmp_path, now=NOW)
    assert not grant_file(tmp_path, old).exists()
    assert grant_file(tmp_path, fresh).exists()


def test_a_model_name_is_carried(tmp_path):
    nonce = issue(tmp_path, mode="read", model="gpt-6-astra")
    grant = grants.redeem(tmp_path, nonce, NOW)
    assert (grant["mode"], grant["model"]) == ("read", "gpt-6-astra")
