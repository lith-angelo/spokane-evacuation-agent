# Third-party dependency record

Audit date: 2026-08-15. Event cutoff for the "open-sourced for 2+ weeks"
rule: 2026-08-01.

The table covers every direct code dependency. Versions are the exact versions
used on the GN100 for the final tests. "Released" is the upload timestamp for
that exact version in the official PyPI or npm registry—not the age of the
upstream repository. Every direct pinned release in the table predates the
2026-08-01 cutoff. Transitive versions remain integrity-locked and
vulnerability-audited; a nested patch's publication date is not the date its
open-source project first became public. Package metadata and the lockfiles
carry the licenses for transitive packages.

| Package | Exact version | License | Exact-version release (UTC) |
|---|---:|---|---|
| FastAPI | 0.141.1 | MIT | [2026-07-29](https://pypi.org/project/fastapi/0.141.1/) |
| Uvicorn | 0.52.0 | BSD-3-Clause | [2026-07-29](https://pypi.org/project/uvicorn/0.52.0/) |
| HTTPX | 0.28.1 | BSD-3-Clause | [2024-12-06](https://pypi.org/project/httpx/0.28.1/) |
| Pydantic | 2.13.4 | MIT | [2026-05-06](https://pypi.org/project/pydantic/2.13.4/) |
| Shapely | 2.1.2 | BSD-3-Clause | [2025-09-24](https://pypi.org/project/shapely/2.1.2/) |
| OpenAI Python client | 2.52.0 | Apache-2.0 | [2026-07-31](https://pypi.org/project/openai/2.52.0/) |
| python-dotenv | 1.2.2 | BSD-3-Clause | [2026-03-01](https://pypi.org/project/python-dotenv/1.2.2/) |
| pytest | 9.1.1 | MIT | [2026-06-19](https://pypi.org/project/pytest/9.1.1/) |
| pytest-asyncio | 1.4.0 | Apache-2.0 | [2026-05-26](https://pypi.org/project/pytest-asyncio/1.4.0/) |
| Leaflet | 1.9.4 | BSD-2-Clause | [2023-05-18](https://www.npmjs.com/package/leaflet/v/1.9.4) |
| Motion | 12.43.0 | MIT | [2026-07-28](https://www.npmjs.com/package/motion/v/12.43.0) |
| React / React DOM | 18.3.1 | MIT | [2024-04-26](https://www.npmjs.com/package/react/v/18.3.1) |
| Vite | 6.4.3 | MIT | [2026-06-01](https://www.npmjs.com/package/vite/v/6.4.3) |
| Vite React plugin | 4.7.0 | MIT | [2025-07-18](https://www.npmjs.com/package/@vitejs/plugin-react/v/4.7.0) |

Reproducibility files:

- `requirements.txt` pins direct Python dependencies.
- `requirements.lock.txt` records the complete tested Python environment.
- `web/package-lock.json` records the complete Node dependency graph,
  integrity hashes and package licenses.

NVIDIA Nemotron 3.5 Lightning, NVIDIA NIM/vLLM, NemoClaw and OpenShell are the
event-specified model/runtime platform. They are installed on the supplied
GN100 and are not vendored or copied into this repository. The project-specific
agent, tools, safety guard, UI and policy were written during the hackathon.
