# Component taxonomy: what the photo pipeline must find, derived from the engineer's report

Date: 2026-10-15. Source: the design partner's completed study (private), whose asset
schedule has 46 rows, one per building system, each with a cost and a recovery bucket
(19 five-year rows, 1 seven-year, 6 fifteen-year, 20 thirty-nine-year). The engineer does
not list objects; they list systems and price them. So the minimum target of the photo
pipeline is: for every system row the engineer would write, give them the evidence
(which objects, how many, what material, where in which photo) that supports its
quantity and its bucket. Over-detecting is harmless; missing a system is not.

## 1. Three kinds of rows

**A. Rows that are countable objects in photos.** The detector and the highlight UI
earn their keep here. Count, material and location all matter.

| Report row (bucket) | Our labels | What the engineer needs |
|---|---|---|
| Appliances (5) | refrigerator, dishwasher, stove, oven, microwave, washer, dryer, range_hood, appliance | count per kind |
| Cabinets / Millwork (5); Cabinets / Millwork - Restrooms (39) | kitchen_cabinet, bathroom_vanity, built_in_shelving, closet_shelving | linear feet or unit count; kitchen vs bathroom matters (different buckets in this study) |
| Specialty Plumbing - sinks (5); Plumbing (39) | sink, toilet, bathtub, shower, water_heater | count; which fixtures the engineer treats as 5-year (kitchen/utility sinks here) vs structural plumbing |
| Specialty Electrical (5); Electrical (39); Security / Exterior Lighting (5) | electrical_outlet, light_fixture, outdoor_lighting, electrical_panel, ceiling_fan, doorbell, smoke_detector, security_system, thermostat | counts; decorative/dedicated-circuit items are 5-year, general wiring 39-year |
| Fireplaces (5); Specialty Electrical - Electric Fireplace (5) | fireplace | count and type (electric vs masonry) |
| Windows (39); Window Treatments (5); Door & Window Guards (5) | window, blinds, window_treatment | count; treatments separately |
| Doors & Frames (39); Overhead Doors (39) | interior_door, exterior_door, garage_door | count |
| Specialty HVAC - Dryer Vents (5); HVAC (39) | hvac_vent, hvac_unit, ac_condenser | count |
| Stairs / Balconies (39) | stairs, railing, porch | presence |
| Movable Shed (7) | other (no label yet: `shed`) | presence |
| Exterior Signage (15); Exterior Fencing (15); Retaining Walls (15); Driveway (15); Sidewalks / Curbs (15); Exterior Equipment Concrete Pads (15) | signage, fence, retaining_wall, driveway, sidewalk, patio, parking_lot | presence and rough extent |
| Gutter Systems (39); Roofing Systems (39); Exterior Facade / Building Skin (39) | gutter, roof, siding | presence and material |

**B. Rows that are finishes or surfaces: material is the whole question.** Whether a
floor is carpet (5-year here) or ceramic tile (39-year) or sealed concrete (39-year), or
whether a wall has raised wood paneling (5) or plain paint (39), changes the bucket. The
object is the room surface; what the engineer needs is the material and the area, so the
photo step must report material per surface per room, not count surfaces.

| Report row (bucket) | Our labels | What the engineer needs |
|---|---|---|
| Flooring - Carpet (5); Wood Flooring (5); Ceramic Tile Flooring (39); Flooring - Sealed Concrete (39) | carpet, hardwood_floor, tile, vinyl_flooring, laminate_flooring (+ material) | material per room, area from the floor plan or takeoff |
| Wall Coverings (5); Raised Wood Paneling (5); Moldings (5); Painting (39); Interior Half Walls (5) | wall_finish, wainscoting, crown_molding, baseboard (+ material) | which rooms have which finish |
| Ceiling Systems (39) | ceiling_finish (+ material) | material per room |
| Bathroom Accessories (5) | other today (towel bars, holders: no label) | presence per bathroom |
| FF&E (5) | furniture, chair, desk, filing_cabinet (loose furniture is `other` in the labels today) | the engineer decides scope with the client |

**C. Rows that photos cannot establish.** They come from the appraisal, the plans, the
engineer's walk-through or the cost model, and the photo pipeline should not pretend to
find them: Foundations, Structural Framing, Interior Framing / Partitions, Site
Utilities, Fire Protection & Alarm (a smoke detector in a photo is a hint, not the
system), Communication / Data, Access Control.

## 2. What this changes

1. The detection target is kind A: the countable objects, prompted on every photo,
   with counts and highlights. Kind B is a classification of surfaces per room with a
   material; the per-photo model already returns material and the room inventory should
   carry one entry per surface per room (the label guide already does this).
2. Three labels are missing from the vocabulary and should be added: `shed` (7-year row),
   `bathroom_accessory`, and `concrete_pad`. `other` currently absorbs them.
3. The evaluation that matters to the engineer is per report row: did the pipeline give
   evidence for every row the engineer wrote? That is a 46-row checklist per study
   (present / missing / not photo-derivable), which is cheaper to label than boxes and
   is the product metric; the per-photo and per-room metrics stay as the engineering
   diagnostics.
4. The rules table's 48 rows cover the kind-A labels; the kind-B rows need the
   material-driven rules (floor by material) that `basis.domain.rules.lookup` already
   supports.
