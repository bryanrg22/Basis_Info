# Rules table re-citation (2026-10-15)

`backEnd/basis/domain/rules.py`, `RULES_VERSION` `2026-10-15.2`. Follows EVAL_REPORT
section 3: the 48 legacy citations of the rules table did not verify against the
reference documents (0 verbatim on the cited page; the cited ATG pages 412-420 do not
exist, the ATG PDF has 348 pages). Every row has been re-cited against the PDFs in
`BASIS_EVAL_DATA_DIR/corpus/`; no row's section, bucket, asset class or aliases changed.
The PDFs stay outside the repository; the excerpts below are the citation excerpts
themselves (one phrase or sentence each), nothing longer.

## Method

1. Page text of the four IRS documents extracted with pymupdf (`page.get_text()`, 1-based
   PDF page numbers, the same numbering `basis.retrieval.parse` stores). The printed and
   PDF page numbers of the ATG coincide.
2. Searched each row's subject and read the candidate pages. The decisive source turned
   out to be the ATG's residential rental property matrix (Pub 5653 chapter 7.H,
   "Exhibit A", PDF pages 296-322): the IRS's own item-by-item categorization and GDS
   recovery period for residential rental property, which the table's default property
   class is. Primary sources were preferred over passing mentions: Rev. Proc. 87-56 class
   text (00.11 on p.4, 00.3 on pp.6-7), Exhibit A rows, the regulation text quoted in the
   ATG (Treas. Reg. 1.48-1(e)(2), p.19); court-case table rows only to show the other side.
3. The rule for a row: it is **supported** when a verbatim passage gives its section and
   recovery period (or the asset class that fixes it) for the item the label most
   plausibly names in residential rental property. Otherwise it is **contested**: the
   legacy bucket is kept (no tax position changes in this change), the row carries a note
   saying no supporting text was found and what the documents say instead, with pages,
   and its citation points at the closest relevant page with an excerpt that really is
   on that page. A contested hit goes to the engineer (`needs_review`,
   `rule_contested:<key>`).
4. Each excerpt was checked three ways, all 65 passing:
   - `basis.classify.verify.excerpt_supported(excerpt, page_text)` on the cited page
     (pymupdf text): 65 of 65. This is what the eval dataset `rule_citations` re-checks.
   - Against the chunks production would store: the PDFs parsed and chunked with
     `basis.retrieval.parse.parse_pdf` + `basis.retrieval.chunk.chunk_document`; a chunk
     of the cited page contains the excerpt for 65 of 65. Five first-choice excerpts
     failed this check and were replaced: the production parser interleaves the two columns of Pub 527
     p.13, so its three class sentences ("This class also includes appliances, carpeting, and furniture used in a
     residential rental real estate activity.", the 7-year office furniture sentence and
     the 15-year roads/fences sentence) are split by the other column's words; on Rev.
     Proc. 87-56 p.4 the class-life numbers land inside the 00.11 sentence "Includes
     furniture and fixtures that are not a structural component of a building."; and on
     ATG p.298 "Inherently" comes out as "i nherently".
   - `resolve_rule_citations`' search simulated with the in-memory BM25
     (`InMemoryChunkStore.bm25`, k = 10) over those chunks of the four IRS documents
     (1,262 chunks): the cited chunk is in the top 10 for 65 of 65. The RSMeans volumes
     were not in this index and the production search is Postgres BM25, so this is
     indicative, not a production measurement.

Result: 47 rows, 65 citations (48 before), all verbatim on the cited page. 25 rows are
supported; 22 are contested (7 contested before, 15 newly).

## Follow-ups for the engineer (not changed here)

- Aliases that the supported rows' text does not cover: `range_hood` / `hood_vent` ->
  `appliance` (Exhibit A p.311: built-in stove hoods are section 1250, 27.5 years);
  `exterior_lighting` -> `outdoor_lighting` (building-mounted exterior lighting is section
  1250, p.313); `hot_tub` / `spa` -> `pool` (a freestanding spa is 57.0, 5 years, p.320;
  an indoor pool is section 1250, p.299); `luxury_vinyl` / `lvp` -> `vinyl_flooring`
  (Exhibit A names VCT and sheet vinyl, readily removable).
