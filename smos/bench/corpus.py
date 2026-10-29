"""Deterministic synthetic requirements text for the benchmark.

Every paragraph is a pure function of (seed, index), so a paragraph can be regenerated on its own (the probe queries
need that) and two runs with the same seed and presets issue identical documents. The text is synthetic: speed numbers
measured on it are meaningful, absolute retrieval-quality numbers are not claims about real documents.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

_TOPICS: dict[str, list[str]] = {
    "braking": ["brake", "caliper", "rotor", "pedal", "hydraulic", "pressure", "deceleration", "wheel", "pad",
                "friction", "actuator", "stopping", "master", "cylinder", "fade", "booster", "skid", "release"],
    "powertrain": ["engine", "torque", "gearbox", "clutch", "transmission", "throttle", "fuel", "injector",
                   "crankshaft", "ignition", "exhaust", "turbocharger", "cylinder", "idle", "drivetrain", "shaft"],
    "battery": ["battery", "cell", "charge", "voltage", "capacity", "thermal", "pack", "discharge", "inverter",
                "charger", "current", "balancing", "coolant", "contactor", "isolation", "converter"],
    "steering": ["steering", "column", "rack", "wheel", "torque", "assist", "angle", "sensor", "motor", "linkage",
                 "tie", "rod", "yaw", "feedback", "lock", "alignment"],
    "software": ["firmware", "software", "bootloader", "update", "checksum", "memory", "flash", "version",
                 "partition", "rollback", "signature", "image", "module", "scheduler", "watchdog", "task"],
    "network": ["bus", "message", "frame", "gateway", "latency", "bandwidth", "node", "payload", "identifier",
                "arbitration", "timeout", "protocol", "ethernet", "packet", "transceiver", "baud"],
    "diagnostics": ["diagnostic", "fault", "code", "trouble", "monitor", "event", "log", "freeze", "snapshot",
                    "readiness", "service", "session", "routine", "memory", "clear", "mode"],
    "safety": ["hazard", "risk", "safety", "integrity", "mitigation", "failure", "redundancy", "detection",
               "reaction", "safe", "state", "fault", "tolerance", "analysis", "goal", "assessment"],
    "emissions": ["emission", "exhaust", "catalyst", "particulate", "oxide", "sensor", "lambda", "regeneration",
                  "filter", "limit", "cycle", "measurement", "ammonia", "injection", "purge", "canister"],
    "lighting": ["lamp", "headlight", "beam", "luminous", "intensity", "signal", "indicator", "glare", "switch",
                 "bulb", "diode", "cutoff", "aiming", "brightness", "flasher", "dimming"],
    "climate": ["climate", "compressor", "evaporator", "cabin", "temperature", "blower", "refrigerant", "duct",
                "defrost", "humidity", "heater", "vent", "airflow", "condenser", "setpoint", "zone"],
    "infotainment": ["display", "touchscreen", "audio", "navigation", "speaker", "bluetooth", "voice", "menu",
                     "startup", "radio", "camera", "phone", "projection", "interface", "icon", "volume"],
    "body": ["door", "latch", "window", "seat", "mirror", "wiper", "lock", "hinge", "sunroof", "tailgate",
             "harness", "airbag", "belt", "pretensioner", "sill", "panel"],
    "testing": ["test", "procedure", "criteria", "bench", "environment", "calibration", "tolerance", "sample",
                "specimen", "report", "inspection", "validation", "coverage", "case", "pass", "setup"],
    "security": ["authentication", "key", "certificate", "encryption", "access", "intrusion", "secure", "token",
                 "credential", "privilege", "audit", "tamper", "handshake", "challenge", "session", "trust"],
    "chassis": ["suspension", "damper", "spring", "axle", "stabilizer", "bushing", "ride", "height", "camber",
                "toe", "chassis", "subframe", "mount", "stiffness", "travel", "bump"],
}
_TOPIC_NAMES = list(_TOPICS)
_DOMAIN_TAGS = ["domain:chassis", "domain:electrical", "domain:software", "domain:safety", "domain:quality"]
_TAG_FOR_TOPIC = {
    "braking": 0, "powertrain": 0, "battery": 1, "steering": 0, "software": 2, "network": 2, "diagnostics": 2,
    "safety": 3, "emissions": 4, "lighting": 1, "climate": 1, "infotainment": 2, "body": 0, "testing": 4,
    "security": 2, "chassis": 0,
}
_FRAMES = [
    "The {a} shall {v} the {b} within the specified {c} limits.",
    "When the {a} is {s}, the {b} shall not exceed the {c} defined for this {d}.",
    "The system shall verify the {a} before the {b} is allowed to {v}.",
    "Each {a} must be checked against the {b} during every {c} cycle.",
    "If the {a} detects a {b}, the {c} shall {v} and record the {d}.",
    "The {a} of the {b} shall be documented together with its {c}.",
    "A {a} outside the permitted {b} shall result in a {c} being reported.",
]
_VERBS = ["limit", "monitor", "release", "report", "reject", "confirm", "maintain", "apply", "log", "restore"]
_STATES = ["active", "disabled", "calibrated", "locked", "degraded", "initialised", "idle", "saturated"]
_CLUSTER_RUN = 25         # consecutive paragraphs share a main topic, so vectors cluster as real text does
_MIN_WORDS, _MAX_WORDS = 40, 110   # <= ~700 characters: one paragraph stays one segment (the limit is 800)
_DOC_MIN, _DOC_MAX = 20, 60


@dataclass(frozen=True)
class Probe:
    query: str
    marker: Optional[str] = None   # unique id a correct result must contain; None for topical probes
    index: Optional[int] = None    # paragraph number of the target, for needle probes


def marker_for(index: int) -> str:
    return f"REQ-{index:06d}"


def _topics_for(seed: int, index: int) -> tuple[str, str]:
    run = random.Random(f"{seed}:run:{index // _CLUSTER_RUN}")
    main = run.choice(_TOPIC_NAMES)
    side = random.Random(f"{seed}:side:{index}").choice(_TOPIC_NAMES)
    return main, side


def paragraph(seed: int, index: int) -> str:
    rng = random.Random(f"{seed}:par:{index}")
    main, side = _topics_for(seed, index)
    pools = (_TOPICS[main], _TOPICS[main], _TOPICS[main], _TOPICS[side])
    target = rng.randint(_MIN_WORDS, _MAX_WORDS)
    sentences = [f"{marker_for(index)} Clause {rng.randint(1, 9)}.{rng.randint(1, 20)}.{rng.randint(1, 30)}."]
    words = sum(len(s.split()) for s in sentences)
    while words < target:
        pick = lambda: rng.choice(rng.choice(pools))  # noqa: E731
        sentence = rng.choice(_FRAMES).format(a=pick(), b=pick(), c=pick(), d=pick(),
                                              v=rng.choice(_VERBS), s=rng.choice(_STATES))
        sentences.append(sentence)
        words += len(sentence.split())
    return " ".join(sentences)


class CorpusBuilder:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._next_paragraph = 1
        self._next_doc = 1

    @property
    def issued_segments(self) -> int:
        return self._next_paragraph - 1

    def next_documents(self, target_segments: int) -> list[dict]:
        """New documents, in order, until `target_segments` paragraphs have been issued in total (the last
        document is cut short to land exactly on the target). Returns [] when the target is already met."""
        documents = []
        while self._next_paragraph <= target_segments:
            documents.append(self._document(target_segments))
        return documents

    def _document(self, target_segments: int) -> dict:
        number = self._next_doc
        rng = random.Random(f"{self.seed}:doc:{number}")
        count = min(rng.randint(_DOC_MIN, _DOC_MAX), target_segments - self._next_paragraph + 1)
        first = self._next_paragraph
        main, _side = _topics_for(self.seed, first)
        blocks = [{"block_type": "heading", "heading_level": 1, "text": f"{main.capitalize()} requirements {number}"}]
        for index in range(first, first + count):
            blocks.append({"block_type": "paragraph", "text": paragraph(self.seed, index)})
        links = []
        if number > 1:
            for _ in range(rng.randint(0, 2)):
                target = rng.randint(1, number - 1)
                link = {"target_native_id": f"BD-{target:06d}", "edge_type": "references"}
                if link not in links:
                    links.append(link)
        tags = [_DOMAIN_TAGS[_TAG_FOR_TOPIC[main]]]
        if rng.random() < 0.3:
            extra = rng.choice(_DOMAIN_TAGS)
            if extra not in tags:
                tags.append(extra)
        self._next_paragraph += count
        self._next_doc += 1
        return {
            "external_handle": {"system": "bench", "native_id": f"BD-{number:06d}"},
            "doc_name": f"Benchmark document {number}",
            "doc_type": "requirements",
            "domain_tags": tags,
            "blocks": blocks,
            "source_links": links,
        }

    def probes(self, n: int = 200, max_index: int = 500) -> list[Probe]:
        """About 60% needle probes (a known target paragraph among the first `max_index`) and 40% topical ones."""
        rng = random.Random(f"{self.seed}:probes")
        probes: list[Probe] = []
        for i in range(n):
            if i % 5 < 3:
                index = rng.randint(1, max(max_index, 1))
                main, _side = _topics_for(self.seed, index)
                body = [w for w in paragraph(self.seed, index).split() if w.lower().strip(".,") in set(_TOPICS[main])]
                sample = rng.sample(body, k=min(len(body), rng.randint(5, 8))) if body else [main]
                probes.append(Probe(f"{marker_for(index)} {' '.join(sample)}", marker_for(index), index))
            else:
                topic = rng.choice(_TOPIC_NAMES)
                words = rng.sample(_TOPICS[topic], k=6)
                probes.append(Probe(f"requirements about the {' '.join(words)}"))
        return probes
