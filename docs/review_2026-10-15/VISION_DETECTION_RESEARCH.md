# Finding and highlighting building components: detection and segmentation research (2026-10-15)

Companion to VISION_RESEARCH.md (multi-image reconciliation) and EVAL_REPORT.md sections 6 to 8. Claims marked unverified or (snippet) were not confirmed from the source; cost lines are arithmetic on list prices.

# Design note: finding and highlighting building components in property photos (as of 2026-10-15; revised with sections 5 and 6)

Nothing was written to any repo. "(snippet)" means the claim came from a search-result summary; I did not open that page.

## Recommendation

1. **Keep the current VLM call as the label proposer**, but move localization and counting to **SAM 3**, Meta's open-weights model that segments every instance of a text concept.
   - On LVIS it scores 53.6 box AP and 48.5 mask AP zero-shot. Grounding DINO-T scores 20.5 box AP [3].
   - On CountBench its MAE is 0.12; Gemini 2.5 Pro's is 0.24 [3].
   - Each instance comes back as a mask, which is the glow highlight the product wants.
   - The same model takes point and box prompts, so the engineer's tap-to-add needs no second model [1][2].
2. **Prompt the countable classes on every photo**, whatever the VLM says: window, light fixture, outlet, exterior light. Use 2×2 tiling for those classes.
3. **Before labelling anything of our own**, measure the candidates on public data that has our classes:
   - Open Images V7 boxes for Window, Door, Light switch, "Power plugs and sockets", Ceiling fan, Lamp, Toilet, Sink [8].
   - ADE20K masks for windowpane, door, cabinet, lamp, light, sconce, chandelier, sink, toilet, countertop [6].

### Ranked shortlist to measure

- **Step 0 (no labelling, about one day).** Run P1–P3 below on about 300 Open Images validation images and about 300 ADE20K validation images. Pick only images containing our overlapping classes. Report AP@0.5 per class and count MAE.
  - Open Images: annotations are CC BY 4.0, images are listed as CC BY 2.0, so it is commercial-safe [8].
  - ADE20K: images are non-commercial research only, and the terms bind a for-profit employer [7]. Get legal sign-off before using it, even for internal evaluation.
- **P1: VLM labels + SAM 3 text prompts, with tiling for small classes.** Expected best recall and real masks. Self-host in GCP for production; fal costs $0.005 per request for trials [16].
- **P2: Gemini Flash native boxes and masks, optionally tiled, then SAM 2 refines boxes into masks.** One vendor, simplest to run. Weaker zero-shot detection: Gemini 2.5 Pro scores 33.7 ODinW13 AP0; SAM 3 scores 61.0 [3].
- **P3: Grounded SAM 2 (Grounding DINO 1.0 + SAM 2).** The licence-clean baseline: SAM 2 is Apache-2.0 [19], and the repo is Apache plus its component licences [5]. It is also the fine-tuning path if zero-shot falls short.

Only after step 0 should we label boxes on our own 20-photo subset (see the Evaluation section).

## 1. Model landscape

