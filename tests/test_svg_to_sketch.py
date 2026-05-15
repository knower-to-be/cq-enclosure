"""Property-based tests for svg_to_sketch and svg_to_wires."""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq
import pytest
import svgelements.svgelements as se

from src.svg_to_sketch import svg_to_sketch, svg_to_wires, wire_to_stroked_face

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SVG_DIR = Path(__file__).parent / "data" / "svg_icons"
SVG_FILES = sorted(SVG_DIR.glob("*.svg"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_svg_subpaths(
    svg_path: Path, sketch_size: tuple[float, float]
) -> list[tuple[list[se.PathSegment], float, float, bool]]:
    """Parse an SVG and return subpath metadata.

    Returns a list of ``(segments, expected_length, scaled_stroke_width,
    is_closed)`` tuples, where *expected_length* and *scaled_stroke_width*
    are already scaled to *sketch_size*.
    """
    svg = se.SVG.parse(str(svg_path), reify=False)
    viewbox = svg.viewbox
    if viewbox is None or viewbox.width == 0 or viewbox.height == 0:
        return []

    scale = min(sketch_size[0] / viewbox.width, sketch_size[1] / viewbox.height)

    result: list[tuple[list[se.PathSegment], float, float, bool]] = []
    for elem in svg.elements():
        if isinstance(elem, se.Path):
            path = abs(elem)
        elif isinstance(elem, (se.Circle, se.Rect)):
            path = se.Path(elem)
            path.reify()
        else:
            continue

        if len(path) == 0:
            continue

        stroke_w = getattr(elem, "stroke_width", 2.0)
        if stroke_w is None or stroke_w <= 0:
            stroke_w = 2.0

        path *= f"scale({scale}, {scale})"
        path.reify()

        for i in range(path.count_subpaths()):
            sub = list(path.subpath(i))
            is_closed = any(isinstance(s, se.Close) for s in sub)
            segs_nomove = [s for s in sub if not isinstance(s, se.Move)]
            length = sum(s.length() for s in segs_nomove)
            result.append((sub, length, stroke_w * scale, is_closed))

    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("svg_path", SVG_FILES, ids=[f.name for f in SVG_FILES])
def test_svg_to_wires(svg_path: Path) -> None:
    """Property tests for svg_to_wires and svg_to_sketch."""
    sketch_size = (24.0, 24.0)

    # Parse the SVG independently to get expected values
    subpath_info = _parse_svg_subpaths(svg_path, sketch_size)

    # ------------------------------------------------------------------
    # 1. svg_to_wires: wire count matches subpath count
    # ------------------------------------------------------------------
    wires = svg_to_wires(svg_path, sketch_size)
    assert len(wires) == len(subpath_info), (
        f"Expected {len(subpath_info)} wires, got {len(wires)}"
    )

    for idx, (wire, (segments, expected_len, scaled_sw, is_closed)) in enumerate(
        zip(wires, subpath_info)
    ):
        # 2. Wire length ≈ svgelements path length (rtol=0.01)
        assert math.isclose(wire.Length(), expected_len, rel_tol=0.01)

        # 3. Open/closed consistency
        assert wire.IsClosed() == is_closed

        # 4. Wires are within sketch_size bounds
        bbox = wire.BoundingBox()
        tol = 1e-9
        assert sketch_size[0] / 2 - tol <= bbox.xmin <= sketch_size[0] / 2 + tol
        assert sketch_size[0] / 2 - tol <= bbox.ymin <= sketch_size[0] / 2 + tol
        assert sketch_size[0] / 2 - tol <= bbox.xmax <= sketch_size[0] / 2 + tol
        assert sketch_size[0] / 2 - tol <= bbox.ymax <= sketch_size[0] / 2 + tol

        # --------------------------------------------------------------
        # 5. Individual stroked face: can extrude, volume == area
        # --------------------------------------------------------------
        face = wire_to_stroked_face(wire, scaled_sw)
        assert face is not None, f"Wire[{idx}]: _wire_to_stroked_face returned None"

        solid = cq.Solid.extrudeLinear(face, (0, 0, 1))
        volume = solid.val().Volume()
        area = face.Area()
        if area > 0:
            rel_vol = abs(volume - area) / area
            assert rel_vol < 1e-6, (
                f"Wire[{idx}]: volume/area mismatch: "
                f"area={area:.6f}, volume={volume:.6f}, "
                f"rel_diff={rel_vol:.6f}"
            )


@pytest.mark.parametrize("svg_path", SVG_FILES, ids=[f.name for f in SVG_FILES])
def test_svg_to_sketch(svg_path: Path) -> None:
    sketch_size = (24.0, 24.0)

    sketch = svg_to_sketch(svg_path, sketch_size)
    try:
        combined = cq.Workplane("XY").placeSketch(sketch).extrude(1.0)
        assert combined.val().Volume() > 0, (
            f"Combined extrusion has zero volume for {svg_path.name}"
        )
    except Exception as exc:
        raise AssertionError(
            f"Combined extrusion failed for {svg_path.name}: {exc}"
        ) from exc


def test_wire_to_stroked_face():

    circle = cq.Wire.makeCircle(10, (0, 0), (0, 0, 1))
    face = wire_to_stroked_face(circle, 2)
    assert face is not None and face.isValid()
    expected_area = math.pi * (11**2 - 9**2)
    assert math.isclose(face.Area(), expected_area)
    # Verify that cadquery is able to extrude the face and that the volume is correct
    solid = cq.Solid.extrudeLinear(face, (0, 0, 1))
    assert math.isclose(solid.Volume(), face.Area())

    line = cq.Wire.assembleEdges([cq.Edge.makeLine((0, 0), (10, 0))])
    face = wire_to_stroked_face(line, 2)
    assert face is not None and face.isValid()
    expected_area = 10 * 2 + math.pi * 1**2
    assert math.isclose(face.Area(), expected_area)
    solid = cq.Solid.extrudeLinear(face, (0, 0, 1))
    assert math.isclose(solid.Volume(), face.Area())
