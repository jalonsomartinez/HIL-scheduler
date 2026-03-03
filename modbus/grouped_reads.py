"""Static grouped read helpers for stable Modbus point sets."""

from modbus.codec import decode_engineering_value
from modbus.units import external_to_internal


def build_read_groups(endpoint_cfg, point_names, *, max_gap_words=4, max_block_words=64):
    points_cfg = dict(endpoint_cfg.get("points", {}) or {})
    ordered_specs = []
    for point_name in point_names or ():
        spec = points_cfg.get(str(point_name))
        if not spec:
            continue
        try:
            address = int(spec["address"])
            word_count = int(spec["word_count"])
        except Exception:
            continue
        ordered_specs.append(
            {
                "point_name": str(point_name),
                "address": address,
                "word_count": word_count,
            }
        )

    ordered_specs.sort(key=lambda item: (int(item["address"]), int(item["word_count"])))
    if not ordered_specs:
        return []

    groups = []
    current_group = None
    for spec in ordered_specs:
        spec_start = int(spec["address"])
        spec_end = spec_start + int(spec["word_count"])
        if current_group is None:
            current_group = {
                "address": spec_start,
                "count": int(spec["word_count"]),
                "items": [dict(spec)],
                "end": spec_end,
            }
            continue

        group_end = int(current_group["end"])
        candidate_end = max(group_end, spec_end)
        candidate_count = candidate_end - int(current_group["address"])
        gap = spec_start - group_end
        can_extend = gap <= int(max_gap_words) and candidate_count <= int(max_block_words)
        if can_extend:
            current_group["items"].append(dict(spec))
            current_group["end"] = candidate_end
            current_group["count"] = candidate_count
            continue

        groups.append(
            {
                "address": int(current_group["address"]),
                "count": int(current_group["count"]),
                "items": [dict(item) for item in current_group["items"]],
            }
        )
        current_group = {
            "address": spec_start,
            "count": int(spec["word_count"]),
            "items": [dict(spec)],
            "end": spec_end,
        }

    if current_group is not None:
        groups.append(
            {
                "address": int(current_group["address"]),
                "count": int(current_group["count"]),
                "items": [dict(item) for item in current_group["items"]],
            }
        )
    return groups


def read_points_internal_grouped(client, endpoint_cfg, point_names, *, read_groups=None):
    points_cfg = dict(endpoint_cfg.get("points", {}) or {})
    result = {str(point_name): None for point_name in (point_names or ())}
    groups = list(read_groups or build_read_groups(endpoint_cfg, point_names))
    for group in groups:
        group_addr = int(group["address"])
        group_count = int(group["count"])
        regs = client.read_holding_registers(group_addr, group_count)
        if regs is None or len(regs) != group_count:
            continue
        for item in group.get("items", []):
            point_name = str(item["point_name"])
            point_spec = points_cfg.get(point_name)
            if point_spec is None:
                continue
            point_addr = int(item["address"])
            word_count = int(item["word_count"])
            offset = point_addr - group_addr
            point_words = regs[offset : offset + word_count]
            if len(point_words) != word_count:
                continue
            try:
                external_value = decode_engineering_value(endpoint_cfg, point_spec, point_words)
                result[point_name] = external_to_internal(point_name, point_spec.get("unit"), external_value)
            except Exception:
                continue
    return result

