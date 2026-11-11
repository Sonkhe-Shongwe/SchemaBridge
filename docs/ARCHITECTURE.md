# Architecture

SchemaBridge separates schema-matching suggestions from the rules that determine whether data may be standardised. The application has a Python processing layer, a thin Streamlit interface and a controlled export path.

## Processing stages

| Stage | Implementation | Responsibility |
|---|---|---|
| Ingestion | [`ingest.py`](../src/schemabridge/ingest.py), [`profile.py`](../src/schemabridge/profile.py) | Read declared source CSVs, check their structure and profile fields |
| Candidate ranking | [`matchers.py`](../src/schemabridge/matchers.py) | Rank canonical fields using names and semantic descriptions |
| Field validation | [`validators.py`](../src/schemabridge/validators.py) | Decide whether candidates satisfy compatibility and ambiguity rules |
| Reconciliation | [`reconcile.py`](../src/schemabridge/reconcile.py) | Standardise compatible records and record exceptions |
| Interface integration | [`gui/adapter.py`](../gui/adapter.py) and [`gui/pages/`](../gui/pages/) | Present each stage without duplicating the business rules |
| Export | [`gui/export_view.py`](../gui/export_view.py) | Verify exported files and serve session-bound downloads |

## 1. Source data and canonical contract

The input consists of three declared fictional IT incident CSV files. Their columns, business definitions and value domains are recorded in [`source_metadata.json`](../config/source_metadata.json).

The target schema is defined in [`canonical_schema.json`](../config/canonical_schema.json):

| Canonical field | Meaning | Required |
|---|---|---|
| `incident_id` | Incident identifier within the originating system | Yes |
| `region` | Service coverage region or province | Yes |
| `incident_status` | Incident lifecycle state | Yes |
| `resolution_minutes` | Resolution duration measured in minutes | No |

The processing layer also generates `source_system` and `source_record_id` for provenance. These are not matching targets. Identifiers remain strings, including those with leading zeroes.

The source reader checks the declared columns and limits input paths to the configured synthetic dataset directory. It rejects malformed or mismatched inputs rather than silently changing their structure.

## 2. Candidate ranking

SchemaBridge uses two approaches:

- **Lexical matching:** RapidFuzz WRatio compares normalised source-column names with canonical names.
- **Semantic matching:** Sentence Transformers encodes the field names and their declared descriptions using `all-MiniLM-L6-v2`. Cosine similarity ranks the candidate targets.

When semantic rankings are available, the validator considers them first. Lexical rankings provide a baseline and fallback, not an independently trained ensemble or a weighted average.

Ranking produces candidates only. Similarity values are not calibrated probabilities and cannot authorise a mapping by themselves.

## 3. Independent field validation

The validator checks the proposed source-to-target pairing using a separate ruleset:

- Declared entity meaning and field type must agree.
- Duration units must have an explicit conversion to minutes.
- Category mappings must be defined and produce permitted canonical values.
- Similarity thresholds and the gap between competing candidates determine whether a proposal is sufficiently clear.
- Multiple source fields cannot silently claim the same accepted canonical target within a system.

If a top-ranked candidate is incompatible, the validator can inspect lower-ranked alternatives. A unique compatible alternative may be accepted only when its similarity also passes the configured threshold. Weak or ambiguous proposals remain under review.

Decisions are `ACCEPT`, `REVIEW` or `REJECT`, with reason codes and an approved conversion where applicable. This stage does not change source records.

## 4. Record reconciliation

Only accepted field mappings are used for value standardisation.

The reconciler applies approved region/status dictionaries and converts durations with Python `Decimal` arithmetic. Required identifiers and categories must be present and valid. Missing optional durations may remain null for unresolved incidents, while missing resolution times for closed incidents are reviewed.

Each source record has one disposition:

| Disposition | Meaning |
|---|---|
| `ACCEPTED` | Required values are valid and the optional duration is present |
| `ACCEPTED_OPTIONAL_NULL` | Record is valid with an allowed missing optional duration |
| `HELD_FOR_REVIEW` | An ambiguous mapping, category, duration or identifier issue needs review |
| `QUARANTINED` | An invalid required value or malformed duration prevents acceptance |

Accounting checks ensure that every input record has a disposition, exception records are uniquely identified and an accepted record cannot also appear in the exception set.

Records are appended with their source identity. SchemaBridge intentionally does not infer shared incident IDs or join records across source systems.

## 5. Session state and export integrity

The Streamlit interface is divided into Data Sources, AI Mapping, Validation and Results & Export. Processing operations run through [`gui/adapter.py`](../gui/adapter.py) and only after the corresponding user action.

A SHA-256 input fingerprint covers the approved runtime configuration and source CSV bytes. If those inputs change, previously derived matching and reconciliation state is invalidated. The fingerprint does not include the cached MiniLM model weights, so it is not an immutable model-environment signature.

The export operation:

1. Confirms the reconciled counts and current session state.
2. Reserves a new directory under `output/gui_runs/` instead of overwriting a previous run.
3. Writes the four report files through the core writer.
4. Verifies the expected filenames, CSV contents, JSON results and row counts.
5. Records file hashes and checks them again before offering downloads.

Viewing results is separate from preparing files. The local Streamlit configuration binds the server to `127.0.0.1` and disables Streamlit's built-in dataframe export controls. That option does not prevent someone from copying data they can see in a browser.

## Current boundaries

The implemented application supports the supplied fictional inputs and the defined incident schema. General-purpose file ingestion, automatic cross-system identity resolution and arbitrary user-defined canonical schemas are not part of this version. The local MiniLM cache and Python dependencies are installed separately from the repository.

For recorded outcomes, test coverage and the synthetic matching challenge, see [Testing and evaluation](TESTING.md).