- The 22 contested rows: for each, either change the bucket to what the documents say
  (most to section 1250, 27.5 years for residential rental; a bucket change also changes
  the cache key through `RULES_VERSION`) or record the engineer's basis for the legacy
  treatment and cite it.
- Production parsing of two-column IRS publications (Pub 527, Pub 946) interleaves the
  columns line by line, so a sentence that wraps inside one column is not contiguous in
  the stored chunk. Any citation (rule or model) of such a sentence fails verification.
  This affects retrieval quality too; it is a parser fix (column-aware line grouping),
  separate from this change.

## Row by row

Status: `supported`, `contested (kept)` (contested before this change, note updated),
`contested (new)`. Section and bucket are unchanged from the legacy table. ATG = IRS
Publication 5653 (doc `IRS_IRS_COST_SEG_ATG__2024`); RP 87-56 = `IRS_REV_PROC_87_56`;
Pub 946 = `IRS_IRS_PUB_946__2024`. "Exhibit A" = ATG pp.297-322.

| Rule | Section, bucket | Old citation | New citation | Status | Why |
|---|---|---|---|---|---|
| `carpet` | 1245, y5 | RP 87-56 p.12: "Asset class 57.0 includes carpeting"<br>ATG p.415: "Carpeting - 5-year recovery" | ATG p.307: "All vinyl composition tile (VCT), sheet vinyl, and carpeting will be treated as not permanently attached and not intended to be permanent."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | Exhibit A, Floor Coverings - Readily Removable: carpeting is not permanently attached, section 1245, asset class 57.0, 5 years; p.297 says 57.0 (5 years) covers most section 1245 property used with residential rental property. Rev. Proc. 87-56 p.12 is about carpet manufacturing (class 22.3), not carpet as an asset. |
| `light_fixture` | 1245, y5 | ATG p.418: "Decorative lighting fixtures" | ATG p.313: "Light fixtures, such as neon, track lighting, or grow lights which are decorative in nature and not necessary for the operation or maintenance of the building." | contested (kept) | Exhibit A supports only accent/decorative fixtures that do not give general illumination (section 1245, 57.0, 5 years); general lighting is section 1250 (p.313) and the regulation lists lighting fixtures as structural components. |
| `kitchen_cabinet` | 1245, y5 | ATG p.412: "Cabinets - kitchen" | ATG p.311: "Kitchen cabinets, counters, sink, includes plumbing costs."<br>ATG p.100: "Cabinets and hardware (§ 1245)" | contested (new) | Exhibit A, Kitchen Components (p.311) and Cabinetry - Kitchen (p.298): section 1250, 27.5 years. The only section 1245 cabinet passage is the Metro National case row (p.100; a non-residential case, no recovery period), cited second so the engineer sees both. |
| `countertop` | 1245, y5 | ATG p.412: "Countertops (non-structural)" | ATG p.311: "Kitchen cabinets, counters, sink, includes plumbing costs." | contested (new) | Exhibit A, Kitchen Components: counters are section 1250, 27.5 years; no passage supports 5 years. |
| `appliance` | 1245, y5 | Pub 946 p.42: "Appliances - residential rental" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | Exhibit A, Kitchen Appliances: section 1245, 57.0, 5 years. Not supported for the `range_hood` / `hood_vent` aliases: Exhibit A (p.311) puts built-in stove hoods in section 1250 (follow-up below). |
| `refrigerator` | 1245, y5 | Pub 946 p.42: "Appliances - refrigerators" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | Exhibit A, Kitchen Appliances (names the refrigerator), plus the 57.0 / 5-year statement. |
| `dishwasher` | 1245, y5 | Pub 946 p.42: "Appliances - dishwashers" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | As refrigerator. |
| `stove` | 1245, y5 | Pub 946 p.42: "Appliances - stoves" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | As refrigerator (Stove/Oven). |
| `oven` | 1245, y5 | Pub 946 p.42: "Appliances - ovens" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | As refrigerator (Stove/Oven). |
| `microwave` | 1245, y5 | Pub 946 p.42: "Appliances - microwaves" | ATG p.311: "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | As refrigerator. |
| `washer` | 1245, y5 | Pub 946 p.42: "Appliances - washers" | ATG p.304: "appliances qualifying as § 1245 property (washer/dryer, refrigerator, range/ovens)"<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | Exhibit A, Electrical - Primary and Secondary EDS - Building (p.304) calls washer/dryer appliances qualifying as section 1245 property; the 57.0 / 5-year statement gives the period. Pub 946 p.42 (the old page) is a worked MACRS example and does not list appliances. |
| `dryer` | 1245, y5 | Pub 946 p.42: "Appliances - dryers" | ATG p.304: "appliances qualifying as § 1245 property (washer/dryer, refrigerator, range/ovens)"<br>ATG p.297: "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 property used with RRP, which is recovered over 5 years." | supported | As washer. |
| `garbage_disposal` | 1245, y5 | ATG p.412: "Garbage disposal units" | ATG p.311: "All piping, drains, faucets, garbage disposal and waste piping, sinks and other components of a kitchen plumbing system" | contested (new) | Exhibit A lists the garbage disposal under Kitchen - Plumbing and Kitchen - Electrical, both section 1250, 27.5 years (p.311); AmeriSouth (an apartment case, p.94) also holds sinks and garbage disposals section 1250. |
| `ceiling_fan` | 1245, y5 | ATG p.418: "Ceiling fans" | ATG p.298: "Ceiling fan typically with attached lights."<br>ATG p.191: "potted plants, hanging mirrors, ceiling fans, and theme related props" | contested (new) | Exhibit A, Ceiling Fans (p.298): section 1250, 27.5 years. The restaurant chapter (p.191) treats ceiling fans as decorative props, section 1245, 57.0, 5 years; cited second, but restaurant guidance is stated not to carry over to residential rental property (p.296). |
| `blinds` | 1245, y5 | ATG p.420: "Window treatments - blinds" | ATG p.322: "Window treatments such as drapes, curtains, louver, blinds, post construction tinting and interior decorative theme décor which are readily removable." | supported | Exhibit A, Window Treatments: blinds, readily removable, section 1245, 57.0, 5 years. |
| `window_treatment` | 1245, y5 | ATG p.420: "Window treatments" | ATG p.322: "Window treatments such as drapes, curtains, louver, blinds, post construction tinting and interior decorative theme décor which are readily removable." | supported | As blinds. |
| `bathroom_vanity` | 1245, y5 | ATG p.412: "Vanities - bathroom" | ATG p.298: "Restroom cabinetry, counters, sinks." | contested (new) | Exhibit A, Cabinetry - Restroom (p.298) and Restroom Components (p.317): section 1250, 27.5 years. |
| `toilet` | 1245, y5 | ATG p.414: "Plumbing fixtures - toilets" | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)" | contested (kept) | Exhibit A, Plumbing - Building: bathroom plumbing fixtures are section 1250, 27.5 years; the regulation (ATG p.19) lists plumbing fixtures as structural components. |
| `sink` | 1245, y5 | ATG p.414: "Plumbing fixtures - sinks" | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)" | contested (kept) | As toilet. |
| `bathtub` | 1245, y5 | ATG p.414: "Plumbing fixtures - bathtubs" | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)" | contested (kept) | As toilet. |
| `shower` | 1245, y5 | ATG p.414: "Plumbing fixtures - showers" | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)" | contested (kept) | As toilet. |
| `mirror` | 1245, y5 | ATG p.420: "Mirrors - decorative" | ATG p.317: "mirror attached with permanent adhesive"<br>ATG p.191: "Decorative mobile props such as playground equipment, potted plants, hanging mirrors" | contested (new) | Exhibit A, Restroom Components (p.317): a mirror attached with permanent adhesive is section 1250, 27.5 years (the `bathroom_mirror` alias maps here). Hanging decorative mirrors are section 1245, 57.0, 5 years only in the restaurant chapter (p.191), cited second. |
| `smoke_detector` | 1245, y5 | ATG p.416: "Safety equipment - smoke detectors" | ATG p.305: "Battery powered or hard-wired fire alarms and CO2 detectors, generally mounted to ceilings or walls by screws." | contested (new) | Exhibit A, Fire Alarm and CO2 Detectors (p.305): section 1250, 27.5 years; HCA (p.97) also holds heat and smoke detectors section 1250. |
| `thermostat` | 1245, y5 | ATG p.416: "HVAC controls - thermostats" | ATG p.304: "demand controllers, thermostats, computer controls, outside air economizers" | contested (new) | Exhibit A lists thermostats in Energy Management Systems (p.304) and the HVAC system (p.310), section 1250, 27.5 years. |
| `doorbell` | 1245, y5 | ATG p.418: "Electrical accessories - doorbells" | ATG p.301: "used in the operation or maintenance of the building or to provide general building services" | contested (new) | No document mentions doorbells. Closest: Exhibit A, Electrical Branch Circuits - Building (p.301), section 1250, 27.5 years. |
| `tile` | 1245, y5 | ATG p.415: "Floor coverings - tile" | ATG p.306: "ceramic or quarry tile, marble, paving brick, and other coverings cemented, mudded, or grouted to the floor" | contested (kept) | Exhibit A, Floor Coverings - Permanent (p.306): ceramic or quarry tile, section 1250, 27.5 years; the regulation names tiling as a permanent covering. |
| `hardwood_floor` | 1245, y5 | ATG p.415: "Floor coverings - hardwood" | ATG p.306: "Includes floor covering that is affixed with permanent adhesive or nailed or screwed in place." | contested (kept) | Exhibit A, Floor Coverings - Permanent (p.306): wood flooring nailed or screwed in place, section 1250, 27.5 years. |
| `vinyl_flooring` | 1245, y5 | ATG p.415: "Floor coverings - vinyl" | ATG p.307: "All vinyl composition tile (VCT), sheet vinyl, and carpeting will be treated as not permanently attached and not intended to be permanent." | supported | Exhibit A, Floor Coverings - Readily Removable: VCT and sheet vinyl are section 1245, 57.0, 5 years. Vinyl plank glued down permanently would not be (the `luxury_vinyl` / `lvp` materials map here; noted below). |
| `laminate_flooring` | 1245, y5 | ATG p.415: "Floor coverings - laminate" | ATG p.307: "Floor covering that is installed by means of strippable adhesives" | contested (new) | No document mentions laminate. Exhibit A makes a floor covering section 1245 only when readily removable (p.307); a floating laminate floor may qualify, a glued or nailed one would be section 1250 (p.306). |
| `closet_shelving` | 1245, y5 | ATG p.412: "Shelving - closet" | ATG p.305: "Built-in bookshelves, shelving and rods in bedroom, hallway, and linen closets, pantries, etc." | contested (new) | Exhibit A, Finish Carpentry (p.305): built-in shelving and rods in closets are section 1250, 27.5 years; AmeriSouth (p.94) holds finish carpentry section 1250. |
| `water_heater` | 1245, y5 | ATG p.414: "Water heaters - unit-specific" | ATG p.315: "all other components of a building plumbing system (water or gas) not specifically identified elsewhere" | contested (new) | Exhibit A does not name water heaters; the residual Plumbing - Building row (p.315) is section 1250, 27.5 years. The section 1245 kitchen hot water heater (Morrison, restaurant and retail chapters) serves equipment, not a dwelling unit. |
| `furniture` | 1245, y7 | RP 87-56 p.8: "Asset class 00.11 - Office furniture" | RP 87-56 p.4: "Includes such assets as desks, files, safes, and communications equipment"<br>ATG p.307: "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations." | contested (new) | Supported for office furniture (Rev. Proc. 87-56 class 00.11, p.4; Exhibit A, Furnishings - Rental Office Furniture, p.307, 7 years). But the row's aliases include sofa, couch, bed, table, bookcase, and Exhibit A, Furnishings - Residential (p.308) and Pub 527 (p.13) make residential furniture 5-year property (57.0). |
| `desk` | 1245, y7 | RP 87-56 p.8: "Asset class 00.11 - desks" | RP 87-56 p.4: "Includes such assets as desks, files, safes, and communications equipment"<br>ATG p.307: "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations." | supported | Rev. Proc. 87-56 class 00.11 (desks) is on p.4, not p.8 (the old page, class 00.4 text), 7 years; Exhibit A, rental office furniture, 00.11, 7 years. |
| `chair` | 1245, y7 | RP 87-56 p.8: "Asset class 00.11 - chairs" | ATG p.307: "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations." | contested (new) | Supported for an office chair (Exhibit A, p.307, 00.11, 7 years); a chair in a dwelling is a residential furnishing, 57.0, 5 years (p.308). |
| `filing_cabinet` | 1245, y7 | RP 87-56 p.8: "Asset class 00.11 - filing cabinets" | RP 87-56 p.4: "Includes such assets as desks, files, safes, and communications equipment"<br>ATG p.307: "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations." | supported | Rev. Proc. 87-56 class 00.11 (files); Exhibit A names the file cabinet, 00.11, 7 years. |
| `parking_lot` | 1250, y15 | Pub 946 p.38: "Land improvements - parking lots" | ATG p.314: "Grade level surface parking area usually constructed of asphalt, brick, concrete, stone, or similar material." | supported | Exhibit A, Parking Lots (p.314): asset class 00.3, 15 years. Pub 946 p.38 (the old page) is a depreciation worksheet, not the land-improvement class. |
| `sidewalk` | 1250, y15 | Pub 946 p.38: "Land improvements - sidewalks" | RP 87-56 p.7: "Examples of such assets might include sidewalks, roads, canals, waterways, drainage facilities"<br>ATG p.318: "Sidewalks and curbs are usually constructed of concrete, asphalt, stone, or similar material." | supported | Rev. Proc. 87-56 class 00.3 text names sidewalks (p.7); Exhibit A, Sidewalks and Curbs, 00.3, 15 years. |
| `landscaping` | 1250, y15 | Pub 946 p.38: "Land improvements - landscaping" | RP 87-56 p.7: "bridges, fences, landscaping, shubbery"<br>ATG p.312: "The costs of landscaping that would be destroyed upon the replacement of the depreciable asset, such as a building, are depreciable land improvements." | supported | Rev. Proc. 87-56 class 00.3 names landscaping and shrubbery (the PDF spells it 'shubbery'; the excerpt keeps it); Exhibit A (p.312): only landscaping that would be destroyed when the building is replaced is depreciable, the rest is land. |
| `fence` | 1250, y15 | Pub 946 p.38: "Land improvements - fences" | RP 87-56 p.7: "bridges, fences, landscaping, shubbery"<br>ATG p.305: "Examples include fences; gates, canals; waterways; drainage facilities" | supported | Rev. Proc. 87-56 class 00.3 names fences; Exhibit A, Fencing, Gates, Retaining Walls (p.305), 00.3, 15 years. |
| `retaining_wall` | 1250, y15 | Pub 946 p.38: "Land improvements - retaining walls" | ATG p.305: "sewers (not including municipal sewers in Class 51); retaining walls" | supported | Exhibit A, Fencing, Gates, Retaining Walls, ... and Other Land Improvements (p.305): 00.3, 15 years. |
| `outdoor_lighting` | 1250, y15 | ATG p.420: "Site improvements - outdoor lighting" | ATG p.313: "Pole-mounted or freestanding outdoor lighting system to illuminate sidewalks, parking, or recreation areas." | supported | Exhibit A, Lighting - Site (p.313): pole-mounted or freestanding, 00.3, 15 years. Building-mounted exterior lighting is section 1250, 27.5 years (p.313), which the `exterior_lighting` alias does not distinguish (noted below). |
| `driveway` | 1250, y15 | Pub 946 p.38: "Land improvements - driveways" | ATG p.317: "Grade level driveways, roads, and base areas usually constructed of asphalt, brick, concrete, stone, or similar material." | supported | Exhibit A, Roadways (p.317): grade level driveways, 00.3, 15 years. |
| `signage` | 1250, y15 | ATG p.420: "Site improvements - signage" | ATG p.319: "Includes exterior signs used to direct traffic and parking." | supported | Exhibit A, Signs - Exterior (p.319): signs directing traffic and parking, 00.3, 15 years (a pylon sign face is 57.0, 5 years; building signs are section 1250). |
| `pool` | 1250, y15 | Pub 946 p.38: "Land improvements - swimming pools" | ATG p.298: "outdoor, swimming pools/spas and associated equipment" | supported | Exhibit A, Common Areas - Exterior (p.298): outdoor pools/spas, 00.3, 15 years. An indoor pool is section 1250 (p.299) and a freestanding spa is 57.0, 5 years (p.320); the `hot_tub` / `spa` aliases map here (noted below). |
| `patio` | 1250, y15 | Pub 946 p.38: "Land improvements - patios" | ATG p.314: "A ground level, paved outdoor area adjoining a residence or other building" | supported | Exhibit A, Patios (p.314): 00.3, 15 years. |
| `deck` | 1250, y15 | Pub 946 p.38: "Land improvements - decks" | ATG p.300: "Inherently permanent decks and gazebos not attached to buildings." | contested (new) | Exhibit A (p.300): only a freestanding deck is 00.3, 15 years; a deck attached to the residence, the usual case, is section 1250, 27.5 years. |
| `sprinkler_system` | 1250, y15 | Pub 946 p.38: "Land improvements - irrigation systems" | ATG p.305: "holding, settling, or detention ponds; and irrigation systems" | supported | Exhibit A, Fencing, Gates, ... and Other Land Improvements (p.305) names irrigation systems, 00.3, 15 years (also Plumbing - Lawn/Landscaping Sprinkler System, p.315). |

