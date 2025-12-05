# Basis – *Accelerated Depreciation, Made Simple*

<img width="997" height="336" alt="Basis Logo" src="https://github.com/user-attachments/assets/aaab8d9c-7238-46d0-a8ea-29ea04a666e5" />

---

> **Cost segregation shouldn't take weeks. Basis gets engineers 80% of the way there—fast, guided, and defensible.**

---

## Table of Contents

* [What is Basis?](#what-is-basis)
* [Why Cost Seg?](#why-cost-seg)
* [The Problem](#the-problem)
* [The Solution](#the-solution)
* [Traction](#traction)
* [🚀 Current Project Overview](#current-project-overview)
* [🎥 Demo Video](#demo-video)
* [🛠️ Tech Stack at a Glance](#-tech-stack-at-a-glance)
* [🧱 System Architecture (High Level)](#-system-architecture-high-level)
* [✅ Engineer-in-the-Loop Workflow](#-engineer-in-the-loop-workflow)
* [🤖 AI-Assisted Automation Workflows](#-ai-assisted-automation-workflows)
* [🧭 User Workflow (High Level)](#-user-workflow-high-level)
* [🔬 Module Deep Dives](#-module-deep-dives)
* [🎯 Accuracy, Safety & Defensibility](#-accuracy-safety--defensibility)
* [🔒 Data Handling](#-data-handling)
* [🤔 Why Not Just Use ChatGPT?](#-why-not-just-use-chatgpt)
* [🧪 Getting Started (Dev)](#-getting-started-dev)
* [🏆 Awards - LavaLab Fall 2025 Best Traction](#-awards---lavalab-fall-2025-best-traction)
* [About](#about)

---

## What is Basis?

**Basis** is an AI-assisted platform for **residential-focused cost segregation firms** that accelerates the most time-consuming part of the study:

> **analyzing hundreds of photos, sketches, and appraisal documents to produce an IRS-ready report.**

Basis is not a “one-click study generator.” It’s a **human-in-the-loop, multi-stage workflow** that combines structured document extraction, vision models, and retrieval-augmented reasoning—then **walks the engineer through every decision before anything becomes client-facing.**

---

## Why Cost Seg?

**$1M** That’s what you might spend to buy a house. That upfront spend can create **tax savings** as the property depreciates over **27.5 years**.

But 27.5 years is a long time to wait.

**Cost segregation** helps owners **accelerate depreciation** and unlock meaningful savings earlier. In the U.S., there are **5,000+** businesses conducting thousands of studies per year—which makes the workflow opportunity massive.

---

## The Problem

A cost segregation study typically follows three steps:

1. **Document the property**
2. **Analyze the documentation**
3. **Generate the report**

The bottleneck is step 2.

Our interviews revealed that this analysis phase:

* Requires engineers to comb through **hundreds of photos, drawings, and appraisals**
* Can take **2–3 weeks** to complete
* Can cost **>$1,200** in labor per study
* Can leave **>$1,000** in savings on the table due to missed or inconsistently documented components

---

## The Solution

**Enter Basis.**

Engineers upload the property artifacts they already use today. Basis:

* **Organizes documents and imagery**
* **Classifies rooms, materials, and objects**
* **Guides engineers through review checkpoints**
* **Surfaces the exact references** needed for takeoffs and tax classification
  (so engineers aren’t hunting across hundreds of pages)

**Result:** faster studies, fewer errors, lower cost to serve.

---

## Traction

* **2 paying users**

  * A cost seg engineer at **CSSI** (top-5 firm)
  * A cost seg engineer at **CBIZ**
* **Design partners** (including firms among the top five largest players) have validated workflows that could be **50%+ faster**.
* **Winner – LavaLab 2025: Best Traction**

---

<a id="current-project-overview"></a>

## 🚀 Current Project Overview

* **Objective:**
  Reduce cost seg analysis time by automating repetitive classification and retrieval tasks while preserving engineer-led accuracy and auditability.

* **Core Features:**

  * **Study creation + structured upload**
  * **Appraisal-to-constraints extraction**
  * **Room classification with scene + object context**
  * **Object/component detection with metadata enrichment**
  * **Engineer review checkpoints at every stage**
  * **Engineering takeoffs assistance**
  * **Asset classification with IRS-grounded RAG**
  * **Cost classification hooks for integrated cost databases**
  * **Export-ready outputs for existing firm templates**

---

---

<a id="demo-video"></a>

## 🎥 Demo Video

A short walkthrough showing how Basis guides engineers through appraisal constraints, room/object classification, takeoffs, and IRS-grounded asset decisions.

[![Basis Demo Video](https://img.youtube.com/vi/ZpUEYUvN5II/hqdefault.jpg)](https://youtu.be/ZpUEYUvN5II)

---

## 🛠️ Tech Stack at a Glance

### 🖼️ Frontend

* **Next.js**
* **React**
* **TypeScript**
* **TailwindCSS**

### ☁️ Backend

* **Python 3.14**
* **FastAPI**
* **PyTorch**
* **Modular services** per workflow stage

### 🧠 AI / ML

**Vision Models**

* **OpenAI Vision** (object classification, appraisal processing)
* **YOLOv8m (Ultralytics)** – object detection
* **CLIP (OpenAI)** – room & material classification
* **Places365 ResNet50** – scene recognition for room classification

**Language Models**

* **OpenAI** – asset classification, cost classification
* **Gemini** – alternative room/material classification path

### 🗄️ Database / Hosting / Infra

* **Firebase** (Firestore, Storage, Auth, App Hosting)
* **Google Cloud Run** (backend services)
* **Docker**

---

## 🧱 System Architecture (High Level)

```text
┌──────────────────────────────────────────────────────────────┐
│                         ENGINEER UI                          │
├──────────────────────────────────────────────────────────────┤
│  • Study Wizard (Upload + Progress + Review)                 │
│  • Room Review                                                │
│  • Object Review                                              │
│  • Takeoff Review                                             │
│  • Asset/Cost Review                                          │
│  • Export Center                                              │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│                     NEXT.JS FRONTEND                          │
├──────────────────────────────────────────────────────────────┤
│  • Typed UI state + workflow gating                           │
│  • Firebase Auth + role-aware access                           │
│  • Upload client + progress tracking                           │
│  • Reads results directly from Firestore                       │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│                     FASTAPI SERVICES                          │
├──────────────────────────────────────────────────────────────┤
│  • Appraisal Processing                                        │
│  • Room Classification                                         │
│  • Object Classification                                       │
│  • Engineering Takeoffs                                       │
│  • Asset Classification (IRS RAG)                              │
│  • Cost Classification                                         │
│  • Shared Study Orchestrator                                   │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│                  FIREBASE DATA LAYER                          │
├──────────────────────────────────────────────────────────────┤
│  • Storage: photos, PDFs, sketches, exports                   │
│  • Firestore: studies, rooms, objects, takeoffs, audits       │
│  • Auth: engineer + admin roles                               │
└───────────────────────────────┘
```

---

## ✅ Engineer-in-the-Loop Workflow

Every module follows the same contract:

1. **Frontend triggers module** with `{ studyId }`
2. **Backend fetches** the required data from Firestore/Storage
3. **Backend runs AI/ML**
4. **Backend writes results** back to Firestore
5. **Frontend renders results**
6. **Engineer reviews + corrects**
7. **Engineer manually advances** to the next stage

This is the core design principle that keeps deliverables defensible.

---

## 🤖 AI-Assisted Automation Workflows

Basis is purpose-built to create **AI-assisted automation workflows** that are:

* **Stage-gated** (engineer-approved before progression)
* **Data-driven** (each step uses verified outputs from prior steps)
* **Audit-friendly** (structured outputs and traceable reasoning)
* **Composable** (each module can run independently via `{ studyId }`)

### Workflow Modules

Each workflow is an automation layer that reduces manual effort while preserving accuracy:

1. **Appraisal → Property Constraints Automation**
   Converts appraisal PDFs into structured constraints that guide downstream classification.

2. **Photos → Room Organization Automation**
   Uses scene + object context to group large photo sets into room-level clusters for faster review.

3. **Photos → Component Inventory Automation**
   Detects and enriches objects with metadata needed for defensible tax classification.

4. **Takeoff Assist Automation**
   Produces structured measurements and assumptions engineers can quickly validate.

5. **IRS RAG Asset Classification Automation**
   Maps verified components to MACRS buckets with citation-aware notes for compliance-grade output.

6. **Cost Classification Automation**
   Translates components + takeoffs into cost-code-ready line items aligned with firm templates.

### Why This Matters

Instead of replacing the engineer, Basis **orchestrates automation across the entire study lifecycle**, compressing timelines while improving consistency across teams and properties.

---

## 🧭 User Workflow (High Level)

1. 📝 **Create New Study**

   * Engineer enters property name
   * Selects files to upload (photos, PDFs, appraisals)
   * Clicks **Start Analysis**

2. ⬆️ **Upload Documents**

   * Files upload to Firebase Storage
   * Progress tracked in UI

3. 📄 **Appraisal Processing**

   * Extract structured data
   * Create property constraints (GLA, bedrooms, room counts, etc.)
   * ⏸️ **Engineer reviews + corrects**

4. 🏠 **Room Classification**

   * Scene + material + object context
   * Groups photos into predicted rooms
   * ⏸️ **Engineer reviews + corrects**

5. 🔍 **Object Classification**

   * Detects components from photos
   * Enriches with room context + metadata
   * ⏸️ **Engineer reviews + corrects**

6. 📐 **Engineering Takeoffs**

   * Calculates measurements
   * ⏸️ **Engineer reviews + corrects**

7. 💰 **Asset Classification**

   * IRS-grounded classification
   * ⏸️ **Engineer reviews + corrects**

8. 🧾 **Cost Classification**

   * Maps components to integrated cost databases
   * ⏸️ **Engineer reviews + corrects**

9. ✅ **Complete Study**

   * Export package generated for firm templates

---

## 🔬 Module Deep Dives

### 1) Appraisal Processing

**Goal:** Extract structured property constraints that guide downstream vision decisions.

**Inputs**

* Appraisal PDFs

**Outputs**

* `appraisal_data{}`

---

### 2) Room Classification — *Photos → Rooms*

**Per-image approach**

* Download from Storage
* Run **YOLO** for object context
* Run **Places365 / CLIP** for scene + room-type prediction

**Writeback**

* `rooms[]` into study

---

### 3) Object Classification — *Photos → Components*

For each image:

* Download from Storage
* Map to a predicted/verified room
* Use YOLO context
* Call vision model to label cost-seg relevant components

**Example output**

```json
{
  "component": "bedroom_carpet",
  "space_type": "unit_bedroom",
  "indoor_outdoor": "indoor",
  "attachment_type": "floating",
  "function_type": "decorative",
  "photo_id": "photo-123"
}
```

---

### 4) Engineering Takeoffs

**Goal:** Accelerate quantity/measurement extraction with structured, reviewable outputs.

**Output**

* `takeoffs[]` with measurement assumptions + confidence markers

---

### 5) Asset Classification — *Objects → Tax Buckets (IRS RAG)*

**Goal:** Attach defensible MACRS lives and IRS citations to each component.

**High-level steps**

* Fetch `objects[]`
* Batch objects
* Run parallel calls
* Force IRS-grounded retrieval
* Attach structured classification

**Example output**

```json
"asset_classification": {
  "bucket": "5-year",
  "life_years": 5,
  "section": "1245",
  "asset_class": "57.0",
  "macrs_system": "GDS",
  "irs_note": "Explanation with IRS citations...",
  "citation_keys": ["PUB527_RRP87_56_57_0", "ATG_CARPET"]
}
```

---

### 6) Cost Classification

**Goal:** Map verified components and takeoffs into cost codes and unit-cost structures that firms already use.

**Output**

* `cost_items[]`

---

## 🎯 Accuracy, Safety & Defensibility

Basis is designed for **engineering-grade output**, not generic AI chat.

We ensure accuracy through:

* **Retrieval-augmented reasoning** with curated, versioned study data
* **Human-in-the-loop checkpoints** at every stage
* **Confidence scoring + fallback logic**
* **Deterministic rules** for geometry and validation

---

## 🔒 Data Handling

* Customer artifacts are stored encrypted in **Firebase Storage**.
* Study data is stored in **Firestore** with role-based access.
* Vision pipelines can be isolated for sensitive drawings and photos.
* Use Enterprise API's for LLMs to prevent data being stored for training.

---

## 🤔 Why Not Just Use ChatGPT?

Cost segregation is not a single “upload a PDF” problem.

Engineers often work with **hundreds of photos and mixed documents** per study, with strict IRS expectations for classification and auditability.

Basis is a **multi-stage pipeline** that:

* structures the entire study,
* preserves engineer-verified context,
* and uses that verified context to increase accuracy at later stages.

---

## 🧪 Getting Started (Dev)

> Adjust commands to your repo structure.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

---

## 🏆 Awards - LavaLab Fall 2025 Best Traction

![Basis Team Holding Check](https://github.com/user-attachments/assets/a48693f1-f7cb-4832-a8ca-f7ed817b2f7f)

---

## About

Basis is building the infrastructure layer for modern cost segregation—
**where AI accelerates the workflow, and engineers remain in control.**
