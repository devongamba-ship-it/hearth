"""Real room geometry (rect + floor) round-trips through the layout codec."""

from ingest.layout import load_layout_from_text


def test_rect_and_floor_round_trip():
    layout = load_layout_from_text(
        """
        home_id: demo
        rooms:
          - id: studio
            name: Studio
            floor: 1
            rect: {x: 0.1, y: 0.2, w: 0.6, h: 0.4}
          - id: hall
            name: Hall
        nodes:
          - id: 1
            room: studio
            x: 0.4
            y: 0.4
            floor: 1
        links: []
        """
    )
    j = layout.to_json()
    studio = next(r for r in j["rooms"] if r["id"] == "studio")
    assert studio["floor"] == 1
    assert studio["rect"] == {"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.4}

    hall = next(r for r in j["rooms"] if r["id"] == "hall")
    assert hall["floor"] == 0          # default
    assert "rect" not in hall          # no geometry given -> omitted, client derives

    assert j["nodes"][0]["floor"] == 1
