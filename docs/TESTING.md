# Testing and evaluation

SchemaBridge is tested at three levels: Python processing rules, Streamlit workflow behaviour and local-browser walkthroughs. The results below are recorded development evidence; the included public test files have not been re-executed as part of this documentation update.

## Test coverage

| Area | Examples covered |
|---|---|
| Ingestion | Declared CSV structure, malformed rows, duplicate headers and identifier handling |
| Candidate ranking | Label normalisation, lexical ranking, semantic similarity with test encoders, invalid vectors |
| Validation | Type/entity mismatch, unsupported units, category rules, collisions and compatible alternatives |
| Reconciliation | Missing fields, invalid durations, duplicate identifiers, source provenance and exact record accounting |
| Export | Explicit output creation, unexpected files, changed bytes, hashes and stale-session rejection |
| Interface | Four-page navigation, separated actions, invalidated state, source previews and visual configuration |

Core and GUI tests live under [`tests/`](../tests/) and [`gui/tests/`](../gui/tests/). GUI tests use Streamlit's test facilities, while deterministic matching tests can inject a small in-memory encoder rather than downloading MiniLM.

With dependencies installed, run the included tests from the repository root:

```powershell
python -m pytest tests gui/tests -q
```

## Recorded Windows checks

The original development checkout was exercised on Windows 11 with Python 3.12 and a locally cached MiniLM model.

| Recorded verification | Result |
|---|---|
| Core Python regression suite | 128 passing tests |
| Streamlit GUI regression suite | 179 passing tests |
| Focused redesigned-interface checks | 22/22 passing |
| Browser walkthrough | Four pages exercised in Chrome using locally cached MiniLM |

These checks were performed against the private development checkout. The public repository carries selected source and test files, not the full development history. There is no published CI result or independent fresh-install test for the public checkout.

## Reconciliation results

The approved fictional incident demonstration includes three CSV files, 22 declared columns and 180 records.

| Disposition | Records |
|---|---:|
| Accepted with duration | 35 |
| Accepted with optional null duration | 75 |
| Held for review | 59 |
| Quarantined | 11 |
| **Total** | **180** |

The exception set contains 70 distinct records and 74 reason events. The count of reason events exceeds the number of exception records because a record can have more than one issue.

These numbers measure the processing outcome of the provided fixtures, not the percentage of correctly identified mappings across arbitrary datasets.

## Independent synthetic matching challenge

Candidate ranking and automatic acceptance were also assessed on a once-frozen fictional challenge consisting of 17 source columns: nine genuine equivalents and eight decoys.

| Method or measure | Result |
|---|---:|
| RapidFuzz correct top-ranked target | 4/9 equivalents |
| MiniLM correct top-ranked target | 6/9 equivalents |
| Validator correctly auto-accepted | 3/9 equivalents |
| Incorrect auto-accepts in this challenge | 0 |
| Auto-accept recall | 33.33% |
| Correct among auto-accepted cases | 3/3 |

The challenge was scored after predictions had been frozen. Its answer keys have since been disclosed, so it should not be reused as an unseen test set. The sample is too small to support general claims about precision or reliability on real incident exports.

**Interpretation:** Candidate ranking can surface useful matches, but the compatibility rules deliberately hold cases whose acceptance would be difficult to justify. The low auto-accept recall is a real limitation of the current rule coverage, not an application failure.

The generated `evaluation.json` file is a placeholder with no computed runtime precision or recall. The challenge measurements above are separate from that export.

## Operational boundaries

- **Fixtures:** Tests and recorded outputs use synthetic inputs only. Real customer datasets and arbitrary uploads have not been evaluated.
- **Reproducibility:** Matching uses a local cached `all-MiniLM-L6-v2` model. Its immutable model-weight revision and all transitive dependency hashes are not pinned in this public checkout.
- **Independent installation:** A fresh clone on another computer has not yet been verified end to end.
- **Browser behaviour:** Some browser runs attempted remote Material icon requests. The local matching process does not intentionally use hosted inference.
- **Accessibility and copying:** Keyboard, zoom and responsive behaviour have been checked in parts. Complete screen-reader conformance and prevention of native dataframe cell copying were not established.
- **Export recovery:** Export verification rejects incomplete or changed report sets, though an interrupted write can leave an incomplete folder to clean up.

For code-level responsibilities and the validation rules, see [Architecture](ARCHITECTURE.md).
