"""Isolated depthmapX CLI runner.

The adapter intentionally uses a tiny JSON bridge contract so the web process
does not depend on a particular depthmapX build. ``DEPTHMAPX_COMMAND`` may
point at a wrapper around depthmapXcli and must accept ``--input`` and
``--output``. The input contains source geometries; the output contains the
same metric mapping returned by the Python engine.
"""

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import networkx as nx

from app.config import settings
from app.services.spatial_query import RoadSegment


DEPTHMAPX_METRICS = ("choice", "integration", "mean_depth", "connectivity")


def _largest_component_edges(graph: nx.Graph, attr: str) -> set[tuple]:
    eligible = [(u, v) for u, v, data in graph.edges(data=True) if data.get(attr)]
    if not eligible:
        return set()
    modal = graph.edge_subgraph(eligible)
    largest = max(nx.connected_components(modal), key=len)
    return {
        tuple(sorted((u, v)))
        for u, v in modal.subgraph(largest).edges()
    }


def _payload(
    graph: nx.Graph,
    source_count: int,
    modes: tuple[str, ...] = ("walk", "drive"),
    radius: str = "n",
    radius_type: str = "steps",
) -> dict:
    walk_edges = _largest_component_edges(graph, "walkable") if "walk" in modes else set()
    drive_edges = _largest_component_edges(graph, "drivable") if "drive" in modes else set()
    return {
        "analysis": "segment-angular-full",
        "include_choice": True,
        "radius": radius,
        "radius_type": radius_type,
        "modes": list(modes),
        "source_count": source_count,
        "segments": [
            {
                "source_index": i,
                "source_indices": list(data.get("segment_indices", [])),
                "coordinates": [list(u), list(v)],
                "length_m": float(data.get("length", 0.0)),
                "walkable": bool(data.get("walkable")) and tuple(sorted((u, v))) in walk_edges,
                "drivable": bool(data.get("drivable")) and tuple(sorted((u, v))) in drive_edges,
            }
            for i, (u, v, data) in enumerate(graph.edges(data=True))
        ],
    }


def _aggregate(raw: dict, payload: dict, modes: tuple[str, ...] = ("walk", "drive")) -> dict:
    output = {
        mode: {
            metric: {i: 0.0 for i in range(payload["source_count"])}
            for metric in DEPTHMAPX_METRICS
        }
        for mode in modes
    }
    weights = {
        mode: {i: 0.0 for i in range(payload["source_count"])}
        for mode in modes
    }
    mode_flags = tuple((mode, "walkable" if mode == "walk" else "drivable") for mode in modes)
    for atomic_index, segment in enumerate(payload["segments"]):
        length = max(float(segment["length_m"]), 1e-9)
        for mode, flag in mode_flags:
            if not segment[flag]:
                continue
            for source_index in segment["source_indices"]:
                weights[mode][source_index] += length
                for metric in DEPTHMAPX_METRICS:
                    output[mode][metric][source_index] += float(
                        raw[mode][metric].get(str(atomic_index), 0.0)
                    ) * length
    for mode in modes:
        for source_index, weight in weights[mode].items():
            if weight:
                for metric in DEPTHMAPX_METRICS:
                    output[mode][metric][source_index] /= weight
    return output


def compute_depthmapx_metrics(
    graph: nx.Graph,
    segments: list[RoadSegment],
    modes: tuple[str, ...] = ("walk", "drive"),
    radius: str = "n",
    radius_type: str = "steps",
    timeout_seconds: int | None = None,
) -> tuple[dict[str, dict[str, dict[int, float]]] | None, str | None]:
    command = settings.depthmapx_command.strip()
    if not command:
        return None, "DEPTHMAPX_COMMAND is not configured"

    with tempfile.TemporaryDirectory(prefix="space-syntax-depthmapx-") as tmp:
        input_path = Path(tmp) / "input.json"
        output_path = Path(tmp) / "output.json"
        payload = _payload(graph, len(segments), modes, radius, radius_type)
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        args = shlex.split(command, posix=os.name != "nt") + [
            "--input", str(input_path), "--output", str(output_path)
        ]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or settings.depthmapx_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"depthmapX execution failed: {exc}"

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-500:]
            return None, f"depthmapX exited with {completed.returncode}: {detail}"
        if not output_path.is_file():
            return None, "depthmapX bridge did not create output.json"

        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            raw_metrics = {
                mode: {
                    metric: {str(k): float(v) for k, v in raw[mode][metric].items()}
                    for metric in DEPTHMAPX_METRICS
                }
                for mode in modes
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return None, f"Invalid depthmapX bridge output: {exc}"
        return _aggregate(raw_metrics, payload, modes), None
