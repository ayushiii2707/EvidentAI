# EvidentAI

Evidence-driven technical documentation investigation and change-impact analysis.

Status: **Phase 1, checkpoint CP2**
- CP1: document validation, parsing (PDF + Markdown), section structure, chunking.
- CP2: PostgreSQL schema, migrations, repositories, document-version lifecycle,
  scope snapshots, workspace isolation (see `docs/data-model.md`).

No ingestion worker, embeddings, retrieval or LLM yet.

## Quick start

```bash
uv sync
docker compose up -d db                       # Postgres 16 + pgvector on 127.0.0.1:5433
uv run alembic upgrade head                   # migrate the dev database (owner role)
uv run python scripts/render_pdfs.py          # Markdown corpus -> PDFs in corpus/acmepay/build/
uv run evidentai parse corpus/acmepay/build/v3.0/auth-guide.pdf --tree
uv run pytest                                 # DB tests use a disposable evidentai_test database
```

Configuration is read from environment variables; `.env.example` lists them, with
local-development defaults that must not be used beyond a laptop.

## Corpus

`corpus/acmepay/` is a **synthetic, controlled benchmark** (fictional product "AcmePay").
It exists so that every documented change between versions is known exactly
(`corpus/acmepay/ground_truth.yaml`). It is not evidence of real-world performance;
a real public documentation corpus is introduced in Phase 2.

PDF tests run against PDFs generated from that Markdown. They are controlled fixture
tests of the parser, not proof of robust parsing of arbitrary real-world PDFs.
