"""Camera device resolution: stable handles survive index shuffling."""

import os

from camvision.camera import resolver


def _make_fake_dev(tmp_path):
    """Build a fake /dev tree: two video nodes + a by-id symlink to one of them."""
    dev = tmp_path / "dev"
    byid = dev / "v4l" / "by-id"
    bypath = dev / "v4l" / "by-path"
    byid.mkdir(parents=True)
    bypath.mkdir(parents=True)
    v3 = dev / "video3"
    v5 = dev / "video5"
    v3.write_text("")
    v5.write_text("")
    # by-id points at video3 initially
    link = byid / "usb-ACME_HD_Camera_SN123-video-index0"
    os.symlink(v3, link)
    return dev, byid, bypath, v3, v5, link


def test_resolve_numeric_spec_returns_index():
    assert resolver.resolve(3) == 3
    assert resolver.resolve("3") == 3


def test_resolve_path_follows_symlink_live(tmp_path):
    dev, byid, bypath, v3, v5, link = _make_fake_dev(tmp_path)
    # Resolving the by-id link yields the concrete node it points at.
    assert resolver.resolve(str(link)) == os.path.realpath(str(v3))

    # Simulate the kernel renumbering: repoint the by-id symlink to video5.
    os.remove(link)
    os.symlink(v5, link)
    # Same stored spec now resolves to the NEW node — this is the whole point.
    assert resolver.resolve(str(link)) == os.path.realpath(str(v5))


def test_resolve_name_fragment_matches_by_id(tmp_path):
    dev, byid, bypath, v3, v5, link = _make_fake_dev(tmp_path)
    got = resolver.resolve("sn123", by_id_dir=str(byid), by_path_dir=str(bypath))
    assert got == os.path.realpath(str(v3))


def test_stable_handle_for_index(tmp_path, monkeypatch):
    dev, byid, bypath, v3, v5, link = _make_fake_dev(tmp_path)
    # Point resolver's /dev/videoN lookup at our fake tree.
    real_realpath = os.path.realpath

    def fake_realpath(p):
        if p == "/dev/video3":
            return real_realpath(str(v3))
        return real_realpath(p)

    monkeypatch.setattr(resolver.os.path, "realpath", fake_realpath)
    handle = resolver.stable_handle_for_index(3, by_id_dir=str(byid), by_path_dir=str(bypath))
    assert handle == str(link)


def test_resolve_missing_falls_back_to_zero():
    assert resolver.resolve("no-such-camera-xyz",
                            by_id_dir="/nonexistent", by_path_dir="/nonexistent") == 0
    assert resolver.resolve(None) == 0
