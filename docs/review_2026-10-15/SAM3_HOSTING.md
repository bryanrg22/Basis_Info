# SAM 3 hosting: Replicate or self-hosted (2026-10-17)

**Decision: stay on Replicate (`vufinder/sam3`), with compact output and SAM 3 off the
rooms gate's critical path, and keep the account's credit at $20 or more. A Cloud Run L4
deployment of the same container works (deployed, measured) and stays one setting away
(`LOCALIZER_URL`) as the fallback, and as the default once volume or Replicate's limits
make it pay.**

The measurements behind it are in EVAL_REPORT section 29; the commands are at the end.
Every number below was measured on the client study's 52 photos on 2026-10-17 unless it
cites a source.

## 1. What was slow, and why

The 72 to 74% "SAM 3 share" of a study's wall time (EVAL_REPORT 23, runs 2 and 3) was
Replicate's low-credit throttle, not SAM 3:

| Run | Replicate state | last analysis | last localization | SAM 3 after the analysis | rooms gate |
|---|---|---|---|---|---|
| 23, run 3 (v0.7.0) | throttled: 6 creates/min | +310 s | +649 s | **+339 s** | +685 s |
| 27.8 (v0.9 branch) | not throttled | +358 s | +361 s | **+3 s** | +397 s |

(From the Temporal history of run 3 and the `activity_results` rows of the 27.8 study;
both are still on this machine.) When the account is not throttled, SAM 3 keeps up with
the per-photo analysis (gpt-6.1-sol, 3 calls a photo, about 29 s each, 5 at a time):
photos arrive at about 0.15 a second and a prediction takes 2 to 5 s. The only other cost
is Replicate's cold boot. In 27.8 the first localizations waited 89 to 92 s for a worker;
I saw 78 s and 96 s here.

So there are two separate problems. **Throughput** is bounded by the account's rate tier.
**Latency on the critical path** is that the rooms gate waited for the last localization,
although nothing after it reads SAM 3's output: the room step, the assets and the export
read the per-photo analysis. This PR fixes the second outright and cuts the first.

## 2. Replicate, measured

### Per call: queue against compute

From each prediction's own timestamps (`started_at - created_at` is the queue,
`metrics.predict_time` is what is billed):

* **Queue**: 0.01 to 0.03 s when a worker is free. A cold worker takes 78 to 96 s (the
  first call after idle). When the shared pool is busy, the p95 is 13 to 56 s at
  concurrency 6 and 12 in some repeats (c6 rep c: 13 of 52 calls queued over 10 s).
* **Compute** with full masks (the code before this PR): predict p50 1.8 to 2.4 s, mean
  1.9 to 5.1 s, p95 3.2 to 19.7 s. The long tail was in the first repeats of the night;
  later repeats ran on a warmer pool.
* **Download** of the result files: 20.6 MB a photo on average (160 MB for the kitchen
  photo with 86 instances), 0.8 to 2.2 s.

### The account's rate tier

Every 429 body said the same thing:

> "Your rate limit for creating predictions is reduced to 60 requests per minute with a
> burst of 5 requests while you have less than $10.0 in credit."

