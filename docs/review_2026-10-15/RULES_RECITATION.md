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

## Re-citation of 2026-10-29: the context dimension (`RULES_VERSION` `2026-10-29.1`)

`CLASSIFICATION_CONVENTIONS.md` section 4. Decision rule for every row below: **an item the
guide names takes the guide's bucket; an item no document names keeps its bucket and its
contested note.** So the rows ATG Exhibit A names as building components lose the legacy
5-year bucket and their contested note, and become building rows (`Rule.building`: 27.5
years residential rental, 39 nonresidential). A row can now carry **variants**: a `When`
(rooms, attachments, property classes) and the rule that applies there; the first variant
matching the component's normalized room type (`normalize_room_type`, so `restroom` and
`bath` are `bathroom`), attachment and property class is the effective rule, and the hit
names it in `RuleHit.context` (`room=bathroom`, `attachment=removable`). A variant's rule
keeps the row's key.

Method: as above. Every excerpt was checked (1) verbatim on the cited page, pymupdf
`page.get_text()` compared after `basis.retrieval.verify.normalize_text`, (2) inside one
production chunk of that page (`parse_pdf` + `chunk_document` of the ATG), and (3) through
`resolve_rule_citations` against the ingested real corpus in Postgres
(`tests/integration/retrieval/test_real_corpus.py`, which since this change walks every row
and every variant through `basis.domain.rules.table_hits`). The `rule_citations` eval
dataset now includes the variants' citations too (a variant's `rule` is
`key[context]`). 8 excerpts are new (marked **(new)** below); checks (1) and (2) pass for
all 104 claims of the table (58 distinct excerpts, every row and every variant), and check (3)
resolves 104 of 104 (the integration test against the six ingested reference PDFs, Postgres
BM25 as production searches). Totals: 54 rows
(53 + `accent_lighting`), 7 variants, 104 citation claims (89 on rows, 15 on variants; 58
distinct excerpts), 10 contested rows (26 before).