| Model | Output | Zero-shot evidence | Compute | Licence | Hosted |
|---|---|---|---|---|---|
| **SAM 3**: released 2025-11-19 [14]; SAM 3.1 checkpoints 2026-03-27 [2] | All-instance masks and boxes from a noun phrase or exemplar; also takes point, box and mask prompts [1][2] | LVIS 53.6 box AP / 48.5 mask AP (paper text says 48.8 mask AP). ODinW13 AP0 61.0. RF100-VL AP0 15.2, AP10 36.5. CountBench MAE 0.12 [3] | 848M parameters [2]. 30 ms per image with 100+ objects on an H200 [1]. Official repo needs CUDA [2]. CPU speed unverified. | Custom "SAM License": royalty-free, no commercial carve-out, trade-control and military restrictions [15]. **Needs legal review.** | fal $0.005 per request [16]; Roboflow serverless (price not found) [14]; Hugging Face weights [1] |
| **SAM 2.1** | Masks from points or boxes; no text prompts [19] | n/a | 39–224M parameters; 39–91 FPS on an A100 [19] | Apache-2.0 [19] | Replicate (per-second GPU billing) [17] |
| **Grounding DINO 1.0** | Boxes from text | LVIS 20.5 (Tiny); ODinW13 49.7; RF100-VL 15.7 [3] | GPU | Apache-2.0 open weights [5] | Self-host |
| **Grounding DINO 1.5 / 1.6 Pro** | Boxes | 1.6 Pro: LVIS-minival 57.7, LVIS-val 51.1. 1.5 Pro: ODinW13 58.7 [4] | Cloud only | Weights not released; DeepDataSpace API, price unpublished [4][5] | DeepDataSpace |
| **OWLv2** | Boxes | LVIS 35.2 box AP [3]. Users preferred SAM 3 about 3 to 1 [1]. | GPU | Apache-2.0; card says "research output" [9] | Replicate forks |
| **Florence-2-large** | Boxes, phrase grounding [10] | COCO detection 37.5 mAP zero-shot [10] | 0.77B parameters | MIT [10] | Self-host |
| **YOLOE** | Boxes and masks; text, visual or prompt-free (4,585 built-in classes) [11] | YOLOE-26 LVIS-minival 24.7 (n) to 40.6 (x); YOLO-Worldv2-L 35.5 [11] | Real-time; runs on CPU too [11] | Not stated on the page; Ultralytics is normally AGPL (**unverified**) | Self-host |
| **Gemini (2.5 → current Flash)** | `box_2d` on a 0–1000 scale plus masks. Current docs describe a polygon inside the box; docs say to set thinking to minimal [12]. Masks launched 2025-07-21 [20]. | ODinW13 AP0 33.7; RF100-VL 11.6 [3] | API | Paid tier is not used for training [18] | 3.8 Flash: $0.75 in / $3.75 out per 1M tokens [18] |
| **GPT-5** | Prompted boxes | RF100-VL 1.5 mAP50:95; counted correctly in 4 of 10 images [21] | API | Proprietary | OpenAI |
| **Frontier VLMs (Oct 2026)** | Prompted boxes | Roboflow's own task set, mAP@50: Gemini 4 Argon 82.5%, GPT-6.1 Sol 80.8%, Qwen3.8 Max 76.7%. Not comparable to LVIS AP. [22] | API | Proprietary | $0.004–0.034 per sample [22] |
| **Qwen2.5-VL / Qwen3-VL** | Boxes and points. Qwen2.5-VL uses absolute pixels [23]; Qwen3-VL uses relative coordinates [24]. | Benchmarks only in images | 2B to 235B [24] | Apache-2.0 [24] | Self-host |
| **Molmo** | Points | Molmo-72B PixMo-Count MAE 0.17, best in that table [3] | 8B [25] | Apache-2.0 [25] | Self-host |
| **Moondream 3** | Boxes and points [26] | Reports RefCOCO and CountBenchQA [26] | 9B MoE (snippet) | BSL 1.1, no third-party service [26] | Moondream cloud |
| **PaliGemma 2** | Boxes; segmentation codewords [27] | RefCOCO val 76.3 [27] | 3B | Gemma terms, gated [27] | Self-host |

On RF100-VL (a deliberately unusual set of domains), every zero-shot model scores about 11–16 AP [3]. Our classes are everyday objects, but we should verify that on step 0 rather than assume it.

## 2. Pure LLM versus a mix