Replicate's documented limit is 600 creates a minute
([rate limits](https://replicate.com/docs/topics/predictions/rate-limits)). Under $10 it
is 60 a minute with a burst of 5. Under $5 it was 6 a minute (runs 2 and 3). Replicate
recommends keeping the balance above $20 with auto-reload. **That is the cheapest
throughput fix there is**: under $10, 52 photos cannot localize in less than about 50 s
whatever the client does.

### Concurrency, 52 photos submitted at once

Wall time to localize all 52 photos (each cell one repeat; repeats interleaved over 35
minutes, so they saw the same pool drift):

| Concurrency | Full masks (before) | 429s | Compact output (after) | 429s |
|---|---|---|---|---|
| 1 | 362 / 175 / 192 s | 0 | 76 s | 0 |
| 3 | 175 / 65 / 80 s | 0 | **63 / 56 s** | 13 / 0 |
| 6 | 82 / 64 / 139 s | 1-10 | 59 / 59 s | 23 / 22 |
| 12 | 102 / 58 / 118 s | 10-33 | 60 / 57 s | 39 / 35 |
| cost of the 52 | $0.098 to $0.251 | | **$0.028 to $0.031** | |

With compact output every concurrency of 3 or more takes 55 to 63 s: the account's 60 a
minute is the bound. Concurrency 3 does that with the fewest 429s, so
`LOCALIZER_CONCURRENCY` defaults to 3. It applies per worker process and per study (it was
`min(PHOTO_CONCURRENCY 5, 4)`). The workflow feeds SAM 3 at the analysis rate, about 0.15
photos a second, so 3 slots are far more than it needs once the first cold boot is over.

### Batching

* **Several images per prediction: no.** The model's input schema (`GET
  /v1/models/vufinder/sam3/versions/1bf97763...`) has a single `image` URI.
* **Several phrases per prediction: yes, already used.** `prompts` is a list. Today's one
  call per photo with every phrase is the minimum number of predictions. The container's
  logs show the image encoded once and then each prompt decoded in turn.
* **What the output costs: the real lever.** By default the model writes, for every
  instance, a full `1 x H x W` 0/1 array as JSON, one file per prompt. Two inputs change
  that:
  * `offset_masks` returns each mask cut to its box, with its upper-left corner;
  * `split_output: false` returns one file.

  Pasted back, the masks are identical pixel for pixel (50 of 50 instances on the kitchen
  photo). On the 52 photos the result is **27x smaller** (0.75 MB against 20.6 MB a
  photo), the download 4x shorter (0.24 s against 1.0 s), and the **billed predict time
  3.7x shorter** (mean 0.57 s against 2.1 s, steady state): the model spends most of its
  "predict" time writing JSON. The localizer now asks for both (`model_input`, `compact`).
  Cost per study: **$0.03 instead of $0.11**.

## 3. Self-hosting

The model's container is public: `r8.im/vufinder/sam3@sha256:1bf97763...` can be pulled
anonymously, about 13 GB compressed with the 3.4 GB checkpoint baked in, so no gated
Hugging Face download is needed. It is a Cog 0.16 server: `POST /predictions` answers
synchronously, output files come back inline as `data:` URIs, it runs one prediction at a
time and answers 409 while busy. SAM 3 is about 850M parameters (3.44 GB in F32) and runs
in bfloat16 here; the container loads it in 19 s. License: the SAM License (royalty-free;
it excludes military, weapons and ITAR uses).
`CogSam3Localizer` (`LOCALIZER_URL`) sends it the same input as Replicate.

### Cloud Run with an L4: deployed and measured

What was done, in `cost-seg-basis`:

* The image was copied into the existing Artifact Registry repository with `gcrane`
  (Cloud Build, 1.5 min, since r8.im serves from Google's US registry).
* It was deployed as `staging-sam3` in **us-east4**: 1 L4, 4 vCPU, 16 GiB, no zonal
  redundancy, concurrency 1, min 0 and max 3 instances, authenticated only.
* **us-central1 has no L4 quota on this project**. The deploy there was refused ("You do
  not have quota for using GPUs without zonal redundancy", and the same with it).
  us-east4, europe-west1 and europe-west4 accepted it, which fits the documented automatic
  grant of 3. asia-southeast1 refused. I deleted the three extra services right away.

| | Replicate (compact, c=3) | Cloud Run L4 (c=1) | Cloud Run L4 (c=3) |
|---|---|---|---|
| 52 photos at once | 56 to 63 s (rate tier) | 88 s | **40.5 s** |
| predict per photo, mean / p95 | 0.57 / 0.85 s (L40S) | 1.26 / 1.99 s | 1.25 / 1.94 s |
| request per photo, warm | about 1.4 s | about 1.7 s | |
| 429s / 409s | 13 / - | 0 / 2 (retried) | 0 / 0 |
| replay of 27.8's arrivals: lag after each analysis, mean / max | **1.6 / 2.8 s** (before: 4.2 / 12.5 s) | | **2.0 / 3.6 s** |
| SAM 3 done after the last analysis | **1.4 s** (before: 2.4 s) | | **1.4 s** |
| cold start, first result from idle | 78 to 96 s (Replicate's worker) | **32 s** (scaled to zero, one request) | |
| cost of the 52, busy time only | $0.029 | $0.026 | $0.035 |
| idle cost after a study | none | about 6 to 10 min per instance used: $0.10 to $0.17 each | |

Notes:

* **Busy time vs billed time.** Cloud Run GPU services are billed per instance second,
  idle included ([pricing](https://cloud.google.com/run/pricing)): one L4 with 4 vCPU and
  16 GiB, no zonal redundancy, is $0.0002907 a second, $1.05 an hour. Cloud Monitoring's
  `billable_instance_time` showed each instance billed for 6 to 10 minutes after its last
  request before scaling to zero. So a lone study costs about $0.13 to $0.20 on one
  instance, and up to $0.55 if it fans out to 3. That is 4 to 18 times Replicate's $0.03.
  Back-to-back studies share the tail.
* **Readiness.** Cog's `/health-check` answers 200 with `"status": "STARTING"` while the
  model loads ([Cog HTTP API](https://github.com/replicate/cog/blob/main/docs/http.md)),
  so Cloud Run's startup probe passes before the model is ready and the first requests
  get 409 ("busy"). Seen twice: two 409s in the first minute of a new revision, and two
  again on the cold start from zero. The client retries 409, so they cost seconds, not
  failures.
* **Kept running?** No minimum instances are set, so it costs nothing while idle (the
  image storage is about $1.30 a month). Delete it with `gcloud run services delete
  staging-sam3 --region us-east4 --project cost-seg-basis` and the image with `gcloud
  artifacts docker images delete
  us-central1-docker.pkg.dev/cost-seg-basis/basis/staging-sam3:1bf97763` if it is not
  wanted.

### AWS

Researched, not deployed:

* **Blocked**: the configured AWS account's "Running On-Demand G and VT instances" quota
  is 0 vCPUs (`aws service-quotas get-service-quota --quota-code L-DB2E81BA`), so no GPU
  instance can start until a quota request is granted. It is also not established that
  this account is the one the YC credits are on.
* Prices (us-east-1, on demand, [EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)):
  * g6.xlarge (L4, the GPU measured on Cloud Run): $0.805 an hour;
  * g5.xlarge (A10G): $1.006;
  * g6e.xlarge (L40S, Replicate's GPU): $1.861;
  * spot is about $0.62, $0.46 and $1.83 (third-party).
* No scale to zero without extra machinery. SageMaker Serverless has no GPU; SageMaker
  Async Inference can scale to zero, but waking from zero takes minutes (an alarm plus an
  instance boot). So the honest AWS shape is an always-on g6.xlarge at about $19 a day,
  or a start-on-demand instance with a boot of a few minutes (unverified for this image).

## 4. Cost per study and at volume

| Option | Per study (52 photos) | Fixed | Break-even against Replicate |
|---|---|---|---|
| Replicate, compact (now) | **$0.03** | none | |
| Replicate, full masks (before) | $0.10 to $0.25 | none | |
| Cloud Run L4, scale to zero | $0.03 busy + $0.10 to $0.17 tail per instance | $1.30 a month (image) | never for isolated studies |
| Cloud Run L4, 1 instance always on | marginal ~$0 | $25 a day | about 840 studies a day |
| AWS g6.xlarge always on | marginal ~$0 | $19 a day | about 640 studies a day |
| Replicate deployment (dedicated L40S, min 1) | marginal ~$0 | $84 a day | not worth it |

The YC credits ($2k GCP, $10k AWS) would pay for Cloud Run's tail for years at this
volume. But money is not the deciding difference at $0.03 a study, and the credits are
better spent where the costs are: the language models, about $6 a study.

## 5. Why Replicate, and what would change it

For Replicate:

* It is the cheapest per study: $0.03, nothing fixed, nothing idle.
* There is nothing to operate.
* With this PR its latency no longer matters: the rooms gate does not wait for SAM 3, and
  SAM 3 trails the analysis by seconds (section 2 and the replay).

Against it:

* The rate tier. Under $10 of credit it caps throughput at 60 creates a minute; under $5,
  at 6, which is what made runs 2 and 3 slow. Fix: auto-reload and a balance of $20 or
  more.
* The 80 to 95 s cold boot on the first photo after idle. It is behind the gate now and
  harmless.
* A shared pool whose queue swings: 13 to 56 s p95 in some repeats.

Switch to Cloud Run (set `LOCALIZER_URL` and `LOCALIZER_GOOGLE_AUTH=true` on the worker,
whose service account needs `roles/run.invoker`) when any of these holds:

* volume reaches hundreds of studies a day, or several concurrent studies;
* Replicate's tier cannot be kept up;
* data residency requires the photos to stay in our project.

Before the switch:

* Raise the L4 quota in us-central1 (or keep us-east4).
* Consider min-instances 1 during business hours ($1.05 an hour) to remove the cold start.
* Give it a proper readiness probe.

## 6. How it was measured (commands)

From `backEnd/`, with `REPLICATE_API_TOKEN` in the environment (never printed). The
photos and the per-photo labels are private (`~/basis-eval-data`).

```bash
# Replicate, 52 photos at once, at a concurrency (compact output; --full-masks for before)
uv run --no-sync python ../docs/review_2026-10-15/repro/sam3_throughput.py \
    --photos ~/basis-eval-data/inputs/photos \
    --components ~/basis-eval-data/predictions/live_e2e_v070_defaults/photo_components.json \
    --concurrency 3 --out OUT
# replay a recorded study's arrivals ({file: seconds when its analysis finished})
uv run --no-sync python ../docs/review_2026-10-15/repro/sam3_throughput.py ... \
    --concurrency 3 --arrivals ARRIVALS.json --out OUT
# the self-hosted container (Cloud Run with authentication: an ID token)
LOCALIZER_TOKEN=$(gcloud auth print-identity-token) uv run --no-sync python \
    ../docs/review_2026-10-15/repro/sam3_throughput.py ... --target url --url SERVICE_URL
```

The before runs used the code at `39fc3c3` (main before this PR), from a frozen copy so
edits could not change it mid-run. Deployment:

```bash
gcloud builds submit --no-source --config cloudbuild.yaml --project cost-seg-basis \
    --region us-central1   # one step: gcrane cp r8.im/vufinder/sam3@sha256:1bf9... AR
gcloud run deploy staging-sam3 --project cost-seg-basis --region us-east4 \
    --image us-central1-docker.pkg.dev/cost-seg-basis/basis/staging-sam3:1bf97763 \
    --port 5000 --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy --cpu 4 \
    --memory 16Gi --min-instances 0 --max-instances 3 --concurrency 1 --timeout 300 \
    --no-cpu-throttling --no-allow-unauthenticated --execution-environment gen2
```