| Rule | Section, bucket (before -> after) | Citation | Status | Why |
|---|---|---|---|---|
| `toilet`, `bathtub`, `shower` | 1245 y5 -> 1250 building | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)"<br>ATG p.317: "Cabinet, counter, sink, faucet, mirror attached with permanent adhesive, toilet, towel rack, toilet paper holder, flush mounted medicine cabinet, tub, shower, vent fan, etc." | supported (was contested) | Exhibit A, Plumbing - Building (p.315) and Restroom Components (p.317) name each fixture: section 1250, RRP 27.5. The regulation lists "plumbing fixtures, such as sinks and bathtubs" (p.19). |
| `sink` | 1245 y5 -> 1250 building | ATG p.315: "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)"<br>ATG p.311: "Kitchen cabinets, counters, sink, includes plumbing costs." | supported (was contested) | Bathroom sinks (p.315) and kitchen sinks (Kitchen Components, p.311; p.315 also names "kitchen sinks") are section 1250. |
| `water_heater` | 1245 y5 -> 1250 building | ATG p.315: "all other components of a building plumbing system (water or gas) not specifically identified elsewhere" | supported (was contested) | Exhibit A does not name water heaters; the residual Plumbing - Building row covers every other component of the building's water or gas system, section 1250. The section 1245 hot water heater passages (restaurant, retail) serve equipment, not a dwelling unit. Judgment: a dwelling's water heater is a building plumbing component. |
| `garbage_disposal` | 1245 y5 -> 1250 building | ATG p.311: "All piping, drains, faucets, garbage disposal and waste piping, sinks and other components of a kitchen plumbing system"<br>ATG p.311: "All general wiring/outlets/switches in kitchen including garbage disposal." **(new)** | supported (was contested) | Exhibit A names the garbage disposal under Kitchen - Plumbing and Kitchen - Electrical, both section 1250. |
| `light_fixture` | 1245 y5 -> 1250 building | ATG p.313: "Includes lighting such as recessed and lay-in lighting, night lighting, as well as decorative lighting fixtures that provide substantially all the artificial illumination in the building." **(new)** | supported (was contested) | Light Fixtures - Interior - Building: section 1250, decorative or not, when it provides the illumination. The old excerpt (accent fixtures) moves to `accent_lighting`. |
| `accent_lighting` (new row; aliases `track_lighting`, `decorative_lighting` (was `light_fixture`), `neon_lighting`, `landscape_lighting`) | (none) -> 1245 y5 (57.0) | ATG p.313: "Light fixtures, such as neon, track lighting, or grow lights which are decorative in nature and not necessary for the operation or maintenance of the building."<br>ATG p.313: "Lighting that highlights only the landscaping or building exterior" **(new)**<br>ATG p.297: the Asset Class 57.0 sentence | supported (new) | Light Fixtures - Interior and Exterior - Accent/Decorative/Special Fixture: section 1245, 57.0, 5 years. The same row says decorative lighting that is the primary light source is section 1250; the row's note says so, and a generic light label stays on `light_fixture`. |
| `ceiling_fan` | 1245 y5 -> 1250 building | ATG p.298: "Ceiling fan typically with attached lights."<br>ATG p.298: "Lighting typically is the primary light source (artificial illumination) in the room." **(new)** | supported (was contested) | Exhibit A, Ceiling Fans: section 1250. The restaurant chapter citation (p.191, ceiling fans as props) is dropped: Exhibit A says other industries' guidance may not apply to residential rental property (p.296). |
| `smoke_detector` | 1245 y5 -> 1250 building | ATG p.305: "Battery powered or hard-wired fire alarms and CO2 detectors, generally mounted to ceilings or walls by screws." | supported (was contested) | Exhibit A, Fire Alarm and CO2 Detectors: section 1250. |
| `thermostat` | 1245 y5 -> 1250 building | ATG p.304: "demand controllers, thermostats, computer controls, outside air economizers"<br>ATG p.310: "Includes all components of a central heating, ventilating and air conditioning system." **(new)** | supported (was contested) | Exhibit A, Energy Management Systems (p.304, names thermostats) and HVAC - Residence (p.310, all components of the central system): section 1250. |
| `tile` | 1245 y5 -> 1250 building | ATG p.306: "ceramic or quarry tile, marble, paving brick, and other coverings cemented, mudded, or grouted to the floor" | supported (was contested) | Exhibit A, Floor Coverings - Permanent: section 1250; the regulation names tiling as a permanent covering. |
| `hardwood_floor` | 1245 y5 -> 1250 building | ATG p.306: "Includes floor covering that is affixed with permanent adhesive or nailed or screwed in place."<br>ATG p.306: "epoxy or sealers; and wood flooring." **(new)** | contested (kept, reworded) | Floor Coverings - Permanent names wood flooring: the documents' position is adopted (section 1250). Studies commonly treat glued wood flooring as 5-year, so the note stays and a hit goes to the engineer. |
| `bathroom_vanity` | 1245 y5 -> 1250 building | ATG p.298: "Restroom cabinetry, counters, sinks."<br>ATG p.317: the Restroom Components sentence (as `toilet`) | supported (was contested) | Exhibit A, Cabinetry - Restroom and Restroom Components: section 1250. |
| `closet_shelving` | 1245 y5 -> 1250 building | ATG p.305: "Built-in bookshelves, shelving and rods in bedroom, hallway, and linen closets, pantries, etc." | supported (was contested) | Exhibit A, Finish Carpentry: built-in shelving and rods in closets are section 1250. |
| `kitchen_cabinet`, `countertop` | 1245 y5, contested (unchanged) | unchanged | contested (kept) | The kitchen row stays: the ATG's case table (Metro National, p.100) gives practitioners a section 1245 path for cabinetry, and the engineer's report uses it. |
| `kitchen_cabinet` / `countertop` `[room=bathroom]` (variant) | -> 1250 building | ATG p.298: "Restroom cabinetry, counters, sinks."<br>ATG p.317: the Restroom Components sentence | supported (new variant) | Cabinetry - Restroom (p.298) and Restroom Components (p.317) name restroom cabinets and counters, section 1250; no passage gives restroom cabinetry a section 1245 path. |
| `mirror` | 1245 y5, contested -> 1250 building | ATG p.317: "mirror attached with permanent adhesive" | supported (was contested) | Restroom Components names the mirror attached with permanent adhesive: section 1250. Without an attachment (or `permanent`, `unknown`) the mirror is that one. |
| `mirror` `[attachment=removable]`, `[attachment=freestanding]` (variant) | -> 1245 y5 (57.0) | ATG p.191: "Decorative mobile props such as playground equipment, potted plants, hanging mirrors"<br>ATG p.322: "Window treatments such as drapes, curtains, louver, blinds, post construction tinting and interior decorative theme décor which are readily removable."<br>ATG p.297: the Asset Class 57.0 sentence | supported (new variant) | The p.191 passage is verbatim on page 191 (restaurant chapter, Décor and Accessories, section 1245, 57.0) and names hanging mirrors. Because Exhibit A warns that other industries' guidance may not apply to residential rental property (p.296), the residential matrix's own passage is cited too: readily removable interior décor is section 1245, 57.0, 5 years (p.322). Judgment: a hanging or freestanding mirror is readily removable décor; not contested. |
| `furniture` | 1245 y7 (00.11), contested -> 1245 y5 (57.0) | ATG p.308: "Includes all furniture and furnishings typically found in a furnished house, condo, apartment, etc." **(new)**<br>ATG p.308: "For example, beds, chairs, sofas, and tables." **(new)**<br>ATG p.297: the Asset Class 57.0 sentence | supported (was contested) | Exhibit A, Furnishings - Residential: section 1245, 57.0, 5 years. The asset class follows the activity the furniture serves, not the item. |
| `chair` | 1245 y7 (00.11), contested -> 1245 y5 (57.0) | ATG p.308: "For example, beds, chairs, sofas, and tables."<br>ATG p.297: the 57.0 sentence | supported (was contested) | As furniture (p.308 names chairs). |
| `desk`, `filing_cabinet` | 1245 y7 (00.11) -> 1245 y5 (57.0) | ATG p.308: "Includes all furniture and furnishings typically found in a furnished house, condo, apartment, etc."<br>ATG p.297: the 57.0 sentence | supported | A desk or file cabinet furnishing a dwelling is a residential furnishing (p.308). |
| `furniture`, `chair`, `desk`, `filing_cabinet` `[room=office]` (variants) | -> 1245 y7 (00.11) | ATG p.307: "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations."<br>RP 87-56 p.4: "Includes such assets as desks, files, safes, and communications equipment" | supported (new variants) | Exhibit A, Furnishings - Rental Office Furniture (p.307) and Rev. Proc. 87-56 class 00.11 (p.4): 7 years. |

