# Vision architecture survey: from a property's photos to the engineer's component schedule

Date: 2026-10-16. Companion to EVAL_REPORT section 26 ("Vision architecture: alternatives
measured"), which tests the candidate architectures at the end of this document on the one real
study (52 photos). The question, in Bryan's words: do not assume the current pipeline (per-photo
VLM counting with two narrow prompts, SAM 3 highlight masks, per-room reconciliation, materials
into the rules table) is the right way; find how this class of problem is solved elsewhere and
test the credible alternatives. Earlier research this builds on: VISION_RESEARCH.md,
VISION_DETECTION_RESEARCH.md, COMPONENT_TAXONOMY.md and EVAL_REPORT sections 10, 15, 19 and 21.

Scope: how to turn ~50 site-visit photos (800x600 JPG) of one property into the engineer's ~46-row component schedule (presence, object counts, surface materials), without fine-tuning. Every claim carries a source. "Vendor claim" means the number comes from the company's own marketing or press release and has not been independently checked. "No public accuracy figure found" means I searched and found none; it does not mean none exists.

Research method and limits: web search and page fetches on 2026-10-16 (a research pass, then checked against what was run). Several numbers were read from arXiv HTML tables whose text extraction was partly garbled; where alignment was uncertain I say so. Every "implication for us" below was a hypothesis when written; the "Measured" line under each candidate architecture gives what EVAL_REPORT section 26 found on our photos.

---

## 1. Property-inspection and insurance-claims AI

**Common pattern across vendors.** The insurance-claims vendors that publish anything describe the same shape: guided capture (often several photos per room or a walkthrough scan) -> geometry (room dimensions, sketch) -> computer vision for damage/material class -> carrier pricing rules -> a human estimator reviews before the adjuster sees it. None publish per-object count accuracy or material accuracy against a field-verified ground truth. Their core output is *measurements and a sketch*, which our schedule mostly does not need; the item lists come from rules on top of geometry plus a reviewer.