- **(a) VLM proposes, detector or SAM localizes, SAM masks.** The common pattern. Grounded SAM 2 is exactly this chain [5]. SAM 3 Agent (an MLLM driving SAM 3) reaches 77.0 gIoU on ReasonSeg val [3].
- **(b) Detector first, VLM classifies crops.** Use it for fine distinctions (vanity vs kitchen cabinet) that SAM 3 phrases cannot separate.
- **(c) VLM-native boxes only.** Improving fast [22], but in the SAM 3 paper's head-to-head it still trails SAM 3 [3].
- **(d) Tiling.**
  - Detectors: SAHI slicing alone adds +5.1 to +6.8 AP, and +12.7 to +14.5 AP with sliced fine-tuning [13].
  - VLMs: LVLM-Count splits the image around Grounding DINO + SAM masks and counts each part. GPT-4o FSC-147 MAE falls from 25.57 to 17.86, and from 89.22 to 57.87 when there are 100+ objects [28].
  - No Gemini-specific tiling study was found. Either way, merge across tiles with NMS.
- **(e) Counting.** CountBench MAE: SAM 3 0.12, Gemini 2.5 Pro 0.24, Molmo-72B 0.27 [3]. Our 30-windows-reported-as-7 failure matches the VLM collapse on many instances that LVLM-Count addresses [28]. CountGD (Grounding DINO plus exemplars) is the counting-specialist option [29].

## 3. How highlight UIs work

- **Matterport CortexAI:** each face detection carries a confidence score. Owners "confirm these detections individually or in bulk", remove false positives and add misses before publishing [36]. This is the closest published human-correction loop, though it covers faces, not fixtures.
- **Google Magic Eraser:** outlines suggested objects in white; the user taps to accept. Third-party reporting only (snippet).
- **Apple:** the published on-device model is salient-object segmentation (EfficientNetV2 encoder, 512×512, under 10 ms on the Neural Engine). It powers subject lifting [30]. Clean Up's model is unpublished.
- **Annotation tools are the UX template:** CVAT interactors take left-click positive and right-click negative points, then convert the mask to a polygon [31]. Roboflow Smart Polygon added SAM 3 point prompts in November 2025 (snippet).

## 4. Proposed pipeline

1. The existing VLM proposes labels and counts per photo.
2. Map each label to 1–3 noun phrases ("electrical_outlet" → "wall outlet", "electrical outlet"). Add the always-on countable set.
3. Run SAM 3 per phrase to get masks, boxes and scores. Run 2×2 tiles for the small classes and merge with mask NMS.
4. Per-photo count = number of instances above a threshold tuned on step 0. Labels the VLM named that SAM 3 never localized stay as "unlocalized" for review. Cross-photo de-duplication stays in the existing room reconciliation.
5. Review UI: glowing outlines; accept, delete or relabel; tap or drag to add (SAM point or box prompt).
6. Residency: fal and Replicate are for trials only. In production, self-host SAM 3 in GCP project `cost-seg-basis`. If we use Gemini, call it through Vertex AI (regional pinning **unverified**). Paid-tier Gemini data is not used for training [18].

### Cost per 52-photo property (arithmetic on list prices, not measured)

- **P1 on fal:** about 1,150 requests (6 prompts plus 16 tile calls per photo) × $0.005 ≈ $5.70. Untiled, about $2.60 [16].
- **P1 self-hosted:** at an assumed 0.3 s per call on an L40S, about $0.35 of GPU time [17].
- **P2:** at an assumed 1.5k input and 3–4k output tokens per photo, about $1. Tiled, about $5 [18].
- **P3:** cents of GPU time.

### Evaluation

- **Step 0, public data:**
  - AP@0.5 for boxes on Open Images.
  - Mask IoU and instance AP on ADE20K, after legal clearance.
- **Our labels:**
  - Label F1 against today's 0.67.
  - Per-room count MAE against the existing room counts.
- **New labelling:** boxes for 4 countable classes (window, light fixture incl. exterior, outlet, cabinet) on 20 of our photos. Pre-label with SAM 3 and correct by hand. A few hours (estimate).
- **Masks:** only about 5 photos, if needed.
- **Product metric:** engineer-correction rate per accepted object. Deletes are false positives, adds are false negatives, relabels are confusions.

## 5. Public datasets

