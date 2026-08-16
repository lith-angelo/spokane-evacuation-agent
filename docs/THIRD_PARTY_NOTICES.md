# Third-party dependency record

Audit date: 2026-08-15. Event cutoff for the "open-sourced for 2+ weeks"
rule: 2026-08-01.

The table covers every direct code dependency. Versions are the exact versions
used on the GN100 for the final tests. "Public since" is the upstream GitHub
repository creation timestamp returned by the GitHub API; every project
predates the event cutoff by years. Package metadata and the lockfile carry the
license for transitive packages.

| Package | Exact version | License | Upstream / public since |
|---|---:|---|---|
| FastAPI | 0.141.1 | MIT | [fastapi/fastapi](https://github.com/fastapi/fastapi), 2018-12-08 |
| Uvicorn | 0.52.3 | BSD-3-Clause | [Kludex/uvicorn](https://github.com/Kludex/uvicorn), 2017-05-31 |
| HTTPX | 0.28.1 | BSD-3-Clause | [encode/httpx](https://github.com/encode/httpx), 2019-04-04 |
| Pydantic | 2.13.4 | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic), 2017-05-03 |
| Shapely | 2.1.2 | BSD-3-Clause | [shapely/shapely](https://github.com/shapely/shapely), 2011-12-31 |
| OpenAI Python client | 3.1.0 | Apache-2.0 | [openai/openai-python](https://github.com/openai/openai-python), 2020-10-25 |
| python-dotenv | 1.2.2 | BSD-3-Clause | [theskumar/python-dotenv](https://github.com/theskumar/python-dotenv), 2014-09-06 |
| pytest | 9.1.1 | MIT | [pytest-dev/pytest](https://github.com/pytest-dev/pytest), 2015-06-15 |
| pytest-asyncio | 1.4.0 | Apache-2.0 | [pytest-dev/pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio), 2015-04-11 |
| Leaflet | 1.9.4 | BSD-2-Clause | [Leaflet/Leaflet](https://github.com/Leaflet/Leaflet), 2010-09-22 |
| Motion | 13.1.0 | MIT | [motiondivision/motion](https://github.com/motiondivision/motion), 2018-11-16 |
| React / React DOM | 18.3.1 | MIT | [react/react](https://github.com/react/react), 2013-05-24 |
| Vite | 6.4.3 | MIT | [vitejs/vite](https://github.com/vitejs/vite), 2020-04-21 |
| Vite React plugin | 4.7.0 | MIT | [vitejs/vite-plugin-react](https://github.com/vitejs/vite-plugin-react), 2022-12-02 |

Reproducibility files:

- `requirements.txt` pins direct Python dependencies.
- `requirements.lock.txt` records the complete tested Python environment.
- `web/package-lock.json` records the complete Node dependency graph,
  integrity hashes and package licenses.

NVIDIA Nemotron 3.5 Lightning, NVIDIA NIM/vLLM, NemoClaw and OpenShell are the
event-specified model/runtime platform. They are installed on the supplied
GN100 and are not vendored or copied into this repository. The project-specific
agent, tools, safety guard, UI and policy were written during the hackathon.