## Rows added 2026-10-16 (`RULES_VERSION` `2026-10-16.1`)

Six labels joined the vocabulary for report rows that had none (EVAL_REPORT sections 13 and
19), so each gets a row. Three of them are building components (section 1250), which the
table had no rows for: a building row (`Rule.building`) stores the residential rental bucket
(27.5 years) and `lookup` returns 39 years for a nonresidential property, the two periods
ATG Exhibit A gives ("RRP - 27.5 Years NRRP - 39 Years"). Rule for the bucket of a new row:
the treatment the documents give for the item; where they give none, the client engineer's
report; and the row is contested whenever the documents and the engineer's report disagree
or no passage names the item, so a hit goes to the engineer. The same three checks as above
pass for all 12 new citations: verbatim on the cited page, inside one production chunk of
that page, and the cited chunk in the BM25 top 10 (in-memory, the four IRS documents).
Totals now: 53 rows, 77 citations, 26 contested.

| Rule | Section, bucket | Citation | Status | Why |
|---|---|---|---|---|
| `bathroom_accessory` | 1250, building (27.5 / 39) | ATG p.316: "Also, includes paper towel dispensers, electric hand dryers, towel racks or holders, cup dispensers, purse shelves, toilet paper holders"<br>ATG p.317: "Cabinet, counter, sink, faucet, mirror attached with permanent adhesive, toilet, towel rack, toilet paper holder, flush mounted medicine cabinet, tub, shower, vent fan, etc." | supported | Exhibit A, Restroom Accessories/Partitions (pp.316-317) and Restroom Components (p.317): section 1250, RRP 27.5 / NRRP 39. The retail, restaurant, office and casino tables and the case table (HCA, Morrison, A.C. Monk) agree. The engineer's report puts the row in 39-year (the property is nonresidential), as this row gives; COMPONENT_TAXONOMY had it in 5-year by mistake. |
| `concrete_floor` | 1250, building (27.5 / 39) | ATG p.307: "Includes concrete slabs and other floor systems."<br>ATG p.307: "Floors include special treatments applied to, or otherwise made a permanent part, of the floor." | supported | Exhibit A, Floors - Concrete (p.307): section 1250, 27.5 years; a sealer is a treatment made a permanent part of the floor. The engineer's report: Flooring - Sealed Concrete, 39-year. |
| `half_wall` | 1250, building (27.5 / 39) | ATG p.321: "partitions regardless of height (typically constructed of studs and sheetrock or other materials) that divide or create rooms or provide traffic control."<br>ATG p.321: "Interior walls where the partition can be 1) readily removed and remain in substantially the same condition after removal as before" | contested (new) | Exhibit A, Walls - Interior (p.321): non-load bearing partitions "regardless of height" are section 1250. Only a readily removable or movable partition (Walls Interior Partitions, same page) is section 1245, 5 years, which is where the engineer's report puts Interior Half Walls; cited second so the engineer sees both. |
| `window_guard` | 1250, building (27.5 / 39) | ATG p.300: "roll-up or sliding wire mesh or steel grills and gates"<br>ATG p.318: "Examples include window and door locks; card key access systems; keyless entry systems" | contested (new) | No document names window guards or bars. Closest: Exhibit A, Doors (p.300: steel grills and gates are doors, section 1250) and Security Systems - Building (p.318: window and door locks, section 1250). The engineer's report: Door & Window Guards, 5-year. |
| `shed` | 1245, y7 (no asset class) | ATG p.102: "Storage sheds (§ 1245)"<br>Pub 946 p.29: "does not have a class life and has not been designated by law" | contested (new) | The case table (A.C. Monk, p.102) holds storage sheds section 1245, with no period. The engineer's 7-year is Pub 946's 7-year class for property with no class life (p.29; the full sentence is split by the two-column parser, so the excerpt is its middle); ATG p.297 says 57.0, 5 years, applies to most section 1245 property used with residential rental property; a shed on a permanent foundation may be a building. |
| `concrete_pad` | 1250, y15 (00.3) | ATG p.299: "Foundations and footings for signs, light poles, and other land improvements (except buildings)."<br>ATG p.320: "Enclosures for waste receptacles, typically constructed on a concrete pad with its posts set in the concrete." | contested (new) | No document names exterior equipment pads. Exhibit A puts foundations for land improvements (p.299) and a trash enclosure on its pad (p.320) in 00.3, 15 years, the engineer's treatment; equipment foundations inside the building footprint are section 1250 (p.299), and a pad under building HVAC equipment outside it is not addressed. |

Aliases added with the rows: `towel_bar`, `towel_ring`, `toilet_paper_holder`, `grab_bar`,
`shower_curtain_rod`, `bath_accessory`, `restroom_accessory` -> `bathroom_accessory`;
`storage_shed`, `garden_shed` -> `shed`; `equipment_pad`, `condenser_pad` -> `concrete_pad`;
`sealed_concrete_floor`, `garage_floor` -> `concrete_floor`; `pony_wall`, `knee_wall` ->
`half_wall`; `window_bars`, `security_bars`, `security_grille`, `door_guard` ->
`window_guard`; and a generic floor label with material `concrete` or `sealed_concrete`
resolves to `concrete_floor`.
