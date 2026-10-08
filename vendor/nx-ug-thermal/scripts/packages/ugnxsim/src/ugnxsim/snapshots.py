from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


def _plotting():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    return plt, np


def thermal(run_dir: Path) -> Path:
    plt, np = _plotting()
    rows = []
    with (run_dir / "nx" / "raw_results" / "nodes.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rows.append(
                (
                    float(row["x_mm"]),
                    float(row["y_mm"]),
                    float(row["z_mm"]),
                    float(row["temperature_K"]),
                )
            )
    values = np.asarray(rows, dtype=float)
    if not len(values):
        raise RuntimeError("thermal snapshot has no nodal results")
    figure = plt.figure(figsize=(12, 8), dpi=140)
    axis = figure.add_subplot(111, projection="3d")
    plot = axis.scatter(values[:, 0], values[:, 1], values[:, 2], c=values[:, 3], s=2.0, cmap="inferno")
    axis.set(xlabel="X (mm)", ylabel="Y (mm)", zlabel="Z (mm)", title="NX SST final nodal temperature")
    figure.colorbar(plot, ax=axis, shrink=0.7, label="Temperature (K)")
    figure.tight_layout()
    output = run_dir / "snapshots" / "thermal_temperature.png"
    figure.savefig(output)
    plt.close(figure)
    return output


def structural(run_dir: Path) -> Path:
    plt, np = _plotting()
    rows = []
    with (run_dir / "results" / "lc1_displacement_nodes.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rows.append(
                (
                    float(row["x_mm"]),
                    float(row["y_mm"]),
                    float(row["z_mm"]),
                    float(row["displacement_magnitude_mm"]),
                )
            )
    values = np.asarray(rows, dtype=float)
    if not len(values):
        raise RuntimeError("structural snapshot has no displacement results")
    figure = plt.figure(figsize=(12, 8), dpi=140)
    axis = figure.add_subplot(111, projection="3d")
    plot = axis.scatter(values[:, 0], values[:, 1], values[:, 2], c=values[:, 3], s=2.0, cmap="viridis")
    axis.set(xlabel="X (mm)", ylabel="Y (mm)", zlabel="Z (mm)", title="NX Nastran SOL 101 LC1 displacement")
    figure.colorbar(plot, ax=axis, shrink=0.7, label="Displacement magnitude (mm)")
    figure.tight_layout()
    output = run_dir / "snapshots" / "structural_displacement.png"
    figure.savefig(output)
    plt.close(figure)
    return output


def fov(run_dir: Path) -> Path:
    plt, np = _plotting()
    config = json.loads((run_dir / "results" / "fov_config.json").read_text(encoding="utf-8"))
    semantics = json.loads((run_dir / "source_inputs" / "geometry_semantics.json").read_text(encoding="utf-8"))
    figure = plt.figure(figsize=(12, 8), dpi=140)
    axis = figure.add_subplot(111, projection="3d")
    for body in semantics["bodies"]:
        minimum = np.asarray(body["bbox"]["min"], dtype=float)
        maximum = np.asarray(body["bbox"]["max"], dtype=float)
        if body.get("object_type") == "shell":
            color, width, alpha = "#333333", 1.2, 0.8
        else:
            color, width, alpha = "#777777", 0.4, 0.22
        for start, end in _box_edges(minimum, maximum):
            axis.plot(*zip(start, end), color=color, linewidth=width, alpha=alpha)
    colors = ("#d62728", "#2ca02c", "#1f77b4", "#9467bd")
    for index, spec in enumerate(config["fov_specs"]):
        origin = np.asarray(spec["origin_mm"], dtype=float)
        direction = np.asarray(spec["direction"], dtype=float)
        direction = direction / np.linalg.norm(direction)
        endpoint = origin + direction * float(spec["display_length_mm"])
        axis.plot(*zip(origin, endpoint), color=colors[index % len(colors)], linewidth=3, label=spec["label"])
        axis.scatter(*origin, color=colors[index % len(colors)], s=35)
    axis.set(xlabel="X (mm)", ylabel="Y (mm)", zlabel="Z (mm)", title="FOV axes against source geometry")
    axis.legend(loc="upper right")
    figure.tight_layout()
    output = run_dir / "snapshots" / "fov_review.png"
    figure.savefig(output)
    plt.close(figure)
    return output


def _box_edges(minimum: Any, maximum: Any):
    corners = [
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    for first in range(len(corners)):
        for second in range(first + 1, len(corners)):
            differences = sum(not math.isclose(corners[first][axis], corners[second][axis]) for axis in range(3))
            if differences == 1:
                yield corners[first], corners[second]
