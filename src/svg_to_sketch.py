from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import cadquery as cq
import svgelements.svgelements as se

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_vec(pt: se.Point | None) -> cq.Vector:
    """Convert an svgelements Point to a cadquery Vector (Z=0)."""
    if pt is None:
        raise ValueError
    return cq.Vector(pt.x, pt.y, 0.0)


def _segment_to_edge(seg: se.PathSegment, tol: float = 1e-12) -> cq.Edge | None:
    """Convert a single svgelements PathSegment to a cadquery Edge.

    Returns None for Move segments (pen-up) and zero-length segments.
    """

    if isinstance(seg, se.Move):
        return None

    start, end = _to_vec(seg.start), _to_vec(seg.end)

    if isinstance(seg, (se.Line, se.Close)):
        return cq.Edge.makeLine(start, end) if abs(start - end) >= tol else None
    elif isinstance(seg, se.CubicBezier):
        bezier_points = [
            start,
            _to_vec(seg.control1),
            _to_vec(seg.control2),
            end,
        ]
        return cq.Edge.makeBezier(bezier_points)
    elif isinstance(seg, se.Arc):
        return _arc_to_edge(seg)

    raise TypeError(f"Unsupported segment type: {type(seg).__name__}")


def _arc_to_edge(seg: se.Arc) -> cq.Edge:
    """Convert an svgelements Arc to a cadquery Edge."""
    center = _to_vec(seg.center)
    normal = cq.Vector(0, 0, 1)
    is_circular = abs(seg.rx - seg.ry) < 1e-9

    if is_circular:

        def _angle(pt: se.Point) -> float:
            return math.atan2(pt.y - seg.center.y, pt.x - seg.center.x)
    else:
        rot_rad = seg.get_rotation().as_radians
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)

        def _angle(pt: se.Point) -> float:
            dx = pt.x - seg.center.x
            dy = pt.y - seg.center.y
            x_local = dx * cos_r + dy * sin_r
            y_local = -dx * sin_r + dy * cos_r
            return math.atan2(
                y_local / seg.ry if seg.ry != 0 else 0.0,
                x_local / seg.rx if seg.rx != 0 else 1.0,
            )

    a_start = math.degrees(_angle(seg.start))
    a_end = math.degrees(_angle(seg.end))

    if seg.sweep > 0:  # CCW
        a1, a2 = a_start, a_end
        orientation = True
        while a2 <= a1:
            a2 += 360.0
    else:  # CW
        a1, a2 = a_end, a_start
        orientation = False
        while a2 <= a1:
            a2 += 360.0

    if is_circular:
        radius = (seg.rx + seg.ry) / 2.0
        return cq.Edge.makeCircle(radius, center, normal, a1, a2, orientation)

    sense: int = 1 if orientation else -1
    xdir = cq.Vector(cos_r, sin_r, 0.0)
    return cq.Edge.makeEllipse(seg.rx, seg.ry, center, normal, xdir, a1, a2, sense)


def _path_segments_to_wire(
    segments: Sequence[se.PathSegment],
) -> cq.Wire | None:
    """Convert a contiguous sequence of svgelements PathSegments to a cq.Wire.

    The sequence should form one subpath (no internal Move commands;
    a leading Move is allowed and will be skipped).
    """
    if not segments:
        return None
    edges = [edge for seg in segments if (edge := _segment_to_edge(seg)) is not None]
    return cq.Wire.assembleEdges(edges) if edges else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def svg_to_wires(svg_path: Path, sketch_size: tuple[float, float]) -> list[cq.Wire]:
    """Parse an SVG and convert all geometric elements to cadquery Wires.

    Each contiguous subpath in the SVG becomes one Wire.  Coordinates
    are uniformly scaled from the SVG viewBox so the entire drawing
    fits inside *sketch_size* while preserving aspect ratio.
    """
    svg = se.SVG.parse(str(svg_path), reify=False)

    viewbox = svg.viewbox
    if viewbox is None or viewbox.width == 0 or viewbox.height == 0:
        return []

    scale = min(sketch_size[0] / viewbox.width, sketch_size[1] / viewbox.height)

    wires: list[cq.Wire] = []

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

        path *= f"scale({scale}, {scale})"
        path.reify()

        for i in range(path.count_subpaths()):
            sub = list(path.subpath(i))
            wire = _path_segments_to_wire(sub)
            if wire is not None:
                wires.append(wire)

    return wires


def svg_to_sketch(svg_path: Path | str, sketch_size: tuple[float, float]) -> cq.Sketch:
    """Convert an SVG to a cadquery Sketch.

    Stroke-based SVG artwork (black areas) is converted to filled
    sketch faces suitable for extrusion.  The stroke width is read
    from the SVG; ``stroke-linecap="round"`` is assumed for open paths.
    """
    svg = se.SVG.parse(str(svg_path), reify=False)

    viewbox = svg.viewbox
    if viewbox is None or viewbox.width == 0 or viewbox.height == 0:
        return cq.Sketch()

    scale = min(sketch_size[0] / viewbox.width, sketch_size[1] / viewbox.height)

    wire_data: list[tuple[cq.Wire, float, bool]] = []
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
        scaled_sw = stroke_w * scale

        path *= f"scale({scale}, {scale})"
        path.reify()

        for i in range(path.count_subpaths()):
            sub = list(path.subpath(i))
            is_closed = any(isinstance(s, se.Close) for s in sub)
            wire = _path_segments_to_wire(sub)
            if wire is not None:
                wire_data.append((wire, scaled_sw, is_closed))

    sketch = cq.Sketch()
    for wire, stroke_w, is_closed in wire_data:
        face = wire_to_stroked_face(wire, stroke_w, is_closed)
        if face is not None:
            sketch.face(face)

    return sketch


# ---------------------------------------------------------------------------
# Stroke-to-face conversion
# ---------------------------------------------------------------------------


def wire_to_stroked_face(wire: cq.Wire, stroke_width: float) -> cq.Face:
    """Convert a centerline Wire into a stroked Face of the given width.

    Closed wires produce a ring (outer - inner offset).
    Open wires use polyline sampling with round end caps.
    """
    if stroke_width <= 0:
        raise ValueError("stroke_width must be positive")
    if len(wire.Edges()) <= 0:
        raise ValueError(f"Wire {wire} has no edges")

    hw = stroke_width / 2.0

    if wire.IsClosed():
        outer_wires = wire.offset2D(hw, kind="arc")
        inner_wires = wire.offset2D(-hw, kind="arc")
        return cq.Face.makeFromWires(outer_wires[0], [inner_wires[0]])
    return cq.Face.makeFromWires(wire.close().offset2D(hw, kind="arc")[0])