| Dataset | Size | Labels | Our classes covered | Licence / commercial use |
|---|---|---|---|---|
| **Open Images V7** | About 9M images; 16M boxes, 600 classes; 2.8M instance masks, 350 classes [8] | Boxes and masks | **Window, Door, Door handle, Light switch, "Power plugs and sockets", Ceiling fan, Lamp, Light bulb, Toilet, Sink, Tap, Bathtub, Shower, Cabinetry, Countertop, Fireplace, Stairs, Oven, Gas stove, Refrigerator, Dishwasher, Microwave oven, Washing machine, Curtain, Window blind, Porch, Swimming pool, House, Building.** Absent: Chandelier, Sconce, Roof, Fence [32]. | Annotations CC BY 4.0. Images listed as CC BY 2.0, but the publishers make no representation per image [8]. **Best candidate for both evaluation and fine-tuning.** |
| **ADE20K / SceneParsing** | 25,574 training + 2,000 validation images [6] | Object and part masks | windowpane, door, screen door, cabinet, wardrobe, lamp, light, chandelier, sconce, sink, toilet, bathtub, shower, countertop, kitchen island, stove, oven, microwave, dishwasher, refrigerator, hood, fireplace, radiator, fan, stairs, railing, bannister, awning, fence, blind, curtain [33] | Annotations BSD-3. **Images non-commercial research and education only**, binding a for-profit employer [7]. Evaluation only, after legal review; not for training. |
| **LVIS v1** | 159,623 images, 1,203 categories, more than 2M masks [34] | Masks | toilet, sink, faucet, cabinet, lamp, chandelier, lightbulb, **wall_socket**, thermostat, water_heater, radiator, air_conditioner, fireplace, shower_head, doorknob, appliances. **No "window", "door", "switch", "railing" or "fence" category** [35] | Licence not confirmed on the page I opened. COCO-family annotations are commonly CC BY 4.0, with images under Flickr terms (snippet). |
| **COCO** | 80 classes | Boxes and masks | toilet, sink, oven, microwave, refrigerator, tv, plus furniture. No window, door or fixtures (from memory, **unverified**). | Same as above: annotations CC BY 4.0, images per Flickr licence (snippet). |
| **Hypersim (synthetic)** | 74,619 images, 461 indoor scenes [37] | NYU40 semantic and instance masks, 3D boxes | NYU40 classes (window, door, cabinet, lamp, sink, toilet...) | CC BY-SA 3.0 [37]. Commercial use is plausible, but share-alike's effect on trained weights is unclear: legal question. |
| **SUN RGB-D** | 10,335 images; 146,617 2D polygons [38] | Polygons, 3D boxes | Indoor furniture and fixtures (class list not on the page) | Not stated on the page [38] |
| **ScanNet** | Indoor RGB-D scans | Instance masks | Indoor | **Non-commercial research only**, binding a for-profit employer [39] |
| **Matterport3D** | 90 buildings, 194,400 RGB-D images [40] | 2D and 3D semantic segmentation | Indoor, room-level | Signed terms of use; licence not stated on the page [40] |
| **HomeObjects-3K** | 2,689 images [41] | Boxes | window, door, lamp, wardrobe (12 classes) | AGPL-3.0; the page says commercial use with attribution [41] |
| **Roboflow Universe** | Per project: facade1 has 163 images (door, window, roof); facade instance-segmentation sets; outlet and "electrical equipments" sets; several CC BY 4.0 (all snippet) | Varies | Facade windows and doors, outlets, roofs | Licence set per project; pages returned 403 to me, so **unverified** |
| **Real-estate listing photos** | No public box- or mask-annotated set found | — | — | Listing photos are copyrighted; scraping is restricted (snippet) |

## 6. Companies that localize building components

