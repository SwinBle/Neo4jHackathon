# QA record

Automated checks run with `pytest` and `scripts/mutation_check.py`. See the README's "Testing and QA" section. This file records the checks on the web explorer that were done by hand in a browser, against the committed store, served with `python -m http.server` and opened at `/web/`.

| Check | Result |
|---|---|
| Page loads `graph.json` into Relate WASM | 715 nodes and 24,086 relationships loaded in about 0.85 s, no console errors |
| Summary matches `kx match` | Same transplants, cycles, chains and unmatched count as the CLI |
| Explain P071 | Same lines as `kx why P071` |
| Explain unknown id `p999` | "No patient P999." |
| Withdraw D001 (a chain donor) | Marks its chain broken and names only the patients after D001 in the chain |
| Withdraw D085 (a cycle donor) | Says P085 leaves with their donor and names P248 and P157 as waiting |
| Explain P071, P004, P001, P161, P090 | Text identical to `kx why` for each, compared line by line in the browser |
| Withdraw unknown D999 | "No donor D999 in this tab's graph." |
| Reload graph | Restores the withdrawn donor |
| Cypher console with a syntax error | Shows the engine's parse error, page keeps working |
| Filter chips All / Cycles / Chains | Chains shows 10 of 67 exchanges; All restores 67 |
| Select an exchange card | Detail lists each donor to patient step; each patient opens their explanation |
| Explain a matched patient | Their exchange is drawn with the patient highlighted |
| Explain P071 (unmatched) | Two candidate cycles drawn; P246, P219 and P120 marked as served, captions name their exchanges |
| Phone width 375 px | No horizontal scroll, all 68 exchange cards render |
| Dark mode | Text and buttons readable; button text uses a dark colour on the light accent |

Re-run these after changing `web/` or the store. `tests/test_web_queries.py` fails if the page's Cypher drifts from the CLI's.
