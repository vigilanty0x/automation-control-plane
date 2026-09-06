from copy import deepcopy

from taskgraph.models import Evidence, GraphSpec


def graph_dict():
    return {
        "schema_version": "1.0", "graph_id": "unit-graph", "version": "1.2.3",
        "tasks": [
            {"task_id":"a-contract","owner":"architect","description":"Contract","path_scope":["docs/contract.md"],"dependencies":[],"max_attempts":2,"required_evidence":["decision"]},
            {"task_id":"b-build","owner":"builder","description":"Build","path_scope":["src/app.py"],"dependencies":["a-contract"],"max_attempts":2,"required_evidence":["commit","test"]},
            {"task_id":"c-review","owner":"reviewer","description":"Review","path_scope":["reports/review.json"],"dependencies":["b-build"],"max_attempts":1,"required_evidence":["decision"]},
        ],
    }


def graph():
    return GraphSpec.from_dict(deepcopy(graph_dict()))


def evidence(*kinds):
    return [Evidence(kind, f"artifact://{kind}", str(index + 1) * 64) for index, kind in enumerate(kinds)]

