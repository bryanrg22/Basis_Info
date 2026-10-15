# Photo analysis research note (2026-10-15)

Research for the photo pipeline (multi-image room reconciliation, open-vocabulary boxes,
evaluation design), produced from the sources listed at the end; claims marked unverified
were not confirmed. Companion to EVAL_REPORT.md, which holds the measured numbers.

# Design note: reconciling objects across photos, open-vocabulary boxes, and evaluation

## Recommendation

The evidence supports your order (measure, then group by room, then one multi-image call per room, then boxes), with one change to step 3.

1. **Measure the current per-photo model first.** Nothing below can be judged without a baseline.
2. **Group photos by room** using file names plus the model's room type. This is cheap. Engineers should confirm the grouping at a review gate because every later step depends on it.
3. **Change: do not let the per-room call replace the per-photo calls.** Use it as a reconciliation pass over the per-photo outputs. It should return distinct objects, and each object should list the photo indices where it appears, so the engineer can check every merge against the photos. Every frontier vendor documents counting as approximate [3,5]. Multi-view benchmarks show models fail to match the same object across views, especially under occlusion [20], and hallucination rises with the number of images [21]. A single opaque count per room cannot be defended. A count tied to the photos it came from can.
4. **Boxes last.** Boxes are for the interface, not for deduplication. A purpose-built open-vocabulary detector produces better boxes than the vision-language models do [18].

Nothing I found argues for building 3D reconstruction or re-identification first.

## 1. Counting distinct objects across several photos of a room

**(a) One multi-image call per room.** All vendors support many images per request:

| Provider | Images per request | Constraint |
|---|---|---|
| Gemini | up to 3,600 [1] | – |
| OpenAI | up to 1,500 [3] | 512 MB payload [3] |
| Claude API | 100 on 200k-context models, 600 on others [5] | above 20 images, each image must be at most about 2000 px per side [5] |

Accuracy evidence is weak:
- All-Angles Bench (2,100+ questions over 90 scenes, 27 models including GPT-4o, Gemini-2.0-Flash and Claude-3.7) includes a counting-across-views task. The authors report models are "far from human-level," with specific weakness matching objects across partially occluded views [20].
- MIHBench measures counting and object-identity consistency across images. It finds hallucination grows as the number of input images grows [21].
- Vendor docs say counts are "approximate" (OpenAI [3]; Claude [5]).

Practical rules that follow:
- Keep each call to one room (around 5 to 10 photos).
- Label each image ("Image 1:") as the Claude docs recommend [5].
- Require per-object photo indices in the output.
- Run it several times to measure how much the output varies between runs.

**(b) Per-photo detection plus heuristic merging** (same label and same room, then merge). This is deterministic and easy to audit, but it cannot tell two identical objects apart from one object photographed twice. It over-merges repeated items (two sinks) and under-merges objects seen from different angles. It is a reasonable fallback, and it is the baseline the multi-image call has to beat.

**(c) Re-identification or 3D reconstruction.** VGGT predicts camera parameters, depth and point maps from one to hundreds of views in under a second [22]. In principle that allows deduplication by 3D position. Whether it handles sparse, wide-baseline engineer photos is unverified. It adds a GPU dependency and a whole research track, so defer it.