- **Hover.** Phone walkthrough scan produces a 3D model, measurements, sketches and photos positioned in the model; sketches export to Xactimate ([Hover Interiors](https://hover.to/interiors/), [Hover for insurance help article](https://help.hover.to/en/articles/12641171-hover-for-insurance)). Claims no LiDAR requirement. Accuracy: "within 2-5%" of traditional measurement, attributed to unnamed carriers (vendor claim; no method or sample size given) ([Hover Interiors](https://hover.to/interiors/)). Requires their guided capture, not arbitrary photos. No per-object counts or material schedule published.
- **CAPE Analytics (Moody's).** Aerial/satellite imagery -> property attributes such as roof condition (5-point Roof Condition Rating with reason codes), roof age, solar panels ([TechCrunch, Jan 2025 acquisition](https://techcrunch.com/2025/01/13/moodys-agrees-to-acquire-cape-analytics-which-develops-geospatial-ai-for-insurance-providers), [CB Insights profile](https://www.cbinsights.com/investor/cape-analytics)). Exterior/roof only. No public accuracy figure found (only adoption claims).
- **Verisk / Xactware.** Two distinct things. (a) **XactAI** inside Xactimate auto-names and describes uploaded photos; it does not produce line items from photos ([Verisk blog](https://www.verisk.com/blog/xactai-built-from-your-insights-delivered-through-your-workflows/), [XactAI page](https://www.xactware.com/xactai.html)). (b) **XactGen Capture**: policyholder browser capture -> measurements, sketch and Xactimate line items; Verisk says "computer vision, not generative AI" produces them, every estimate is reviewed by an estimator, and scope is currently interior water damage ([XactGen page](https://www.xactware.com/xactgen.html)). No public accuracy figure found. Design lesson: a narrow scope (one claim type) plus a human estimator.
- **Tractable (with Verisk, 2023).** Policyholder photos -> identify, classify and measure damage -> estimate in Xactimate ([Verisk newsroom](https://www.verisk.com/newsroom/tractable-teams-up-with-verisk-to-offer-ai-powered-estimates-for-property-damage/), [Tractable property](https://tractable.ai/en/industries/property-claims)). Published figures are about speed, not accuracy. No public accuracy figure found.
- **EagleView Assess.** Drone roof capture -> AI damage detection (hail, wind); Sept 2025 release claims fewer false positives and misses, with no numbers ([EagleView Assess](https://www.eagleview.com/products/eagleview-assess/), [GlobeNewswire 2025-09-10](https://www.globenewswire.com/fr/news-release/2025/09/10/3147793/0/en/eagleview-assess-expands-drone-roof-inspection-solution-to-include-large-and-complex-roofs-ai-driven-models-underpin-offering.html)). Aerial property data covers roof material/condition and parcel attributes, nothing interior ([Geo Week News](https://www.geoweeknews.com/articles/eagleview-launches-new-property-data-ecosystem/)). No independent accuracy figure found.
- **Hosta AI.** Press releases describe 4-6 photos per room -> automatic sketch and proposed line items; partner copy says it "determines building materials" ([BusinessWire 2022, CoreLogic integration](https://www.businesswire.com/news/home/20220518005489/en/Hosta-A.I.-Integrates-With-CoreLogic-to-Provide-Automated-Property-Claims-Assessments-and-Estimates-for-the-Insurance-Industry), [Guidewire partner blog](https://www.guidewire.com/ja/resources/blog/industry-trends/empower-policyholders-to-process-claims-when-resources-are-stretched)). On 2026-10-16, hosta.ai returned a 301 redirect to elaraspatial.ai; I did not confirm a rebrand. That site describes "a few interior photos" from any phone -> 3D room reconstruction -> line-item estimate traced to image and carrier rule, "human-reviewed" for every estimate, and claims "80%+" of estimates returned without errors (vendor claim, no method) ([elaraspatial.ai](https://www.elaraspatial.ai/)). Closest in input to us (ordinary phone photos), but still geometry-first and human-reviewed.
- **Plnar.** Photos -> 2D/3D room models and dimensions; integrations with Xactimate/Guidewire ([Plnar PDF](https://plnar.ai/wp-content/uploads/2021/06/PlnarforEveryClaim.pdf), [Coverager 2019](https://coverager.com/plnar-unveils-first-measure-ready-photo-technology/)). Measurement product. No public accuracy figure found.
- **DocuSketch.** Floor-plan service; Xactimate-ready sketch including doors, windows, cabinets and fixtures, produced with "AI and skilled craftsmanship" plus a QA step, i.e. human-in-the-loop drafting ([DocuSketch](https://docusketch.com/?p=3903)). Encircle: nothing found on its AI.
- **Restb.ai** (real estate, not claims). 700+ tags per photo: room type, features, views, style, condition (mapped to UAD C1-C6 / Q1-Q6), damage; results per image and per property; FAQ states results are AI-generated with no human validation; pricing is a monthly fee plus overage, no public rates ([Restb.ai FAQ](https://restb.ai/company/faq/)). FAQ does not say it counts objects. No public accuracy figure found.
- **Zillow.** 3D Home / interactive floor plans from 360 panoramas, with AI room dimensions and listing photos placed on the plan ([National Mortgage Professional](https://nationalmortgageprofessional.com/news/76404/zillow-launches-next-home-tours)); Virtual Staging (2025) adds furniture rather than recognizing it ([Zillow PDF](https://s25.q4cdn.com/975009107/files/doc_news/Zillow-brings-AI-powered-Virtual-Staging-to-Showcase-listings-2025.pdf)). Research: ZInD dataset (CVPR 2021): 71,474 panoramas from 1,524 unfurnished homes with layouts, floor plans and window/door positions, Apache-2.0 ([CVPR paper](https://openaccess.thecvf.com/content/CVPR2021/html/Cruz_Zillow_Indoor_Dataset_Annotated_Floor_Plans_With_360deg_Panoramas_and_CVPR_2021_paper.html)). I found no public Zillow feature-recognition accuracy on listing photos.
- **Matterport (Cortex AI / Property Intelligence).** Works on Matterport 3D captures (digital twins): automatic measurements, room names, layouts, "defurnish" (2024 fall release), object recognition mentioned without a class list ([Cortex AI](https://matterport.com/cortex-ai), [RISMedia 2024](https://www.rismedia.com/2024/02/20/matterport-launches-property-intelligence/)). Requires 3D capture. No public accuracy figure found.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| Hover | 3D model, measurements, sketch | "2-5%" vs traditional (vendor) | Not public | Neither (SaaS) | Geometry, not inventory; needs guided scan |
| CAPE / Moody's | Roof condition, roof age (aerial) | None public | Not public | Neither | Exterior roof only; could cross-check Roofing row |
| Verisk XactGen Capture | Measurements, sketch, line items (water claims) | None public; human estimator reviews every one | Not public | Neither | Narrow scope + human review is the industry norm |
| Tractable | Damage classify/measure -> Xactimate | Speed claims only | Not public | Neither | Damage, not inventory |
| EagleView Assess | Drone roof damage | Qualitative vendor claims | Not public | Neither | Exterior only |
| Hosta / elaraspatial | Photos -> 3D room, materials, line items | "80%+ error-free" (vendor), human-reviewed | Not public | Neither | Closest analog; still geometry-first + reviewer |
| Restb.ai | 700+ tags/photo, condition | None public | Monthly + overage | Neither (API) | Could be a second opinion on features/condition |
| Matterport | Measurements, layouts on 3D twin | None public | Subscription | Neither | Requires 3D capture we do not have |

Bottom line for topic 1: no vendor found publishes object-count or material accuracy from ordinary photos. The ones that produce line items keep a human reviewer in the loop, so the "engineer edits the output" design matches what the industry does.

---

## 2. Cost-segregation vendors using AI on photos

- **Segtax** (Richmond VA, launched mid-2025): a contractor does a LiDAR iPhone 3D scan instead of an engineer visit; software extracts assets; studies reviewed by cost-seg engineers ([Richmond BizSense 2025-06-09](https://richmondbizsense.com/2025/06/09/scotts-addition-startup-segtax-aims-to-automate-real-estate-depreciation-analysis/)). Needs 3D scan. No accuracy figure found.
- **RentalWriteOff** (residential DIY): says it reads property photos to judge finish quality (e.g. granite, LVP) and generate an asset schedule. A notice on the WCG page says WCG no longer recommends it, that the AI list "is not reviewed" by a human, and cites a report that counted the same grill twice ($3,000 and $800) because the photographer moved it ([WCG blog](https://wcginc.com/blog/cost-segregation-mixing-high-tech-with-sticks-and-bricks/)). This is a public example of the cross-photo double-counting failure we must design against.
- **Overline**: property records, satellite/street imagery, owner photos; licensed partner engineers make final classifications; claims remote analysis is "equally accurate" for standard properties (vendor claim, no metric) ([Overline blog](https://overlineiq.com/blog/best-cost-segregation-companies-compared)).
- **Segmenti** (segmenti.ai -> segmenti.io), **Findcostseg**, **AI Cost Depreciation**: exist as "automated cost segregation" products; their pages gave no method or accuracy ([segmenti.io](http://segmenti.io/), [startuphub Findcostseg](https://www.startuphub.ai/startups/findcostseg.md), [aicostdepreciation.com](https://aicostdepreciation.com/)).
- **KBKG, CSSI, Engineered Tax Services, Cost Seg EZ**: no public description of photo AI found.
- **Industry context**: an Accounting Today opinion (July 2026, author runs a competing platform, Unlevered) criticizes non-reproducible AI studies and proposes machine vision for materials (hardwood vs carpet) feeding a *rules engine*, with AI not doing the calculations ([Accounting Today](https://www.accountingtoday.com/opinion/ai-slop-is-flooding-the-cost-segregation-industry)). A companion piece says IRS examiners focus on whether the work product (engineering report, timestamped photos, engineer of record) exists ([Accounting Today ATG piece](https://www.accountingtoday.com/opinion/cost-segregation-in-the-age-of-ai-what-the-irs-audit-technique-guidelines-reveal)).

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| Segtax | LiDAR scan -> assets, engineer review | None | Not public | Neither | Different capture; confirms engineer-in-loop |
| RentalWriteOff | Photos -> finish quality, schedule | None; public double-count anecdote | Not public | Neither | Cross-photo dedup is a real, visible failure |
| Overline | Imagery + records, engineer finalizes | "Equally accurate" (vendor) | Not public | Neither | Same human-final pattern |
| Others (Segmenti, Findcostseg) | Unclear | None | Not public | Neither | Nothing to learn yet |

Bottom line: I found no cost-seg vendor that publishes how well it gets presence, counts or materials from photos. Our photo -> structured facts -> rules table design matches what the Accounting Today author argues for, and no vendor publishes a better method.

---

## 3. Real-estate listing feature extraction research

- **Airbnb amenity detection** ("Amenity Detection and Beyond - New Frontiers of Computer Vision at Airbnb"): object detection on listing photos, starting from Open Images V4, deployed as a REST service ([summary, Analytics India Mag](https://analyticsindiamag.com/how-online-companies-are-tapping-into-object-detection-to-improve-user-experience/); [Bourke replication with Detectron2](https://www.mrdbourke.com/airbnb-amenity-detection/)). They trained a detector, which we cannot do. I did not retrieve the original post's mAP figures.
- **Expedia room grouping** (arXiv 2507.00263, 2025): room-type classifier -> pairwise "same space" overlap model -> spectral clustering using room-count metadata -> fine-tuned MLLM to match bedrooms to bed configurations ([arXiv 2507.00263](https://arxiv.org/abs/2507.00263)). A third-party summary reports DINOv2 room-type F1 of 0.97 bedroom, 0.98 bathroom, 0.86 living room (from search summary of the same paper; I did not verify the table). Directly relevant to our "group photos into rooms" step: grouping is done with a dedicated pairwise overlap score plus clustering, not a free-form VLM.
- **VLMs for tax-assessment features** (ENIAC 2025, Brazil): 12 VLMs, zero-shot, listing photos, 11 legally defined construction categories (flooring, structure, finishes...). Best: Gemini 1.5 Pro macro-F1 0.77, Gemini 1.5 Flash 0.76; "Sanitary Installation" and "External Finishes" were hardest, partly because they are not visible in typical photos ([ENIAC 2025 paper](https://sol.sbc.org.br/index.php/eniac/article/view/38759)). This is the closest peer-reviewed analog to our task: zero-shot VLM, property photos, category presence. Peer-reviewed (national conference), older models.
- **Zillow ZInD** (CVPR 2021): see topic 1; layouts, windows and doors on panoramas, not ordinary photos.
- Other: MIT thesis on VLM condition/aesthetic scoring of listing photos for hedonic pricing ([DSpace@MIT](https://dspace.mit.edu/handle/1721.1/158862)).

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| Airbnb amenity detector | Amenity boxes | Not retrieved | Trained in-house | N/A | Needs training data |
| Expedia room grouping | Photos grouped into rooms | Room-type F1 0.86-0.98 (secondary) | DINOv2 + small models | Local (DINOv2 in transformers) | Replace VLM room grouping with embedding overlap + clustering |
| ENIAC 2025 VLM study | 11 construction-feature labels | Gemini 1.5 Pro macro-F1 0.77 | API | API | Zero-shot VLM presence is good but not great; invisible items cap recall |

---

## 4. Trained indoor semantic / panoptic segmentation (no fine-tuning)

**ADE20K-150 classes relevant to a building schedule** (verified from the official `objectInfo150.csv`, [CSAILVision/sceneparsing](https://raw.githubusercontent.com/CSAILVision/sceneparsing/master/objectInfo150.csv)): wall, building, floor, ceiling, windowpane, cabinet, door, curtain, shelf, mirror, rug, fence, wardrobe, lamp, bathtub, railing, column, chest of drawers, counter, sink, fireplace, refrigerator, stairs, screen door, stairway, bookcase, blind, toilet, countertop, stove, kitchen island, bar, towel, light, chandelier, awning, streetlight, bannister, washer, swimming pool, oven, step, microwave, dishwasher, hood, sconce, fan, shower, radiator, plus outdoor road, sidewalk, path, grass, tree, house.

**Not in ADE20K** (these need another tool): electrical outlet, light switch, smoke detector, faucet, roof (only "house"/"building"), gutter, driveway (only road/sidewalk/path), garage door, deck, water heater, HVAC condenser, mailbox, and *any floor or wall material* (carpet vs tile vs hardwood is not a class; "rug" is a movable rug). So ADE20K can supply surface *regions* (floor, wall, ceiling masks) and counts for maybe a third of our countable classes, but not materials and not outlets.

**Numbers (ADE20K val):**
- **Mask2Former** (CVPR 2022): Swin-L semantic 57.3 mIoU; Swin-L-FaPN 57.7 mIoU (multi-scale) ([arXiv 2112.01527](https://arxiv.org/abs/2112.01527)); Swin-L panoptic 48.1 PQ (secondary listing; I could not read the paper's panoptic table directly) ([sotaverified listing](https://sotaverified.org/papers/masked-attention-mask-transformer-for)). HF transformers: `Mask2FormerForUniversalSegmentation`, checkpoint `facebook/mask2former-swin-large-ade-panoptic`, license "other" ([HF card](https://huggingface.co/facebook/mask2former-swin-large-ade-panoptic)).
- **OneFormer** (CVPR 2023): Swin-L 640: 49.8 PQ, 35.9 AP, 57.0 mIoU; DiNAT-L 1280 COCO-pretrained: 53.4 PQ, 58.4 mIoU; MIT license ([SHI-Labs/OneFormer](https://github.com/SHI-Labs/OneFormer)). HF: `shi-labs/oneformer_ade20k_swin_large`, `OneFormerForUniversalSegmentation`, MIT, semantic/instance/panoptic via `task_inputs` ([HF card](https://huggingface.co/shi-labs/oneformer_ade20k_swin_large)).
- **SegFormer** (NeurIPS 2021): B4 50.3 mIoU ([arXiv 2105.15203](https://arxiv.org/abs/2105.15203)). HF `nvidia/segformer-b5-finetuned-ade-640-640` (`SegformerForSemanticSegmentation`); license is NVIDIA's, which a downstream repackager describes as non-commercial research/evaluation only ([HF card](https://huggingface.co/nvidia/segformer-b5-finetuned-ade-640-640), [LibreSegformer note](https://huggingface.co/LibreYOLO/LibreSegformerb1-sem)). **License likely blocks commercial use.**
- **EoMT** (CVPR 2025 highlight, "Your ViT is Secretly an Image Segmentation Model"): plain ViT encoder-only; DINOv3 EoMT-L reaches "up to 59.5 mIoU" on ADE20K at 512; MIT license; in HF transformers since 2025-06-27 under `tue-mps/*` (e.g. `tue-mps/ade20k_semantic_eomt_large_512`) ([GitHub](https://github.com/tue-mps/eomt), [HF docs](https://huggingface.co/docs/transformers/en/model_doc/eomt)). ADE20K panoptic PQ not found on the repo page.
- **DINOv3** (arXiv 2508.10104): ADE20K linear probe ~55.9 mIoU (secondary leaderboard); higher numbers listed for DINOv3 + Mask2Former decoder with test-time augmentation ([arXiv 2508.10104](https://arxiv.org/abs/2508.10104), [codesota](https://www.codesota.com/model/dinov3-linear-probe)). Not verified against the paper's table.
- **Open-vocabulary segmentation**: much lower on ADE-150 than trained models: SAN ViT-L (CVPR 2023, COCO-trained) about 32.1 mIoU on ADE-150 (table alignment unverified) ([arXiv 2302.12242](https://arxiv.org/abs/2302.12242)); CAT-Seg (CVPR 2024) reproduced at 27.2 on A-150 in the SED supplement ([SED supplemental](https://openaccess.thecvf.com/content/CVPR2024/supplemental/Xie_SED_A_Simple_CVPR_2024_supplemental.pdf)). Open-vocab segmentation trades ~25 mIoU for vocabulary.
- **Small objects**: ADE20K mIoU averages hide per-class weakness. I did not find a source giving per-class IoU for sconce/outlet-sized classes for these models, so treat "small fixtures are weak" as expected but unmeasured here. Measure on our photos.

**Replicate**: `hassamdevsy/mask2former` ("trained on ADE 20k", backbone/variant not stated), Nvidia T4, about $0.019/run ([Replicate](https://replicate.com/hassamdevsy/mask2former)); `meta/mask2former` cannot run (unsupported Cog version) ([Replicate](https://replicate.com/meta/mask2former)); `cjwbw/oneformer` page says no versions pushed ([Replicate](https://replicate.com/cjwbw/oneformer)). No SegFormer ADE20K on Replicate found. **Local**: all of the above load in HF transformers; Swin-L/ViT-L at 800x600 on Apple silicon (MPS) or CPU is feasible for 52 photos, though I did not measure latency.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| Mask2Former Swin-L | 150-class panoptic/semantic masks | 57.3 mIoU (paper); 48.1 PQ (secondary) | ~$0.019/run on T4 | Replicate (community); HF local | Floor/wall/ceiling masks + counts for doors, windowpanes, sinks, toilets, appliances |
| OneFormer Swin-L / DiNAT-L | Same, one model for 3 tasks, MIT | 49.8-53.4 PQ, 57.0-58.4 mIoU | Local compute | HF local; Replicate page empty | Best license/accuracy combo for local |
| EoMT (DINOv3) | Fast ViT-only segmentation, MIT | up to 59.5 mIoU (repo) | Local compute | HF local | Simplest to run; check panoptic checkpoints |
| SegFormer-B5 | Semantic only | B4 50.3 mIoU | Local | HF local | Non-commercial license; avoid |
| SAN / CAT-Seg | Open-vocab masks | ~27-32 mIoU on ADE-150 | Local | Local (repo code) | Too weak for our use vs trained models |

---

## 5. Open-vocabulary detectors, and SAM 3 against them

**Key detector numbers (zero-shot; AP = box AP unless noted):**
- **Grounding DINO** (ECCV 2024): 52.5 AP COCO zero-shot; 26.1 mean AP on ODinW ([arXiv 2303.05499](https://arxiv.org/abs/2303.05499)). HF `IDEA-Research/grounding-dino-tiny/-base`.
- **MM-Grounding-DINO** (arXiv 2401.02361): tiny 50.6 COCO; LVIS minival 41.4 AP / val 31.9 AP (best tiny); Swin-L 53.0 COCO zero-shot. HF `openmmlab-community/mm_grounding_dino_*` ([HF docs](https://huggingface.co/docs/transformers/model_doc/mm-grounding-dino)).
- **LLMDet** (CVPR 2025): LVIS minival AP 50.7 (tiny) / 54.3 (base) / 56.6 (large); LVIS val 44.3 / 47.8 / 50.2; runs through the MM-Grounding-DINO class in transformers, `iSEE-Laboratory/llmdet_large` ([HF docs table](https://huggingface.co/docs/transformers/model_doc/mm-grounding-dino), [arXiv 2501.18954](https://arxiv.org/abs/2501.18954)).
- **OWLv2** (NeurIPS 2023): L/14 LVIS rare AP 44.6 (from 31.2 with OWL-ST self-training) ([arXiv 2306.09683](https://arxiv.org/abs/2306.09683)). HF `google/owlv2-*`.
- **YOLO-World** (CVPR 2024): 35.4 AP LVIS at 52 FPS (paper claim, cited in [YOLOE search summary](https://docs.ultralytics.com/models/yoloe)); Replicate `zsxkib/yolo-world`, L40S, GPLv3 ([Replicate](https://replicate.com/zsxkib/yolo-world)).
- **YOLOE** (ICCV 2025): YOLOE-v8-S beats YOLO-Worldv2-S by 3.5 AP on LVIS at 1.4x speed ([arXiv 2503.07465](https://arxiv.org/abs/2503.07465), [Ultralytics docs](https://docs.ultralytics.com/models/yoloe)).
- **Florence-2** (CVPR 2024): zero-shot COCO detection 34.7 (base) / 37.5 (large); MIT; Replicate `lucataco/florence-2-large` ([HF card mirror](https://huggingface.co/GuiAworld/Florence-2-large), [Replicate](https://replicate.com/lucataco/florence-2-large/readme)).
- **DINO-X Pro** (arXiv 2411.14347): COCO 56.0, LVIS-minival 59.8, LVIS-val 52.4, rare 63.3 / 56.5 ([arXiv](https://arxiv.org/abs/2411.14347)). API only via DeepDataSpace (`dds-cloudapi-sdk`); no public per-call price found ([dino-x-api repo](https://github.com/idea-research/dino-x-api)).
- **T-Rex2** (ECCV 2024): text + visual prompts; FSC-147 MAE 10.94 and FSCD-LVIS AP 43.35 as quoted in a later paper ([T-Rex-Omni arXiv 2511.08997](https://arxiv.org/abs/2511.08997)); API.
- **Rex-Omni** (arXiv 2510.12798, CVPR 2026): 3B MLLM on Qwen2.5-VL that emits box coordinates as tokens; authors report zero-shot COCO/LVIS comparable to or above Grounding DINO; no counting result found ([CVPR 2026](https://openaccess.thecvf.com/content/CVPR2026/html/Jiang_Detect_Anything_via_Next_Point_Prediction_CVPR_2026_paper.html)).
- **OmDet-Turbo**: in transformers; I did not collect numbers.

**SAM 3 against detectors and VLMs** (all from SAM 3 paper tables, [arXiv 2511.16719](https://arxiv.org/html/2511.16719v1); text extraction partly garbled, values below are the ones with clear alignment):

| Model | LVIS cgF1 | LVIS AP | SA-Co/Gold cgF1 |
|---|---|---|---|
| OWLv2* | 29.3 | 43.4 | 24.6 |
| Grounding DINO-T | 14.7 | - | 3.3 |
| LLMDet-L | 35.1 | 36.3 | 6.5 |
| DINO-X | - | 38.5 (paper) | 21.3 (API) |
| Gemini 2.5 | 13.4 | - | 13.0 |
| **SAM 3** | **37.2** | **48.5** (text says 48.8) | **54.1** (human 72.8) |

(* partly trained on LVIS.) The LVIS "AP" here is the SAM 3 paper's mask-based protocol; LLMDet-L's 36.3 here vs 56.6 box AP in its own repo shows that the two protocols are not comparable. ODinW13 zero-shot: SAM 3 61.0 vs Grounding DINO 1.5 Pro 58.7 vs Gemini 2.5 Pro 33.7.

**Counting** (SAM 3 paper): CountBench accuracy SAM 3 93.8% (MAE 0.12), Gemini 2.5 Pro 92.4%, Molmo-72B 92.4%, Qwen2-VL-72B 86.7%, DINO-X 82.9%; PixMo-Count: Molmo-72B 88.8%, SAM 3 86.2%, DINO-X 85.0%, Gemini 2.5 Pro 78.2% ([arXiv 2511.16719](https://arxiv.org/html/2511.16719v1)). No GPT entry.

Interpretation: on the published benchmarks SAM 3 is already the strongest open-vocabulary detector, by a wide margin on SA-Co. Swapping SAM 3 out for Grounding DINO/OWLv2/LLMDet is unlikely to help *on average*. The exceptions to test are classes where a detector trained on LVIS/O365 vocabulary may know a term SAM 3 handles poorly (e.g. "electrical outlet", "smoke detector"); DINO-X Pro has the best LVIS rare-class numbers but is API-only.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| SAM 3 | Concept masks + counts | SA-Co/Gold 54.1 cgF1; CountBench 93.8% | Replicate (current) | Replicate; HF local | Already best overall; keep as baseline |
| LLMDet-L | Boxes, open vocab | LVIS minival 56.6 AP (own protocol) | Local | HF local | Cheap second detector per class |
| OWLv2 | Boxes; image-exemplar queries | LVIS rare 44.6 AP | Local | HF local; community Replicate | Exemplar (one-shot) mode is unique |
| DINO-X Pro | Boxes, open vocab | LVIS-val 52.4 AP | API, price not public | API only | Try for rare classes if pricing is OK |
| YOLO-World / YOLOE | Fast boxes | 35.4 AP LVIS (YOLO-World) | ~$0.0098 per 1,000 runs (Replicate listing) | Replicate; local | Speed not our bottleneck; weaker |
| Florence-2 | Boxes, captions | 37.5 COCO zero-shot | Cheap | Replicate; HF local | Weaker; skip |
| Rex-Omni | Boxes/points as tokens | "Comparable to GDINO" (authors) | Local GPU | HF (Qwen-based) | MLLM detector; untested for counting |

---

## 6. Material recognition

- **OpenSurfaces** (Bell et al., SIGGRAPH 2013): crowdsourced surface segments with material labels, the data behind MINC ([MINC paper](https://arxiv.org/abs/1412.0623)).
- **MINC** (Bell et al., CVPR 2015): 23 categories; patch classification 85.2% mean class accuracy; full-image per-pixel (FCN + CRF) 73.1% mean class accuracy; data and models released ([CVPR 2015](https://openaccess.thecvf.com/content_cvpr_2015/html/Bell_Material_Recognition_in_2015_CVPR_paper.html)). Old CNNs (Caffe era).
- **DMS, Dense Material Segmentation** (Upchurch and Niu, **ECCV 2022**, not CVPR): 3.2M segments on 44,560 images; baseline 0.729 pixel accuracy, 0.585 mean class accuracy, 0.420 mIoU over 46 materials ([ECVA paper](https://www.ecva.net/papers/eccv_2022/papers_ECCV/html/6243_ECCV_2022_paper.php), [Apple ML](https://machinelearning.apple.com/research/dense-material)). Weights released: `DMS46_v1.pt` (~170 MB) loaded via `--jit_path`, i.e. TorchScript; sample code requires a GPU; labels are CC-BY-NC 4.0; model license is a separate LICENSE file I did not read ([apple/ml-dms-dataset](https://github.com/apple/ml-dms-dataset)). Must check the model license before commercial use. A 2026 follow-up (SegFormer-B5 + stabilized training) reports 0.457 mIoU on the original split and warns that a re-split inflates scores while hurting out-of-distribution performance; no weights mentioned ([arXiv 2605.23747](https://arxiv.org/abs/2605.23747)). The 46-material taxonomy (in `taxonomy.json`, not read) is the only trained dense material model I found that covers carpet/tile/wood-type classes; whether it separates wallpaper vs paint must be checked.
- **VLM/CLIP material benchmarks**: Wu and Maji, "How well does CLIP understand texture?" (ECCV 2022 workshop) tests CLIP zero-shot on texture and material datasets; I could not retrieve the numbers ([arXiv 2203.11449](https://arxiv.org/abs/2203.11449)). MatCha (EMNLP Findings 2025) is about microscopy materials characterization, not building surfaces ([ACL Anthology](https://aclanthology.org/2025.findings-emnlp.235/)). I found **no public benchmark of GPT/Claude/Gemini on building-surface material recognition** (carpet vs tile vs hardwood, paint vs wallpaper). The ENIAC 2025 study (topic 3) found "External Finishes" among the hardest categories.
- **Does cropping help a VLM?** "MLLMs Know Where to Look" (ICLR 2025): accuracy on small details depends causally on subject size; attention-guided crops (ViCrop) raise LLaVA-1.5 TextVQA 47.8 -> 55.2 and V* 42.4 -> 62.3, with no loss on general benchmarks; on LLaVA-NeXT V* 58.1 -> 61.8 ([arXiv 2502.17422](https://arxiv.org/html/2502.17422v1)). These are 7B models; the gain shrinks with a stronger model, and no frontier-model result exists in that paper. SPARC (ICML 2026) reports separating perception from reasoning helps on V* (+6.7 for Qwen3-VL 4B) ([arXiv 2602.06566](https://arxiv.org/abs/2602.06566)). Our own "crop verification" was rejected for counting; for *materials*, a crop of a known surface region is a different question (what is this surface?) and is supported by this evidence only for small models.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| MINC (2015) | 23-class material patches | 85.2% patch, 73.1% dense mean-class | Local | Old Caffe models | Outdated; taxonomy useful |
| DMS46 (Apple, ECCV 2022) | 46-class dense material map | 0.420 mIoU, 0.729 pixel acc | Local GPU | TorchScript; not on Replicate | Candidate per-pixel material prior; check license |
| CLIP zero-shot | Material by text prompt | Number not retrieved | Local, cheap | HF local | Cheap tie-breaker; unvalidated |
| Frontier VLM on crops | Free-text material | No building-material benchmark found; ViCrop helps small VLMs | API | API | Must measure on our photos |

---

## 7. Multi-image and whole-house understanding

- **MuirBench** (ICLR 2025): 12 multi-image tasks, 2,600 questions; GPT-4o 68.0%, Gemini Pro 49.3%, about 25 and 44 points below humans ([arXiv 2406.09411](https://arxiv.org/abs/2406.09411)).
- **MMIU** (ICLR 2025): 52 tasks, 77K images; GPT-4o 55.7%, spatial tasks hardest ([arXiv 2408.02718](https://arxiv.org/abs/2408.02718)).
- **MMSI-Bench** (ICLR 2026): multi-image spatial reasoning; GPT-5 41.9%, best open model ~30.7%, humans >97%, chance 25% ([arXiv 2505.23764](https://arxiv.org/abs/2505.23764)).
- **VSI-Bench "Thinking in Space"** (CVPR 2025): video of indoor scenes; object-count task (counting unique objects across a walkthrough = our dedup problem): GPT-4o 46.2, Gemini 1.5 Pro 56.2, Gemini 2.5 Pro 48.2, GPT-5-Chat 52.4 (re-evaluations reported in [InternSpatial arXiv 2506.18385](https://arxiv.org/abs/2506.18385) and [arXiv 2603.06704](https://arxiv.org/abs/2603.06704); original [arXiv 2412.14171](https://arxiv.org/abs/2412.14171)). Overall humans 79% vs best MLLM 49% (secondary summary).
- **Degradation with image count**: MIMIC (arXiv 2601.07812) has a cross-image counting task and finds LVLMs "often fail to aggregate information across images" ([arXiv 2601.07812](https://arxiv.org/abs/2601.07812)); MM-NIAH (NeurIPS 2024): with more than five images in a document, image-needle accuracy falls below 10%, partly because models miscount images ([arXiv 2406.07230](https://arxiv.org/abs/2406.07230)); MMDU reports declines as image count and context grow ([arXiv 2406.11833](https://arxiv.org/abs/2406.11833)).
- **Cross-view dedup specifically**: CrossView (Aug 2026) reports cross-view counting among the weakest tasks, GPT-5.2 included ([arXiv 2608.15539](https://arxiv.org/abs/2608.15539)); SpatialMosaic finds unreliable cross-view instance correspondence ([arXiv 2512.23365](https://arxiv.org/abs/2512.23365)). **I found no benchmark showing a frontier model reliably de-duplicating objects across ~50 unordered photos.** The grill counted twice (topic 2) is the same failure seen in production.
- **Frontier API limits**: Claude API accepts up to 600 images per request (100 for 200k-context models); above 20 images each image must be at most 2000 px per side; an image costs ceil(w/28) x ceil(h/28) visual tokens, so an 800x600 photo is 29 x 22 = 638 tokens and 52 photos are about 33k tokens ([Anthropic vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)). Docs also list counting as a known limitation for many small objects.
- **Floorplans from sparse photos**: ZInD (topic 1) is panorama-based; I did not find a production-ready method for floor plans from ~50 unordered perspective photos and did not look further, per scope.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| MuirBench / MMIU | Multi-image QA scores | GPT-4o 68.0 / 55.7 | N/A | N/A | Multi-image is much weaker than single-image |
| MMSI-Bench | Multi-image spatial | GPT-5 41.9 vs human 97 | N/A | N/A | Cross-view spatial reasoning is near chance |
| VSI-Bench count | Unique-object counts across views | 46-56 for frontier models | N/A | N/A | Whole-house dedup by VLM alone is unreliable |
| MM-NIAH | Many-image retrieval | <10% beyond 5 images (older models) | N/A | N/A | Keep image sets small per call |
| Claude API | Up to 600 images/request | 638 tokens per 800x600 photo | ~33k tokens / property | API | One call per property is cheap enough to test |

---

## 8. One call vs pipeline for structured output

- **Evidence for decomposition**: a multi-stage, source-tracked extraction pipeline reached F1 ~0.96 vs ~0.87 single-pass (materials literature, arXiv 2510.05142) ([arXiv](https://arxiv.org/abs/2510.05142)); a PACIS 2026 paper found single-prompt GPT-4o lost mostly on *recall* (missed attributes) against a hybrid pipeline ([AIS eLibrary](https://aisel.aisnet.org/pacis2026/ai_ml/ai_ml/21)). These are text extraction, not vision.
- **Long context**: "Lost in the Middle" (TACL; arXiv 2307.03172) shows a U-shaped accuracy curve over position ([arXiv](https://arxiv.org/abs/2307.03172)); multimodal versions (MM-NIAH, MIMIC) above show worse effects with images.
- **Building/construction VLM evaluations**: GPT-4V for construction progress monitoring identified stages and materials but struggled with localization (arXiv 2412.16108) ([arXiv](https://arxiv.org/abs/2412.16108)); GPT-4V construction-component descriptions vs engineer descriptions (AI in Civil Engineering 2025) ([Springer](https://link.springer.com/article/10.1007/s43503-025-00063-9)); ENIAC 2025 property-feature study (topic 3) is per-category presence, macro-F1 0.77. **I found no paper evaluating GPT/Claude/Gemini on counting building components (doors, outlets, fixtures) against an engineer's inventory, and no insurance-estimate-from-photos paper with GPT-4V numbers.**

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| Multi-stage extraction | Higher recall | F1 0.96 vs 0.87 (text) | More calls | N/A | Pipelines help recall of many-field schemas |
| Lost in the Middle / MM-NIAH | Position/length effects | U-curve; <10% many-image | N/A | N/A | One giant call risks dropped rows |
| Construction GPT-4V papers | Qualitative material/stage ID | Weak localization | API | API | No direct benchmark for our task |

Implication: the evidence leans toward "one call misses rows" (recall), which is exactly the failure the engineer would have to fix by adding rows. A one-call design needs a recall check against the 46-row schema.

---

## 9. Reasoning effort / thinking for perception

- **"More Thinking, Less Seeing?"** (NeurIPS 2025): reasoning models with long chains hallucinate more on perception tasks than their non-reasoning Qwen2.5-VL backbones; attention shifts away from the image as chains lengthen; larger models balance better ([arXiv 2505.21523](https://arxiv.org/abs/2505.21523)).
- **SPARC** (ICML 2026): unstructured visual reasoning entangles perception and reasoning; small perception errors cascade ([arXiv 2602.06566](https://arxiv.org/abs/2602.06566)).
- **For more test-time compute**: PerceptionComp (video) finds more thinking tokens improve accuracy ([arXiv 2603.26653](https://arxiv.org/html/2603.26653v1)); OpenAI o3/o4-mini "think with images" via crop/zoom/rotate tools ([OpenAI](https://openai.com/index/thinking-with-images/)); I found no OpenAI-published V* number.
- **No source found that measures counting accuracy vs thinking budget for frontier models.**
- **Current controls**: Anthropic `output_config.effort` = low / medium / high / xhigh / max; Opus 5.5 defaults to medium, adaptive thinking always on, docs say run an effort sweep on your own evals and that `max` can overthink on structured-output tasks (stated for Opus 4.7) ([Anthropic effort docs](https://platform.claude.com/docs/en/build-with-claude/effort)). OpenAI `reasoning.effort` = none, minimal, low, medium, high, xhigh (and max on newer models), support varies by model ([OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)).

Implication: our current choice (low for GPT, medium for Opus) is consistent with the evidence that more thinking does not reliably help perception, but it is only measured on our photos. A per-class effort sweep is cheap.

---

## 10. Counting with VLMs and detectors (additions to what we already cover)

- **SAM 3 counting**: CountBench 93.8% / PixMo-Count 86.2% (topic 5).
- **CountQA** (Google/Waymo, arXiv 2508.06585): 1,500+ real-world cluttered images; best of 15 MLLMs 42.9%, falling as counts rise ([arXiv](https://arxiv.org/abs/2508.06585)).
- **HoloCount** (2026): 20+ MLLMs degrade from perception to analytical counting ([alphaXiv 2607.06420](https://www.alphaxiv.org/abs/2607.06420)).
- **Molmo2** (Ai2, Dec 2025; arXiv 2601.10611): claims counting strength on PixMo-Count and best Point-Bench (84.8 for 8B); a third-party board lists Molmo2-7B at 83.3 on PixMo-Count ([arXiv](https://arxiv.org/abs/2601.10611), [wizwand](https://www.wizwand.com/sota/visual-counting-on-pixmo)). We rejected Molmo-7B pointing; Molmo2 is the newer generation, unverified on our data.
- **CountGD** (NeurIPS 2024): text + exemplar counting ([arXiv 2407.04619](https://arxiv.org/abs/2407.04619)); T-Rex2 FSC-147 MAE 10.94 (secondary).
- **Gemini 3 Pro**: pointing advertised; no CountBench/PixMo number found ([Roboflow](https://blog.roboflow.com/gemini-3-pro/)).

Relevance: our counts are small (0-20 per photo), the regime where CountBench-style accuracy is high (~92-94%) for both SAM 3 and frontier VLMs. The real error is probably not per-photo counting but **which photos show the same object** (topic 7) and **what the engineer counts** (convention, e.g. is a double window one or two). Those are not addressed by better counters.

| System | What it gives | Accuracy evidence | Cost | Replicate / local | Implication for us |
|---|---|---|---|---|---|
| SAM 3 | Counts via masks | CountBench 93.8% | Replicate | Replicate / HF | Already strongest |
| Gemini 2.5 Pro | Counts via text | CountBench 92.4%, PixMo 78.2% | API | API | Comparable on small counts |
| CountQA | Cluttered-scene counts | Best MLLM 42.9% | N/A | N/A | Dense clutter is hard; our scenes mostly are not |
| Molmo2-8B | Points + counts | Point-Bench 84.8 | Local GPU | HF | Possible but we rejected Molmo-7B |

---

## Candidate architectures

Baseline for every experiment: the current pipeline on the same 52 photos (per-photo Opus 5.5 `photo_countables/v3` + `photo_surfaces/v2`, Sonnet 5.5 room step of section 16.5: prediction directory `rooms19_v3_opus55`; per-photo counts: the per-class fusion 4c of section 15). Scored by the harness (`photo_counts`, `photo_components`, `room_inventory`, `report_rows`) and by the new `evals.score_schedule` (row recall and precision against the engineer's schedule with distractor rows, row quantities against the labelled inventory, room count error over every repeat). One change per experiment.

### A. Trained ADE20K panoptic segmentation as counter and surface mapper
**Pipeline:** per photo, OneFormer DiNAT-L or Swin-L (or Mask2Former Swin-L) panoptic -> instances for doors, windowpanes, cabinets, sinks, toilets, bathtubs, stoves, ovens, microwaves, dishwashers, refrigerators, washers, hoods, fans, lamps, sconces, chandeliers, lights, blinds, curtains, railings, fireplaces -> counts; floor/wall/ceiling stuff masks passed to C. VLM/SAM 3 kept only for classes ADE20K lacks (outlets, switches, smoke detectors, roof, gutters, driveway, garage door).
**Hypothesis:** a closed-set model trained on dense indoor labels gives more consistent per-photo counts and much better surface masks than prompting, and is deterministic (same input, same output), which aids defensibility.
**Evidence:** 49.8-53.4 PQ and 57-58.4 mIoU on ADE20K (OneFormer repo); open-vocab segmentation is ~25 mIoU worse (SAN/CAT-Seg). No evidence on small fixtures.
**Risk:** "door" vs cabinet door confusion; "windowpane" counts panes, not window units (engineer counts units); small fixtures (sconce, outlet-sized) likely weak; panoptic "things" vs "stuff" split for some classes may put windowpane as stuff (check the panoptic category list).
**Cost:** local, free; ~52 forward passes. Replicate Mask2Former ~$0.019/run if needed.
**Experiment:** run `shi-labs/oneformer_ade20k_swin_large` (panoptic) on all 52 photos locally; map classes to schedule rows; compare per-row count error and per-photo instance precision (manually check 10 photos) against current VLM counts. Also measure mask IoU for floor/wall on 10 hand-labelled photos.
**Measured (section 26.2):** Mask2Former Swin-L and OneFormer Swin-L, local, phrases and thresholds chosen per class leave-one-photo-out as for SAM 3. mAP@0.5 on the 191 verified boxes 0.22 and 0.19 (SAM 3 0.76); per-photo count MAE 0.54 and 0.53 against the current best 0.124. They never fire on outlets or exterior lights (not ADE20K classes, or not seen as "light" outdoors), find under a third of the windows (exterior windows are absorbed into "building"/"house"), and merge cabinet runs into one segment. Their best class, light fixtures (OneFormer 0.15 MAE), does not beat the VLM (0.12). Their surface masks were useful as crop regions for C.

### B. Open-vocabulary detector in place of (or beside) SAM 3
**Pipeline:** LLMDet-L (local, transformers) and, if pricing allows, DINO-X Pro API, prompted with our class list; boxes -> counts; SAM 3 still draws masks for display.
**Hypothesis:** for specific classes (outlets, switches, smoke detectors, exterior light fixtures), a detector trained on LVIS/O365/V3Det vocabularies may beat SAM 3.
**Evidence:** weak. SAM 3 leads on SA-Co/Gold (54.1 vs OWLv2* 24.6, LLMDet-L 6.5, DINO-X 21.3) and on CountBench. DINO-X has the best LVIS rare AP among detectors.
**Risk:** likely no gain on average; threshold tuning without training data is overfitting to 52 photos.
**Cost:** LLMDet local free; DINO-X price unknown.
**Experiment:** per class, compare SAM 3 vs LLMDet-L counts on the 52 photos at a fixed threshold chosen on 10 photos and tested on the other 42. Keep only classes where the detector wins on the held-out 42.
**Measured (section 26.2):** OWLv2-L, Grounding DINO-B and LLMDet-L, local, prompted with SAM 3's phrase pool, one phrase per pass, selected per class leave-one-photo-out exactly as SAM 3 was. mAP 0.66 (OWLv2) and 0.44 (Grounding DINO) against SAM 3's 0.76 (LLMDet: see section 26.2); count MAE 0.41 and 0.45 against SAM 3's 0.26 and the VLM's 0.124. No detector wins a class; OWLv2 ties the VLM on light fixtures. MM-Grounding-DINO-L was not run (LLMDet is its stronger successor on the same architecture; LLMDet-L takes three minutes per photo on the CPU and hangs on MPS). The SAM 3 paper's own ranking holds on our photos.

### C. Segmentation-guided surface crops with a material query (or trained material model)
**Pipeline:** ADE20K floor/wall/ceiling/countertop/cabinet masks (from A) -> crop the largest connected region per surface per photo (with padding) -> ask the VLM a closed-choice question ("carpet / ceramic tile / hardwood / laminate-LVP / vinyl sheet / concrete") on the crop plus the full photo; optionally add Apple DMS46 per-pixel material votes inside the same mask as a second opinion.
**Hypothesis:** the material question currently competes with everything else in the photo; a crop of a known surface plus a closed label set reduces confusion (e.g. a rug on hardwood, wood-look tile).
**Evidence:** ViCrop raises small-model accuracy on small details (V* 42.4 -> 62.3 for LLaVA-1.5) without hurting general tasks; DMS46 is a released dense material model (0.420 mIoU). No frontier-model material benchmark exists.
**Risk:** crop loses context (wood-look tile needs grout lines and room context); DMS license may forbid commercial use; DMS sample code needs a GPU.
**Cost:** one extra VLM call per surface per room (~150 crops per property at ~638 tokens or less each); DMS local.
**Experiment:** for every photo with a visible floor, label the true floor material by hand (engineer schedule plus photo); compare full-photo question vs crop+photo question vs DMS46 majority vote; report accuracy per material and the confusion matrix.
**Measured (section 26.3):** Mask2Former wall/floor/ceiling masks, crop enlarged with the rest greyed, closed-choice prompt `surface_crop_material/v1`, Sonnet 5.5 low and gpt-6.1-sol low, three repeats, scored against the materials of the per-photo labels (58 labelled photo surfaces). Alone, the crops are better on walls (0.78 to 0.83 against 0.58) and ceilings (0.64 against 0.18; the per-photo call rarely lists a ceiling) and worse on floors (0.66 to 0.69 against 0.78), +0.05 overall, not significant. The floor loss is coverage, not reading: 10 of the 11 missed floors were never asked (the segmenter's floor region was under 2% of the photo, or labelled "rug"), and one was answered wrongly. Crops where a crop exists, the per-photo surfaces call elsewhere: hit rate 0.79 against 0.64, +0.155 [+0.034, +0.276] paired over the 58 surfaces, with either model. Report rows unchanged: both runs already evidence every material-dependent row on this study. DMS46 was not run: TorchScript weights, a GPU-only sample and an unread model licence next to CC-BY-NC labels.

### D. Whole-room multi-image prompting instead of per-photo + reconcile
**Pipeline:** group photos into rooms (keep current grouping, or Expedia-style embedding overlap + clustering); send all photos of one room (typically 2-6) in one call with "Image k:" labels; ask for the room's unique object counts and materials, with per-object references to which images show it.
**Hypothesis:** the model sees overlapping views together and can avoid double counting (grill problem) better than a separate reconcile step that only sees per-photo lists.
**Evidence:** mixed to negative. Cross-view counting is a known weak spot (VSI-Bench count 46-56; CrossView; MIMIC); MM-NIAH degrades beyond 5 images. But small groups (2-6) are in the regime where degradation is smallest, and the reconcile step currently has no pixels at all.
**Risk:** the model double counts anyway or under-counts by assuming overlap; harder to attribute errors.
**Cost:** similar token cost to per-photo (same images, fewer calls).
**Experiment:** on rooms with 2 or more photos, compare room-level counts from (current per-photo + reconcile) vs (one multi-image call) against the engineer's counts; separately score how often each method double counts an object visible in two photos (hand-labelled correspondences on ~10 rooms).
**Measured (section 26.4):** prompt `room_photos_inventory/v2` (the counting rules of `room_inventory/v3` with the current label conventions), one call per room with all its photos, no per-photo step. Opus 5.5 medium: room count MAE over three repeats 0.269 against 0.350 for the current pipeline (paired over 33 rooms -0.068, 95% CI [-0.115, -0.017]); row quantities against the labelled inventory WAPE 0.086 against 0.175; $1.18 per study against about $2.65. It misses Retaining Walls and Sidewalks in the 13-photo exterior group (36 or 37 of 38 rows). gpt-6.1-sol low: no better than the current pipeline (0.341). The first prompt version (v1) inherited `room_inventory/v3`'s outdated rule that files furniture and bath accessories as `other` and lost two rows; kept as a recorded loss. This is the one architecture change that measurably moves closer to the engineer's quantities.

### E. One call: photos to schedule
**Pipeline:** all 52 photos (~33k image tokens on Claude) plus the 46-row schedule template and rules in one request; the model returns the filled schedule with per-row evidence (image numbers).
**Hypothesis:** the model uses whole-house context (e.g. same flooring continues between rooms; one roof seen from four sides) and the schedule's own structure.
**Evidence:** against: MM-NIAH (<10% beyond 5 images, older models), MuirBench/MMIU/MMSI gaps, single-prompt recall deficits in extraction studies. For: none specific to our task found.
**Risk:** missed rows, lower counts, unreproducible outputs; hardest to debug or defend.
**Cost:** one call; with Opus 5.5 at the documented $4 per million input tokens, ~33k image tokens is roughly $0.13 of input per property plus output (pricing from [Anthropic vision docs](https://platform.claude.com/docs/en/build-with-claude/vision) example; check current pricing).
**Experiment:** run E 3 times per model on the 52 photos; score rows and counts against the engineer; also measure run-to-run variance. Use it as a ceiling/floor reference and as a *row-presence recall check* against A-D (rows E finds that the pipeline misses).
**Measured (section 26.5):** both the property-wide inventory call (`house_inventory/v2`) and the photos-to-schedule call (`house_schedule/v2`, with a template of the engineer's 46 row names plus 20 distractor rows) were run three times on Opus 5.5 and gpt-6.1-sol. The whole-house inventory is worse than per-room calls (Opus room count MAE 0.335, quantity WAPE 0.137) and the schedule call is far worse on quantities (cost-weighted quantity error 0.42 to 0.50 against 0.064), lists two or three rows photos cannot establish (Foundations-type rows), and misses three to four photo-derivable rows. Its recovery-period choice agrees with the engineer on 75 to 80% of the rows it lists. As predicted by MM-NIAH and the extraction studies: recall and counting fall when everything is asked at once.

### Suggested order (written before the experiments)
C (materials, the most direct gap with a clear mechanism) and A (local, cheap, deterministic) first; D next; B only for the handful of classes SAM 3 does poorly on; E as a reference run. Each experiment should split the 52 photos into a tuning set and a held-out set to avoid fitting thresholds or prompts to the very photos we score on.

---

## References

Industry and cost segregation
- Hover Interiors: https://hover.to/interiors/ ; Hover for insurance: https://help.hover.to/en/articles/12641171-hover-for-insurance
- CAPE / Moody's: https://techcrunch.com/2025/01/13/moodys-agrees-to-acquire-cape-analytics-which-develops-geospatial-ai-for-insurance-providers ; https://www.cbinsights.com/investor/cape-analytics
- Verisk XactAI: https://www.xactware.com/xactai.html ; https://www.verisk.com/blog/xactai-built-from-your-insights-delivered-through-your-workflows/ ; XactGen: https://www.xactware.com/xactgen.html
- Tractable + Verisk: https://www.verisk.com/newsroom/tractable-teams-up-with-verisk-to-offer-ai-powered-estimates-for-property-damage/ ; https://tractable.ai/en/industries/property-claims
- EagleView: https://www.eagleview.com/products/eagleview-assess/ ; https://www.globenewswire.com/fr/news-release/2025/09/10/3147793/0/en/eagleview-assess-expands-drone-roof-inspection-solution-to-include-large-and-complex-roofs-ai-driven-models-underpin-offering.html ; https://www.geoweeknews.com/articles/eagleview-launches-new-property-data-ecosystem/
- Hosta: https://www.businesswire.com/news/home/20220518005489/en/Hosta-A.I.-Integrates-With-CoreLogic-to-Provide-Automated-Property-Claims-Assessments-and-Estimates-for-the-Insurance-Industry ; https://www.guidewire.com/ja/resources/blog/industry-trends/empower-policyholders-to-process-claims-when-resources-are-stretched ; https://www.elaraspatial.ai/
- Plnar: https://plnar.ai/wp-content/uploads/2021/06/PlnarforEveryClaim.pdf ; https://coverager.com/plnar-unveils-first-measure-ready-photo-technology/
- DocuSketch: https://docusketch.com/?p=3903
- Restb.ai FAQ: https://restb.ai/company/faq/
- Zillow: https://nationalmortgageprofessional.com/news/76404/zillow-launches-next-home-tours ; https://s25.q4cdn.com/975009107/files/doc_news/Zillow-brings-AI-powered-Virtual-Staging-to-Showcase-listings-2025.pdf
- ZInD (CVPR 2021): https://openaccess.thecvf.com/content/CVPR2021/html/Cruz_Zillow_Indoor_Dataset_Annotated_Floor_Plans_With_360deg_Panoramas_and_CVPR_2021_paper.html
- Matterport: https://matterport.com/cortex-ai ; https://www.rismedia.com/2024/02/20/matterport-launches-property-intelligence/
- Segtax: https://richmondbizsense.com/2025/06/09/scotts-addition-startup-segtax-aims-to-automate-real-estate-depreciation-analysis/
- WCG / RentalWriteOff: https://wcginc.com/blog/cost-segregation-mixing-high-tech-with-sticks-and-bricks/
- Overline: https://overlineiq.com/blog/best-cost-segregation-companies-compared
- Segmenti: http://segmenti.io/ ; Findcostseg: https://www.startuphub.ai/startups/findcostseg.md ; https://aicostdepreciation.com/
- Accounting Today: https://www.accountingtoday.com/opinion/ai-slop-is-flooding-the-cost-segregation-industry ; https://www.accountingtoday.com/opinion/cost-segregation-in-the-age-of-ai-what-the-irs-audit-technique-guidelines-reveal

Listing research
- Airbnb amenity detection summaries: https://analyticsindiamag.com/how-online-companies-are-tapping-into-object-detection-to-improve-user-experience/ ; https://www.mrdbourke.com/airbnb-amenity-detection/
- Expedia room grouping, arXiv 2507.00263: https://arxiv.org/abs/2507.00263
- VLMs for tax-assessment features, ENIAC 2025: https://sol.sbc.org.br/index.php/eniac/article/view/38759
- MIT thesis: https://dspace.mit.edu/handle/1721.1/158862

Segmentation
- ADE20K class list: https://raw.githubusercontent.com/CSAILVision/sceneparsing/master/objectInfo150.csv
- Mask2Former, arXiv 2112.01527 (CVPR 2022): https://arxiv.org/abs/2112.01527 ; HF: https://huggingface.co/facebook/mask2former-swin-large-ade-panoptic ; PQ listing: https://sotaverified.org/papers/masked-attention-mask-transformer-for
- OneFormer, arXiv 2211.06220 (CVPR 2023): https://github.com/SHI-Labs/OneFormer ; HF: https://huggingface.co/shi-labs/oneformer_ade20k_swin_large
- SegFormer, arXiv 2105.15203 (NeurIPS 2021): https://arxiv.org/abs/2105.15203 ; HF: https://huggingface.co/nvidia/segformer-b5-finetuned-ade-640-640
- EoMT, arXiv 2503.19108 (CVPR 2025): https://github.com/tue-mps/eomt ; https://huggingface.co/docs/transformers/en/model_doc/eomt
- DINOv3, arXiv 2508.10104: https://arxiv.org/abs/2508.10104
- SAN, arXiv 2302.12242 (CVPR 2023): https://arxiv.org/abs/2302.12242 ; CAT-Seg (CVPR 2024): https://openaccess.thecvf.com/content/CVPR2024/html/Cho_CAT-Seg_Cost_Aggregation_for_Open-Vocabulary_Semantic_Segmentation_CVPR_2024_paper.html ; SED supplement: https://openaccess.thecvf.com/content/CVPR2024/supplemental/Xie_SED_A_Simple_CVPR_2024_supplemental.pdf
- Replicate: https://replicate.com/hassamdevsy/mask2former ; https://replicate.com/meta/mask2former ; https://replicate.com/cjwbw/oneformer

Detection and counting
- SAM 3, arXiv 2511.16719: https://arxiv.org/html/2511.16719v1
- Grounding DINO, arXiv 2303.05499 (ECCV 2024): https://arxiv.org/abs/2303.05499
- MM-Grounding-DINO / LLMDet in transformers: https://huggingface.co/docs/transformers/model_doc/mm-grounding-dino ; LLMDet arXiv 2501.18954: https://arxiv.org/abs/2501.18954
- OWLv2, arXiv 2306.09683: https://arxiv.org/abs/2306.09683
- YOLOE, arXiv 2503.07465: https://docs.ultralytics.com/models/yoloe ; YOLO-World Replicate: https://replicate.com/zsxkib/yolo-world
- Florence-2: https://huggingface.co/GuiAworld/Florence-2-large ; https://replicate.com/lucataco/florence-2-large/readme
- DINO-X, arXiv 2411.14347: https://arxiv.org/abs/2411.14347 ; API: https://github.com/idea-research/dino-x-api
- T-Rex2, arXiv 2403.14610: https://arxiv.org/abs/2403.14610 ; T-Rex-Omni, arXiv 2511.08997: https://arxiv.org/abs/2511.08997
- Rex-Omni, arXiv 2510.12798: https://openaccess.thecvf.com/content/CVPR2026/html/Jiang_Detect_Anything_via_Next_Point_Prediction_CVPR_2026_paper.html
- CountGD, arXiv 2407.04619: https://arxiv.org/abs/2407.04619
- CountQA, arXiv 2508.06585: https://arxiv.org/abs/2508.06585 ; HoloCount: https://www.alphaxiv.org/abs/2607.06420
- Molmo2, arXiv 2601.10611: https://arxiv.org/abs/2601.10611 ; https://www.wizwand.com/sota/visual-counting-on-pixmo
- Gemini 3 Pro (Roboflow): https://blog.roboflow.com/gemini-3-pro/

Materials and cropping
- MINC, arXiv 1412.0623 (CVPR 2015): https://openaccess.thecvf.com/content_cvpr_2015/html/Bell_Material_Recognition_in_2015_CVPR_paper.html
- DMS (ECCV 2022): https://www.ecva.net/papers/eccv_2022/papers_ECCV/html/6243_ECCV_2022_paper.php ; https://github.com/apple/ml-dms-dataset ; https://machinelearning.apple.com/research/dense-material
- DMS revisited, arXiv 2605.23747: https://arxiv.org/abs/2605.23747
- CLIP texture, arXiv 2203.11449: https://arxiv.org/abs/2203.11449
- MatCha (EMNLP Findings 2025): https://aclanthology.org/2025.findings-emnlp.235/
- MLLMs Know Where to Look, arXiv 2502.17422 (ICLR 2025): https://arxiv.org/html/2502.17422v1
- SPARC, arXiv 2602.06566: https://arxiv.org/abs/2602.06566

Multi-image, long context, reasoning
- MuirBench, arXiv 2406.09411: https://arxiv.org/abs/2406.09411
- MMIU, arXiv 2408.02718: https://arxiv.org/abs/2408.02718
- MMSI-Bench, arXiv 2505.23764: https://arxiv.org/abs/2505.23764
- VSI-Bench, arXiv 2412.14171: https://arxiv.org/abs/2412.14171 ; re-evaluations: https://arxiv.org/abs/2506.18385 ; https://arxiv.org/abs/2603.06704
- MIMIC, arXiv 2601.07812: https://arxiv.org/abs/2601.07812
- MM-NIAH, arXiv 2406.07230: https://arxiv.org/abs/2406.07230 ; MMDU, arXiv 2406.11833: https://arxiv.org/abs/2406.11833
- CrossView, arXiv 2608.15539: https://arxiv.org/abs/2608.15539 ; SpatialMosaic, arXiv 2512.23365: https://arxiv.org/abs/2512.23365
- Lost in the Middle, arXiv 2307.03172: https://arxiv.org/abs/2307.03172
- Multi-stage extraction, arXiv 2510.05142: https://arxiv.org/abs/2510.05142 ; PACIS 2026: https://aisel.aisnet.org/pacis2026/ai_ml/ai_ml/21
- GPT-4V construction monitoring, arXiv 2412.16108: https://arxiv.org/abs/2412.16108 ; GPT-4V construction components: https://link.springer.com/article/10.1007/s43503-025-00063-9
- More Thinking, Less Seeing?, arXiv 2505.21523 (NeurIPS 2025): https://arxiv.org/abs/2505.21523
- PerceptionComp, arXiv 2603.26653: https://arxiv.org/html/2603.26653v1
- OpenAI thinking with images: https://openai.com/index/thinking-with-images/
- Anthropic effort: https://platform.claude.com/docs/en/build-with-claude/effort ; vision: https://platform.claude.com/docs/en/build-with-claude/vision
- OpenAI reasoning guide: https://developers.openai.com/api/docs/guides/reasoning