| Company | What it detects | Stack evidence | Human-correctable highlights? | API we could call? |
|---|---|---|---|---|
| **Nearmap (owns Betterview)** | Aerial AI packs: roof characteristics, solar, pools, vegetation, **surfaces**, building footprints, damage [42]. Gen 6 has 130+ layers and a "new deep learning architecture" (snippet). | Geometry delivered as GeoParquet polygons per class [42] | Not documented | **Yes**, Feature API; `nmaipy` client [42]. Aerial only: useful for roof and paving land improvements, not interiors. |
| **CAPE Analytics (Moody's)** | Roof condition (5-point scale), roof age, solar, yard debris, vegetation, from aerial imagery plus ML (snippet) | CV on geospatial imagery (snippet) | No | API and batch (snippet) |
| **Arturo** | 150+ attributes from imagery: roof material and condition, change detection (snippet) | Proprietary models (snippet) | Unknown | Via partners (snippet) |
| **EagleView Assess** | Drone roof damage (hail, wind), facet anomaly counts, roof component counts, wireframe overlays (snippet) | "AI, ML and real-time 3D processing" (snippet). The page I opened has only the launch announcement [43]. | Wireframe overlays (snippet) | Not public |
| **Hover** | Exterior measurements from phone photos: windows and doors grouped by trim, siding by material [44] | 3D reconstruction from ground photos [44]. Detector details unpublished. | Users can draw custom line segments [44] | Developer portal at developers.hover.to (snippet). **The closest buy-instead option for exterior windows and siding quantities.** |
| **Zeitview** | Roof anomalies (hail, ponding, membrane) on drone orthomosaics; thermal [45] | AI/ML, then **in-house experts review** before the report [45] | Yes, expert-reviewed overlays | Service, not an open API |
| **Loveland IMGING** | Highlights hail and wind damage on roof images; Guidewire ClaimCenter add-on (snippet) | "Deep learning" (snippet) | Highlights (snippet) | Via integration |
| **DroneDeploy** | Roof Report: area, pitch, roof features, delivered as PDF/DXF; defect detection is a marketing claim (snippet) | Photogrammetry | Unknown | Platform API (unverified) |
| **Skydio** | Capture only; AI comes from partners (EagleView, Levatas, Qii.AI) (snippet) | — | — | — |
| **OpenSpace** | Progress tracking via segmentation and detection; Object Search by drawn box (snippet) | 360° capture plus CV (snippet) | Draw-box search | No public detection API found |
| **Doxel** | Counts installed elements and linear feet across trades; 80+ construction stages [46] | ML/CV against BIM; **QA team checks results** [46] | Internal QA | Integrations only [46] |
| **Buildots** | Installed elements against BIM from hard-hat 360° video (snippet) | CV plus BIM comparison (snippet) | Deviation flags | No |
| **Reconstruct / Cupix** | 360° video to digital twin, progress against BIM (snippet) | Reality capture plus AI (snippet) | — | No |
| **Matterport** | Room type and dimensions, plumbing/HVAC fixture locations, appliances, faces [36] | Trained on about 500M 3D scans [36] | **Yes, confirm/correct with confidence scores** [36] | SDK (unverified for detections) |
| **Hosta a.i.** | Measurements, materials, "classifying objects and materials" (snippet) | Photo-based 3D (snippet) | Unknown | API (snippet) |
| **Encircle** | Per-item AI descriptions (brand, model) from item photos (snippet) | Likely VLM captioning (**inference**) | Editable item fields | No |
| **Verisk + Tractable** | Damage identified, classified and measured from photos, into Xactimate estimates [47] | CV trained on claims data [47] | Unknown | Via Verisk/Tractable |
| **Restb.ai** | 700+ image-level tags: room type, features, condition [48] | Tags per image; no boxes documented [48] | No | **Yes, but tags only** [48] |

**Takeaway:** nobody sells an interior per-component localization API matching our 72 labels. Partial buy options are Hover (exterior openings and siding), Nearmap/CAPE (roofs, surfaces, pools from aerial imagery) and Restb.ai (image-level tags). The human-correction pattern in Matterport, Zeitview and Doxel is "AI proposes, a person confirms", which matches our design.

**Open items:**
- SAM 3 licence suitability.
- ADE20K use by a commercial company.
- Hypersim share-alike's effect on trained models.
- LVIS licence page.
- YOLOE licence.
- Roboflow Universe licences.
- Hover and Nearmap API pricing.
- All cost lines above, which are estimates.

## Sources (opened)

1. https://ai.meta.com/blog/segment-anything-model-3/
2. https://github.com/facebookresearch/sam3
3. https://arxiv.org/html/2511.16719v1
4. https://github.com/IDEA-Research/Grounding-DINO-1.5-API
5. https://github.com/IDEA-Research/Grounded-SAM-2
6. https://ade20k.csail.mit.edu/
7. https://ade20k.csail.mit.edu/terms/
8. https://storage.googleapis.com/openimages/web/factsfigures_v7.html
9. https://huggingface.co/google/owlv2-base-patch16-ensemble
10. https://huggingface.co/microsoft/Florence-2-large
11. https://docs.ultralytics.com/models/yoloe/
12. https://ai.google.dev/gemini-api/docs/image-understanding
13. https://arxiv.org/abs/2202.06934
14. https://playground.roboflow.com/models/compare/gemini-2-5-pro-vs-sam-3
15. https://github.com/facebookresearch/sam3/blob/main/LICENSE
16. https://fal.ai/models/fal-ai/sam-3/image
17. https://replicate.com/pricing
18. https://ai.google.dev/gemini-api/docs/pricing
19. https://github.com/facebookresearch/sam2
20. https://developers.googleblog.com/en/conversational-image-segmentation-gemini-2-5/
21. https://roboflow.com/blog/gpt-5-vision-multimodal-evaluation
22. https://playground.roboflow.com/evals
23. https://qwenlm.github.io/blog/qwen2.5-vl/
24. https://github.com/QwenLM/Qwen3-VL
25. https://huggingface.co/allenai/Molmo-7B-D-0924
26. https://docs.moondream.ai/
27. https://huggingface.co/google/paligemma2-3b-mix-448
28. https://arxiv.org/html/2412.00686v4
29. https://arxiv.org/abs/2407.04619
30. https://machinelearning.apple.com/research/salient-object-segmentation
31. https://docs.cvat.ai/docs/annotation/auto-annotation/ai-tools/
32. https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv
33. https://raw.githubusercontent.com/CSAILVision/sceneparsing/master/objectInfo150.csv
34. https://github.com/lvis-dataset/lvis-api
35. https://huggingface.co/spaces/HarborYuan/ovsam/raw/main/ext/class_names/lvis_list.py
36. https://matterport.com/blog/property-intelligence-principles-and-the-art-of-possible
37. https://github.com/apple/ml-hypersim
38. https://rgbd.cs.princeton.edu/
39. https://kaldir.vc.in.tum.de/scannet/ScanNet_TOS.pdf
40. https://niessner.github.io/Matterport/
41. https://docs.ultralytics.com/datasets/detect/homeobjects-3k/
42. https://pypi.org/project/nmaipy/5.1.7/
43. https://www.eagleview.com/products/eagleview-assess/
44. https://hover.to/measurements/windows-and-doors
45. https://insideunmannedsystems.com/zeitview-expands-rooftop-inspection-capacity-with-ai-enabled-software-platform
46. https://doxel.ai/faqs
47. https://tractable.ai/tractable-teams-up-with-verisk-to-offer-ai-powered-estimates-for-property-damage/
48. https://restb.ai/company/faq/

**Attempted but returned no usable content:** the Nearmap Gen 6 help page, lvisdataset.org, the cocodataset.org terms page, and Roboflow Universe project pages (403).

**Note on the leaderboard:** Roboflow's Vision Evals [22] lists Oct-2026 models such as "GPT-6.1 Sol" and "Claude Opus 5.5". The numbers are as published on 2026-10-13, on Roboflow's own task set.