Unchanged (no document names the item, or the guide gives both sides): `doorbell`,
`laminate_flooring`, `half_wall`, `window_guard`, `shed`, `concrete_pad`, `deck`,
`outdoor_lighting`, and every row not named above.

Alias changes besides `accent_lighting`'s: `lamp` now resolves to `furniture` (it resolved
to `light_fixture`; a lamp is a furnishing, ATG p.308, and as a building light fixture it
would have become 27.5 years). `shelf`, `shelves` and `storage_shelf` still resolve to
`closet_shelving`, now a building row, so the row gained a variant: `[attachment=freestanding]`
-> 1245 y5 (57.0), cited to ATG p.308 ("Includes all furniture and furnishings typically
found in a furnished house, condo, apartment, etc.") and the 57.0 sentence (p.297); a
freestanding shelf unit is a furnishing, a built-in one is finish carpentry (p.305).
Not changed, for the engineer: `office_furniture`, `office_chair` and `conference_table`
resolve to the 57.0 rows and take 00.11 only in an office room.

## Rows added 2026-10-30: the structure model's concealed rows (`RULES_VERSION` `2026-10-30.1`)

One row per concealed label of the structure model (`basis/domain/structure.py`,
STRUCTURE_MODEL.md sections 3.5-3.6). Each excerpt was read on the page image and checked
verbatim in the page's text as pdfplumber reads it (the production parser's input), avoiding
the first line of a matrix row, where the three columns interleave. Page numbers are PDF pages
of the ATG.

