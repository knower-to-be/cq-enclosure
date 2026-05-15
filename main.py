from dataclasses import dataclass
from typing import cast

import cadquery as cq
import heatserts  # Adds the heatsert function to cadquery.Workplane


@dataclass
class Enclosure:
    box: cq.Workplane
    lid: cq.Workplane | None


def main() -> Enclosure:
    isize = cq.Vector(50, 80, 20)
    thickness = 2
    hole_diameter = 3.0
    hole_depth = 5.0
    bottom_chamfer = 0.3
    inner_bottom_fillet = 0.5

    pcb_screws_rect_size = cq.Vector(30, 50)
    pcb_screw_size = 3.0
    pcb_screw_height = 3.0

    lid_box_sep = 0.3

    esize = isize + cq.Vector(2 * thickness, 2 * thickness, thickness)

    # Enclosure shell
    box = (
        cq.Workplane()
        .box(*esize, centered=(True, True, False))
        .faces(">Z")
        .shell(-thickness, kind="intersection")
    )

    # Make holes
    # The following is also an option, but I like `pushPoints` more
    #   .rect(isize.x - hole_diameter/2, isize.y - hole_diameter/2).vertices()
    hole_points = [
        (i * (isize.x / 2 - hole_diameter / 2), j * (isize.y / 2 - hole_diameter / 2))
        for i in [1, -1]
        for j in [1, -1]
    ]
    box: cq.Workplane = (
        box.faces("+Z")
        .faces("<Z")
        .workplane()
        .pushPoints(hole_points)
        .rect(hole_diameter + 2 * thickness, hole_diameter + 2 * thickness)
        .extrude(isize.z)
        .faces(">Z")
        .workplane()
        .pushPoints(hole_points)
        .heatsert("M3")  # type: ignore
        # .hole(hole_diameter, hole_depth)
    )

    # Fillets on the outside of the box
    box = (
        box.faces(">X or <X or >Y or <Y")
        .edges("|Z")
        .fillet(hole_diameter / 2 + thickness)
    )
    # Fillets between the inner walls and the hole rectangles (8 in total)
    box = (
        box.faces(">X[-2] or <X[-2] or >Y[-2] or <Y[-2]")
        .edges("|Z")
        .fillet(hole_diameter / 2)
    )
    # Fillets on the inner edge of the hole rectangle.
    # Essentially, these are the last Z edges remaining, so we select just that.
    # -0.01 because otherwise oct produces an error.
    box = box.edges("|Z and %Line").fillet(hole_diameter / 2 + thickness - 0.01)

    if inner_bottom_fillet > 0:
        box = box.faces("<Z[-2]").fillet(inner_bottom_fillet)

    box = (
        box.faces("<Z[-2]")
        .workplane()
        .rect(pcb_screws_rect_size.x, pcb_screws_rect_size.y, forConstruction=True)
        .vertices()
        .circle(pcb_screw_size / 2 + thickness)
        .extrude(pcb_screw_height)
        .faces("<Z[-3]")
        .workplane()
        .rect(pcb_screws_rect_size.x, pcb_screws_rect_size.y, forConstruction=True)
        .vertices()
        .hole(pcb_screw_size, pcb_screw_height)
    )

    if bottom_chamfer > 0:
        box = box.faces("<Z").chamfer(bottom_chamfer)

    from src.svg_to_sketch import svg_to_sketch

    sketch = svg_to_sketch("tests/data/svg_icons/heart-pulse.svg", (50, 50))
    lid = (
        cq.Workplane()
        .box(100, 100, 2, centered=True)
        .faces(">Z")
        .workplane()
        .placeSketch(sketch.moved(-25, -25))
        .extrude(1)
    )

    return Enclosure(box, lid)
    lid = cq.Workplane().box(esize.x, esize.y, thickness, centered=(True, True, False))
    lid = lid.faces(">Z").workplane().pushPoints(hole_points).hole(hole_diameter)
    lid = lid.edges("|Z").fillet(hole_diameter / 2 + thickness)

    if len(box.faces(">Z").objects) > 1:
        print(
            f"Warning: Multiple top Z faces. Num: {len(box.faces('>Z').objects)}. Assuming that the first one is what needed"
        )
    top_face = cast(cq.Face, box.faces(">Z").objects[0])
    # The top face contains the inner cavity and the holes. We are interested in the wire around the cavity.
    # Almost always the area that the cavity wire encloses is greater than the hole area.
    inner_wire: cq.Wire = max(top_face.innerWires(), key=lambda w: w.Area())
    inner_wire = inner_wire.translate((0, 0, thickness - inner_wire.Center().z))
    lid = (
        lid.faces(">Z")
        .workplane()
        .newObject(inner_wire.offset2D(-lid_box_sep))
        .toPending()
        .extrude(thickness)
    )

    if (top_chamfer_size := max(0.0, min(1.0, thickness / 4))) > 0:
        lid = lid.faces(">Z").chamfer(top_chamfer_size)
    if bottom_chamfer > 0:
        lid = lid.faces("<Z").chamfer(bottom_chamfer)

    from src.svg_to_sketch import svg_to_sketch

    return Enclosure(box, lid)


if __name__ == "__main__":
    enc = main()
    if enc.box is not None:
        enc.box.findSolid().exportStl("box.stl")
    if enc.lid is not None:
        enc.lid.findSolid().exportStl("lid.stl")
