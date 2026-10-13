# HLD Assistant - AUTOSAR High-Level Design Document Analysis (CS1)

TechPulse FY-26 Applied AI/ML Capstone. A locally running RAG application that lets architects ask questions
about AUTOSAR High-Level Design (HLD) documents and get **cited answers**, an architecture inventory, a dependency
map, consistency checks and revision comparison. It never invents an answer: if the approved documents do not
support it, it refuses.

## 1. Run it (5 minutes)

Requirements: **Python 3.10 or newer** (checked with 3.12). Internet is needed **once** to `pip install` two small packages.

**Windows:** open the folder `Code`, double-click `run.bat` (or in a terminal: `cd Code` then `run.bat`).
**Linux / macOS:** `cd Code && bash run.sh`
**Manual:**
```
cd Code
python -m venv .venv
.venv\Scripts\activate          (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python server.py
```
Open **http://127.0.0.1:8000**. Sign in with `alice` / `demo123` (architect). Other demo users: `bob` (tester), `carol` (viewer); same password.
The synthetic knowledge base loads automatically on first start (about 2 seconds). Nothing is sent to the internet while the app runs.

## 2. What to click during a demo
| Page | What it shows |
|---|---|
| Ask | Type or click an example. Every statement carries a source tag (document, section, page). Click a tag to open the evidence. Out-of-scope questions are refused. |
| Architecture | Dependency map, plus component / port / interface / signal tables with CSV and JSON export. Click a row to see the impact of a change. |
| Checks | 11 deterministic rules (missing producer, undefined interface, type conflict, timing mismatch, safety level gap, suspicious text ...). Architects confirm or reject each finding. |
| Compare revisions | HLD-BCM-001 v1.0 against v1.1: added/removed/changed entities and changed text. |
| Documents | Upload your own PDF/MD/TXT (architect only). Newer versions supersede older ones. |
| Evaluation | Results of `scripts/run_evaluation.py` with the failed cases left visible. |
| Audit trail | Who did what (architect only). Log in as `bob` and open the Energy project to see access being refused. |

## 3. Folder map
```
Synopsis/                 filled synopsis (Word) - print, sign
Input_Data/               synthetic AUTOSAR HLD PDFs + manifest.json (+ DATA_CARD.md)
Code/                     the application (hldassist/ package, web/ UI, scripts/, tests/)
Model_Prompts_Config/     config.json (models, retrieval settings), prompts.json (prompts, guard patterns)
Evaluation_Results/       results json/csv/md + ground_truth/ (questions, entities, seeded issues)
Documentation/            technical report (Word), diagrams, screenshots, user guide
Video/                    demo script and checklist
Declarations/             student, AI-tool and external-dependency declarations
```

## 4. Configuration switches (no code change)
Edit `Model_Prompts_Config/config.json` or set an environment variable `HLD_<SECTION>_<KEY>`.

| Goal | Setting | Extra install |
|---|---|---|
| Use a local LLM through Ollama | `HLD_LLM_PROVIDER=ollama` (model in `llm.ollama_model`, default `llama3.2:3b`) | install Ollama and run `ollama pull llama3.2:3b` |
| BGE embeddings | `HLD_EMBEDDING_PROVIDER=bge` | `pip install -r requirements-full.txt` (downloads weights once) |
| FAISS index | `HLD_VECTOR_STORE_BACKEND=faiss` | `pip install faiss-cpu` |

The default (`extractive` + hashing embeddings + NumPy search) needs no model download and is what the submitted
evaluation was run with. If an optional component is missing the app falls back automatically and says so in the sidebar.
When an LLM answer fails citation validation the app falls back to the extractive answer.
After changing the embedding or vector store setting, delete the folder `Code/data` and restart.

## 5. Tests and evaluation
```
cd Code
python -m unittest discover -s tests -v      (17 tests: parsing, checks, QA, refusal, RBAC, upload, LLM fallback)
python scripts/run_evaluation.py             (writes ../Evaluation_Results/*)
python scripts/generate_synthetic_data.py    (regenerates PDFs and ground truth)
```
Docker (optional, not tested in the authoring environment): `docker compose up --build` from this folder.

## 6. Known limitations (be ready to explain these)
* Documents are **synthetic and templated**; extraction and issue-detection scores are an upper bound for real HLDs.
* Extraction is rule-based on the HLD template (key/value lines, pipe-separated tables). Diagrams inside PDFs and scanned pages are not interpreted (OCR is not included).
* The default hashing embeddings match words, not meaning. Paraphrased questions can miss (see failed case P09). BGE embeddings are the planned upgrade.
* Authentication is demo-grade (PBKDF2 passwords, in-memory sessions, plain HTTP on localhost). Use a reverse proxy with TLS and a real identity provider for production.
* Confidence thresholds were set by hand and checked on the same small question set.

## 7. Troubleshooting
* `python` not found on Windows: install Python from python.org and tick "Add to PATH".
* Port 8000 busy: `python server.py --port 8010`.
* Blank page after upgrading code: hard refresh (Ctrl+F5).
* Start fresh: stop the server and delete the folder `Code/data`.
