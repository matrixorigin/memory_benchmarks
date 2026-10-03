"""Registry; each benchmark owns its parser in a standalone directory."""

from benchmarks.coding.swecontextbench import dataset as swecontextbench
from benchmarks.textual.beam import dataset as beam
from benchmarks.textual.clbench import dataset as clbench
from benchmarks.textual.locomo_refined import dataset as locomo
from benchmarks.textual.longmemeval_s import dataset as longmemeval
from benchmarks.textual.personamem_v2 import dataset as personamem_v2

ADAPTERS = {
    "locomo": locomo, "longmemeval": longmemeval,
    "clbench": clbench, "personamem_v2": personamem_v2,
    "beam": beam, "swecontextbench": swecontextbench,
}
DATASETS = {
    name: {"path": adapter.DEFAULT_PATH, "evidence_unit": adapter.EVIDENCE_UNIT,
           "source": adapter.SOURCE}
    for name, adapter in ADAPTERS.items()
}


def normalize(dataset, kind):
    return ADAPTERS[kind].normalize(dataset)
