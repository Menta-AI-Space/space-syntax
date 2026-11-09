import math


def compare_engines(python_metrics: dict, reference_metrics: dict) -> dict:
    """Return compact agreement diagnostics for UI inspection."""
    output: dict[str, dict[str, float | int]] = {}
    for mode in ("walk", "drive"):
        for metric in ("choice", "integration"):
            left = python_metrics[mode][metric]
            right = reference_metrics[mode][metric]
            keys = sorted(set(left) & set(right))
            xs = [float(left[k]) for k in keys]
            ys = [float(right[k]) for k in keys]
            mae = sum(abs(x - y) for x, y in zip(xs, ys)) / len(keys) if keys else 0.0
            mx = sum(xs) / len(xs) if xs else 0.0
            my = sum(ys) / len(ys) if ys else 0.0
            numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            denominator = math.sqrt(
                sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
            )
            output[f"{metric}_{mode}"] = {
                "count": len(keys),
                "mae": round(mae, 6),
                "pearson": round(numerator / denominator, 6) if denominator else 0.0,
            }
    return output
