-- Reference schema (SQLAlchemy creates this automatically; shown for review). PostgreSQL 16 + pgvector.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id varchar(32) PRIMARY KEY, filename varchar(500) NOT NULL, stored_path varchar(1000) NOT NULL,
  kind varchar(10), size_bytes int, sha256 varchar(64) NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'queued',      -- queued|processing|retrying|completed|needs_review|failed
  stage varchar(100), progress int DEFAULT 0, error text, attempts int DEFAULT 0, pages_count int DEFAULT 0,
  duplicate_of varchar(32), warnings jsonb, processing_ms int,
  created_at timestamptz, updated_at timestamptz, finished_at timestamptz
);
CREATE INDEX ON documents (sha256); CREATE INDEX ON documents (status);

CREATE TABLE pages (                                   -- extracted text, one row per page / sheet
  id serial PRIMARY KEY, document_id varchar(32) REFERENCES documents ON DELETE CASCADE, page_no int,
  text text, method varchar(15), ocr_confidence numeric(5,4), error text
);
CREATE INDEX ix_pages_fts ON pages USING GIN (to_tsvector('simple', text));   -- keyword search

CREATE TABLE chunks (                                  -- semantic search
  id serial PRIMARY KEY, document_id varchar(32) REFERENCES documents ON DELETE CASCADE, page_no int,
  text text, embedding vector(384)
);
CREATE INDEX ix_chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE invoices (                                -- 1 document -> N invoices
  id varchar(32) PRIMARY KEY, document_id varchar(32) REFERENCES documents ON DELETE CASCADE, seq int,
  page_start int, page_end int, source varchar(15),
  invoice_number varchar(100), invoice_date varchar(10), supplier_name varchar(300), supplier_norm varchar(300),
  customer_name varchar(300), subtotal numeric(18,4), tax numeric(18,4), total numeric(18,4), currency varchar(3),
  confidence jsonb, notes jsonb, extraction_method varchar(20),
  needs_review boolean DEFAULT false, reviewed boolean DEFAULT false, manually_corrected boolean DEFAULT false,
  created_at timestamptz, updated_at timestamptz
);
CREATE INDEX ix_invoice_dedupe ON invoices (supplier_norm, invoice_number);
CREATE INDEX ON invoices (needs_review); CREATE INDEX ON invoices (total); CREATE INDEX ON invoices (invoice_date);

CREATE TABLE line_items (id serial PRIMARY KEY, invoice_id varchar(32) REFERENCES invoices ON DELETE CASCADE,
  position int, description text, quantity numeric(18,4), unit_price numeric(18,4), amount numeric(18,4));

CREATE TABLE validation_issues (id serial PRIMARY KEY, document_id varchar(32) REFERENCES documents ON DELETE CASCADE,
  invoice_id varchar(32) REFERENCES invoices ON DELETE CASCADE, code varchar(40), field varchar(40),
  severity varchar(10), message text, created_at timestamptz);

CREATE TABLE processing_events (id serial PRIMARY KEY, document_id varchar(32) REFERENCES documents ON DELETE CASCADE,
  stage varchar(60), status varchar(20), message text, duration_ms int, created_at timestamptz);   -- processing history

CREATE TABLE corrections (id serial PRIMARY KEY, invoice_id varchar(32) REFERENCES invoices ON DELETE CASCADE,
  field varchar(40), old_value text, new_value text, "user" varchar(100), created_at timestamptz);  -- audit trail