| Rule | Section, bucket | Citation | Status | Why |
|---|---|---|---|---|
| `foundation` | 1250 building | p.299: "Includes excavation and backfill for building footings and foundations, but does not include site grading costs."<br>p.299: "formwork, reinforcement, concrete block, and pre-cast or cast-in-place work." | supported | Concrete Footings and Foundations - Building. |
| `structural_framing` | 1250 building | p.321: "posts, beams, girders, curtain walls, tilt up panels, studs, framing, sheetrock, insulation, windows, doors, exterior façade, brick, masonry, etc."<br>p.317: "including but not limited to joists, rafters, deck, shingles, vapor barrier, skylights, trusses, girders, and gutters." | supported | Walls - Exterior names studs and framing; Roof names joists, rafters and trusses. |
| `exterior_wall_assembly` | 1250 building | p.321 (the Walls - Exterior list) | supported | Sheathing, housewrap and insulation are the exterior wall. |
| `roofing_assembly` | 1250 building | p.317 (the Roof list) | supported | Deck, vapor barrier and flashings are the roof. |
| `interior_partition` | 1250 building | p.321: "load bearing partitions regardless of height (typically constructed of studs and sheetrock or other materials) that divide or create rooms or provide traffic control."<br>p.321: "Includes rough carpentry and plaster, dry wall or gypsum board, and other finishes." | supported | Walls - Interior. |
| `painting` | 1250 building | p.320: "paint; ceramic or quarry tile, marble, stone, brick, and other" | supported | Wall Coverings - Permanent: "Includes interior and exterior paint" (the row's first line, interleaved on the page; the excerpt is its continuation). |
| `plumbing_rough_in` | 1250 building | p.315: "all other components of a building plumbing system (water or gas) not specifically identified elsewhere" | supported | Plumbing - Building (the water_heater row's passage). |
| `hvac_distribution` | 1250 building | p.310: "air conditioning system. HVAC systems that are primarily installed for the temperature, humidity, and ventilation of the residence and its occupants are building components." | supported | HVAC - Residence. |
| `electrical_wiring` | 1250 building | p.301: "Branch circuits are the electrical connections between a distribution or subpanel and the end-use equipment. This includes wire and conduit, junction boxes, wall switches, and outlets."<br>p.303: "May include feeder circuits, power service entrance equipment, transformers, and conduit." | supported | Electrical Branch Circuits - Building; Electrical - Primary and Secondary EDS - Building. |
| `fire_alarm_system` | 1250 building | p.305: "computer controls, sprinkler heads, piping or plumbing, pumps, visual and audible alarms, alarm control panels, heat and smoke detection devices, fire escapes, fire doors" | supported | Fire Protection and Alarm Systems. |
| `data_cabling` | 1245 y5 (57.0) | p.300: "internet, cable TV, satellite TV,"<br>p.297: the Asset Class 57.0 sentence | supported | Data Cable and Wiring: section 1245, 57.0, 5 years (the engineer's Communication / Data row is 5-year too). The short excerpt is the one contiguous fragment of the row's first sentence; it is 33 characters normalized. |
| `access_control` | 1250 building, contested | p.300: "Includes computerized door locks, encoders, computers, and other associated hardware of the computerized lock system."<br>p.318: "Examples include window and door locks; card key access systems; keyless entry systems;" | contested | Doors and Security Systems - Building put locks and keyless entry on the building side; the design partner's report has Access Control as 5-year. The documents' position is adopted and the hit goes to the engineer (a firm convention then carries the ruling). |
| `site_utilities` | 1250 building | p.319: "are used to distribute utility services from the property line to the building. Includes water, sanitary sewer, gas, and electrical services." | supported | Site Utilities. |

Count after this change: 67 rows (54 + 13), 7 variants, the contested set is 11 (the 10 of
section 4 plus `access_control`).

### Row added 2026-10-31 (`RULES_VERSION` 2026-10-31.1, STRUCTURE_MODEL.md section 9.2)

The structure model writes the Specialties residual (the square-foot model's kitchen
cabinets and countertops, kitchen sink and water heater less the itemized ones) as a row of
its own so the reconciliation counts it. No Exhibit A row names an unidentified residual; the
guide's residual estimation approach says where one goes. The excerpt was checked on the
page as pymupdf and the production parser read it, and inside one production chunk of
page 31 (2026-10-31).

| Label | Section / bucket | Citation | Verdict | Note |
|---|---|---|---|---|
| `specialties_residual` | 1250 building, contested | p.31 (ch. 3 C.4(1)): "remaining or "residual" cost is then simply assigned to the building and/or other long-lived assets." | contested | The residual stays with the building until it is identified (never 5-year: C.4(3)'s skew is toward section 1245); a study that measures the cabinets itemizes them as 5-year, so the hit goes to the engineer. |

Count after this change: 68 rows, 7 variants, the contested set is 12 (adds
`specialties_residual`).