**Cost per photo** (about a 12 MP phone photo, computed from the vendors' own token rules):

| Model | Approx. cost per photo | Basis |
|---|---|---|
| Claude Opus 5.5, high-resolution tier | about $0.019 | capped at 4,784 tokens; docs give about $19 per 1,000 4K images [5] |
| Claude Haiku 4.5 | about $0.0016 | about 1,568 tokens at $1/M [5] |
| Gemini 2.5 Flash | about $0.0003 | 4 tiles × 258 tokens at $0.30/M [1,2] |
| Gemini 3.1 Pro | about $0.002 | $2/M [2] |
| gpt-4.1-mini | about $0.004 | up to 6,144 patches × 1.62 at $0.40/M [3,4] |
| gpt-5.6-luna | – | $0.20/M [4] |

- Batch processing halves the price at both OpenAI and Gemini [2,4].
- For 52 photos, every option costs well under $1 per run. Cost does not decide between the approaches.

## 2. Open-vocabulary detection with boxes

| Model | License / commercial use | CPU? | Hosted options | Zero-shot accuracy |
|---|---|---|---|---|
| Grounding DINO (original) | Apache-2.0; commercial OK [9] | Yes, CPU-only mode [9] | Replicate: about $0.001/run, about 1 s on L40S [15]; Roboflow dedicated GPU or self-hosted [16] | COCO zero-shot 48.4 AP (T model) [9]; RF100-VL 15.7 mAP [18] |
| Grounding DINO 1.5 / 1.6 Pro | Hosted API only, token from DeepDataSpace; no downloadable weights found; paid via their platform (WeChat Pay) [8] | n/a | IDEA/DeepDataSpace only | LVIS-minival 55.7 / 57.7; ODinW35 30.2 (1.5 Pro) [7,8]; 1.5 Edge runs at 75 FPS with TensorRT [7] |
| OWLv2 | Apache-2.0, but the model card calls it a research output [10] | Likely (unverified) | Roboflow dedicated or self-hosted [16] | LVIS rare AP 44.6 with self-training [11]; RF100-VL 13.6 [18] |
| Florence-2-large (0.77B) | MIT; commercial OK [12] | Plausible at this size (unverified) | Roboflow serverless [16] | COCO 37.5 to 39.8 [12]. Its fixed detection mode is not open-vocabulary; arbitrary labels go through its caption-to-phrase grounding mode [12] |
| YOLO-World | GPL-3.0; the README says commercial use is supported and points to the authors for a commercial license [13] | Unverified | Roboflow serverless [16] | LVIS 35.4 AP at 52 FPS on V100 [14]; v2.1-L 26.8 [13] |
| Gemini (native boxes) | Commercial API | n/a | Google | RF100-VL 11.6 (paper) [18]; 13.3 later reported by Roboflow [19] |
| GPT-5 | Commercial API | n/a | OpenAI | RF100-VL 1.5 mAP; boxes "did not match" object locations [19] |
| Claude | Commercial API | n/a | Anthropic | No benchmark found. Docs say coordinates are "approximate"; ask for absolute pixel coordinates [6] |

Notes on the table:
- **Gemini box format:** `box_2d` as [ymin, xmin, ymax, xmax], normalized to 0 to 1000 [1].
- **Azure AI Vision** uses a fixed taxonomy, not free-text labels. It generally misses objects smaller than 5% of the image [17], so it does not fit this task.
- **RF100-VL's main finding:** dedicated detectors beat general vision-language models, partly because the language models give no per-box confidence and do not suppress overlapping boxes [18].
- **Practical choice for the interface:** ask Gemini for boxes inside the existing per-photo call (near-zero extra cost), or run Grounding DINO on Replicate with the labels the model already produced.
- **Data residency:** sending client photos to the Grounding DINO 1.5/1.6 hosted API is a concern that should be checked before using it.

## 3. Evaluation design for one property (52 photos)

The smallest useful set of metrics:

1. **Room classification:** accuracy and a confusion matrix over a fixed room vocabulary.
2. **Components per photo:** micro precision, recall and F1 over (photo, normalized label) pairs.
   - Normalize labels with a versioned synonym map that the engineer approves.
   - Report a count-aware (multiset) variant as well.
3. **Distinct objects per room:** compare predicted counts with the human inventory per (room, label).
   - Mean absolute error.
   - Exact-match rate.
   - **Signed bias.** Over-counting is the duplicate failure, so the sign matters.
4. **Boxes:** AP at IoU 0.5, computed with pycocotools (`stats[1]`, maxDets=100) [24].
   - Vision-language model boxes have no confidence scores, so AP collapses to a single point [18]. Report precision and recall at IoU 0.5 instead.
5. **Room grouping (step 2):** pairwise precision and recall over photo pairs. This is a standard clustering metric; I did not open a source for it.

Statistical treatment:
- **Intervals:** treat items as samples and report 95% intervals [23].
  - 52 photos is small: about ±11 percentage points at 80% accuracy.
  - Use Wilson intervals for proportions (standard method; not separately cited).
- **Clustering by room:** photos in the same room are correlated. Use clustered standard errors, or a bootstrap that resamples whole rooms [23].
  - With about 10 rooms, the room-level intervals will be wide. Say so rather than hide it.
- **Repeated runs:** run each configuration 3 to 5 times to measure nondeterminism [23]. Report the mean and the run-to-run standard deviation.
- **Comparing approaches:** compare per-photo-plus-merge against the multi-image call as paired differences on the same rooms [23].
- **Limits of one property:** results from one property are a sanity check, not evidence that the method generalizes.

## How products in adjacent markets handle this

- **Restb.ai (real estate listings):** classifies room type per photo and offers 700+ tags. It analyzes "all of a property's images collectively," and users confirm or deselect detected features before publishing [26]. This is the same per-photo-then-aggregate pattern with a human confirmation step. How they aggregate is not public.
- **Hosta a.i. (insurance claims):** photo prompts lead to floor plans, 3D models, and an object and material list [25]. How it groups photos by room and deduplicates objects is not described. Claims of 4 to 8 photos per room appeared only in search snippets and are unverified.
- **Unverified:** Encircle, Hover, and Xactimate-adjacent inventory tools were not checked.

## Sources

1. https://ai.google.dev/gemini-api/docs/image-understanding
2. https://ai.google.dev/gemini-api/docs/pricing
3. https://developers.openai.com/api/docs/guides/images-vision
4. https://developers.openai.com/api/docs/pricing
5. https://platform.claude.com/docs/en/build-with-claude/vision
6. https://platform.claude.com/docs/en/build-with-claude/vision-coordinates
7. https://arxiv.org/abs/2405.10300
8. https://github.com/IDEA-Research/Grounding-DINO-1.5-API
9. https://github.com/IDEA-Research/GroundingDINO
10. https://huggingface.co/google/owlv2-base-patch16-ensemble
11. https://arxiv.org/abs/2306.09683
12. https://huggingface.co/microsoft/Florence-2-large
13. https://github.com/AILab-CVC/YOLO-World
14. https://arxiv.org/abs/2401.17270
15. https://replicate.com/adirik/grounding-dino
16. https://docs.roboflow.com/models/supported-models
17. https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/concept-object-detection-40
18. https://arxiv.org/html/2505.20612v3
19. https://www.latent.space/p/gpt5-vision
20. https://arxiv.org/abs/2504.15280
21. https://arxiv.org/abs/2508.00726
22. https://arxiv.org/abs/2503.11651
23. https://arxiv.org/abs/2411.00640
24. https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py
25. https://news.mit.edu/2023/making-property-assessments-simple-from-photos-0421
26. https://restb.ai/company/faq/

## What was verified and what was not

- **Read on the pages listed above:** prices, image limits, licenses, and published benchmark numbers.
- **My own arithmetic from the vendors' token rules:** the per-photo costs.
- **Not checked (marked unverified above):** CPU latency for OWLv2, Florence-2 and YOLO-World; whether VGGT works on sparse engineer photos; Hosta's photos-per-room figure; per-model counting scores on All-Angles Bench, since the full PDF failed to load and only the abstract was read.
- **Nothing was written to any repository.**
