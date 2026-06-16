"""HTML graph visualization generator for the Mementic knowledge graph."""

from __future__ import annotations

import math
from collections import defaultdict

from mementic.graph import GraphStore


def generate_html_graph(
    graph: GraphStore, filter_type: str | None = None
) -> str:
    """Generate a self-contained interactive HTML visualization of the graph.

    Produces a standalone HTML file with embedded vis.js — no CDN dependencies,
    no server required. Supports zoom, pan, drag, hover tooltips, neighborhood
    highlighting, and a visual legend.

    Args:
        graph: The GraphStore containing decisions, constraints, and entities.
        filter_type: Optional node type to display. One of 'decision',
            'constraint', 'entity'. When set, only nodes of that type and
            edges between them are rendered.

    Returns:
        A complete HTML document string ready to write to a .html file.
    """
    from pyvis.network import Network  # type: ignore[import-untyped]

    net = Network(
        height="100%",
        width="100%",
        directed=True,
        cdn_resources="in_line",
        bgcolor="#ffffff",
        font_color="#333333",
        neighborhood_highlight=True,
        select_menu=False,
        filter_menu=False,
        heading="",
    )

    # ── Physics & interaction configuration ───────────────────────
    # Higher damping + strict stabilization so the graph settles quickly.
    # forceAtlas2Based gives good spatial separation; the fit mode stops
    # the simulation once minimal energy remains.
    net.set_options("""
    {
      "nodes": {
        "font": {"size": 12, "face": "system-ui, -apple-system, sans-serif"}
      },
      "edges": {
        "font": {"size": 10, "align": "middle"},
        "smooth": {"type": "continuous", "forceDirection": "none"}
      },
      "physics": {
        "forceAtlas2Based": {
          "gravitationalConstant": -80,
          "centralGravity": 0.005,
          "springLength": 250,
          "springConstant": 0.08,
          "damping": 0.7
        },
        "maxVelocity": 20,
        "minVelocity": 1.5,
        "solver": "forceAtlas2Based",
        "stabilization": {
          "enabled": true,
          "iterations": 300,
          "updateInterval": 10,
          "fit": true
        }
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 100
      }
    }
    """)

    # ── Node styling constants ────────────────────────────────────
    _NODE_COLORS: dict[str, dict[str, object]] = {
        "Decision": {
            "background": "#4A90D9",
            "border": "#2E6BA0",
            "highlight": {"background": "#6BAFE0", "border": "#2E6BA0"},
        },
        "Constraint": {
            "background": "#E8833A",
            "border": "#B85D20",
            "highlight": {"background": "#F0A366", "border": "#B85D20"},
        },
        "CodeEntity": {
            "background": "#5DAE6E",
            "border": "#3D8A4E",
            "highlight": {"background": "#7EC48E", "border": "#3D8A4E"},
        },
    }

    _NODE_SHAPES: dict[str, str] = {
        "Decision": "dot",
        "Constraint": "diamond",
        "CodeEntity": "square",
    }

    _STATUS_MUTED_COLOR = {
        "background": "#CCCCCC",
        "border": "#888888",
        "highlight": {"background": "#DDDDDD", "border": "#888888"},
    }

    # ── Pre-compute degree from edges ─────────────────────────────
    edges = graph.iter_edges()
    degree: dict[str, int] = {}
    for src, tgt, _rel in edges:
        degree[src] = degree.get(src, 0) + 1
        degree[tgt] = degree.get(tgt, 0) + 1

    # ── Collect & filter nodes ────────────────────────────────────
    all_nodes = graph.iter_nodes()

    _FILTER_MAP: dict[str, str] = {
        "decision": "Decision",
        "constraint": "Constraint",
        "entity": "CodeEntity",
    }
    internal_filter = _FILTER_MAP.get(filter_type or "", None)

    filtered_nodes = [
        (nid, ntype, data)
        for nid, ntype, data in all_nodes
        if not internal_filter or ntype == internal_filter
    ]
    filtered_ids = {nid for nid, _, _ in filtered_nodes}

    # ── Compute initial positions for CodeEntity clustering ──────
    # Group entities by their top-level path prefix, then place each
    # group along a ring so same-directory entities start near each other.
    entity_prefixes = _group_entity_prefixes(
        [(nid, ntype, data) for nid, ntype, data in all_nodes if ntype == "CodeEntity"]
    )
    initial_positions = _compute_prefix_positions(entity_prefixes)

    # ── Add nodes ─────────────────────────────────────────────────
    for node_id, node_type, payload in filtered_nodes:
        label = _build_label(node_type, payload)
        tooltip = _build_tooltip_plain(node_type, payload)

        # Color: use type colour, override for superseded/deprecated
        status = str(payload.get("status", "accepted"))
        node_color: dict[str, object]
        if status in ("superseded", "deprecated"):
            node_color = dict(_STATUS_MUTED_COLOR)  # type: ignore[arg-type]
        else:
            node_color = dict(_NODE_COLORS.get(node_type, _NODE_COLORS["Decision"]))  # type: ignore[arg-type]

        # Size proportional to degree
        node_degree = degree.get(node_id, 0)
        size = max(8, min(40, 10 + node_degree * 2))

        # Border: normal for all statuses
        border_width = 2

        kwargs: dict[str, object] = {
            "label": label,
            "title": tooltip,
            "color": node_color,
            "shape": _NODE_SHAPES.get(node_type, "dot"),
            "size": size,
            "borderWidth": border_width,
            "borderWidthSelected": border_width + 1,
        }

        # Set initial position for CodeEntity nodes to cluster by prefix
        if node_type == "CodeEntity" and node_id in initial_positions:
            x, y = initial_positions[node_id]
            kwargs["x"] = x
            kwargs["y"] = y

        net.add_node(node_id, **kwargs)  # type: ignore[arg-type]

    # ── Add edges ─────────────────────────────────────────────────
    _EDGE_STYLES: dict[str, dict[str, object]] = {
        "affects": {"color": "#7EB8E0", "dashes": False, "width": 1.5},
        "binds_to": {"color": "#E8833A", "dashes": True, "width": 1.5},
        "enforces": {"color": "#2E6BA0", "dashes": False, "width": 2},
        "uses": {"color": "#AAAAAA", "dashes": True, "width": 1},
        "provides_config_to": {"color": "#AAAAAA", "dashes": True, "width": 1},
    }

    for src, tgt, relation in edges:
        if filter_type and (src not in filtered_ids or tgt not in filtered_ids):
            continue
        style = _EDGE_STYLES.get(relation,
            {"color": "#AAAAAA", "dashes": False, "width": 1})

        net.add_edge(
            src,
            tgt,
            title=relation,
            color=str(style["color"]),
            dashes=bool(style["dashes"]),
            width=float(str(style["width"])) if "width" in style else 1,
            arrows="to",
        )

    # ── Generate HTML ─────────────────────────────────────────────
    html = net.generate_html()

    # ── Strip CDN references ──────────────────────────────────────
    html = _strip_cdn_bootstrap(html)

    # ── Clean up pyvis template cruft ─────────────────────────────
    html = html.replace("<center>\n<h1></h1>\n</center>", "")
    html = html.replace("<center>\n          <h1></h1>\n        </center>", "")

    # Inject modern heading and legend
    heading_html = _build_heading()
    legend_html = _build_legend()
    html = html.replace("</body>", heading_html + "\n" + legend_html + "\n</body>")

    return html


# ── Helpers ─────────────────────────────────────────────────────────────


def _group_entity_prefixes(
    entities: list[tuple[str, str, dict[str, object]]],
) -> dict[str, list[str]]:
    """Group CodeEntity node IDs by their top-level path prefix.

    Returns a dict mapping prefix to list of node IDs. Entities with no
    path separator are grouped under 'root'.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for node_id, _ntype, _data in entities:
        prefix = node_id.split("/")[0] if "/" in node_id else "root"
        groups[prefix].append(node_id)
    return dict(groups)


def _compute_prefix_positions(
    groups: dict[str, list[str]],
) -> dict[str, tuple[float, float]]:
    """Compute initial (x, y) positions for nodes, grouped by prefix.

    Each prefix group is placed at a distinct angle around a large circle.
    Nodes within a group get small random offsets so they start near each
    other but not stacked exactly on top.
    """
    import random

    positions: dict[str, tuple[float, float]] = {}
    if not groups:
        return positions

    prefix_list = sorted(groups.keys())
    n_groups = len(prefix_list)
    group_radius = 2000
    jitter = 200

    for i, prefix in enumerate(prefix_list):
        angle = (2 * math.pi * i) / n_groups
        cx = group_radius * math.cos(angle)
        cy = group_radius * math.sin(angle)

        for node_id in groups[prefix]:
            # Small jitter so nodes in same group don't pile up exactly
            rng = random.Random(hash(node_id) % (2**31))
            jx = rng.uniform(-jitter, jitter)
            jy = rng.uniform(-jitter, jitter)
            positions[node_id] = (cx + jx, cy + jy)

    return positions


def _build_label(node_type: str, payload: dict[str, object]) -> str:
    """Build a short display label for a graph node."""
    if node_type == "Decision":
        return str(payload.get("title", payload.get("id", "?")))[:40]
    if node_type == "Constraint":
        return str(payload.get("description", payload.get("id", "?")))[:40]
    return str(payload.get("id", "?"))[:40]


def _build_tooltip_plain(node_type: str, payload: dict[str, object]) -> str:
    """Build a plain-text tooltip string for a graph node.

    vis.js default tooltips render as plain text — no HTML.
    Uses newline separators instead of <br> tags.
    """
    status = str(payload.get("status", "unknown"))
    lines = [
        f"{node_type} ({status})",
        f"ID: {payload.get('id', '')}",
    ]

    if node_type == "Decision":
        rationale = payload.get("rationale")
        if rationale:
            lines.append(f"Rationale: {str(rationale)[:200]}")
        rejected = payload.get("rejected_alternatives")
        if rejected and isinstance(rejected, list):
            lines.append(f"Rejected: {', '.join(rejected)[:150]}")
        source = payload.get("source")
        if source:
            lines.append(f"Source: {source}")
    elif node_type == "Constraint":
        enforced = payload.get("enforced_by")
        if enforced:
            lines.append(f"Enforced by: {enforced}")
        bound = payload.get("bound_entities")
        if bound and isinstance(bound, list):
            lines.append(f"Bound to: {', '.join(bound)[:150]}")
    elif node_type == "CodeEntity":
        lines.append(f"Type: {payload.get('type', 'file')}")

    timestamp = payload.get("timestamp")
    if timestamp:
        lines.append(f"Timestamp: {timestamp}")

    return "\n".join(lines)


def _build_heading() -> str:
    """Build a modern heading overlay for the graph page."""
    return """
<div style="
    position: fixed;
    top: 20px;
    left: 20px;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 18px;
    font-weight: 600;
    color: #333;
    background: rgba(255,255,255,0.92);
    padding: 8px 16px;
    border-radius: 8px;
    z-index: 999;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    pointer-events: none;
">
    Mementic Knowledge Graph
</div>
"""


def _build_legend() -> str:
    """Build an HTML + CSS legend overlay for the graph."""
    return """
<div id="mementic-legend" style="
    position: fixed;
    bottom: 20px;
    left: 20px;
    background: rgba(255,255,255,0.92);
    border: 1px solid #ccc;
    border-radius: 8px;
    padding: 12px 16px;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 13px;
    line-height: 1.6;
    z-index: 999;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    max-width: 280px;
">
    <b style="font-size:14px;">Legend</b><br>
    <span style="display:inline-block;width:12px;height:12px;border-radius:50%;
        background:#4A90D9;margin-right:6px;"></span> Decision<br>
    <span style="display:inline-block;width:12px;height:12px;
        background:#E8833A;clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%);
        margin-right:6px;"></span> Constraint<br>
    <span style="display:inline-block;width:12px;height:12px;
        background:#5DAE6E;margin-right:6px;"></span> Code Entity<br>
    <span style="display:inline-block;width:12px;height:12px;
        background:#CCCCCC;margin-right:6px;border-radius:50%;"></span> Deprecated<br>
    <hr style="margin:6px 0;border-color:#ddd;">
    <span style="color:#7EB8E0;">────</span> affects&nbsp;&nbsp;
    <span style="color:#E8833A;">- - -</span> binds_to<br>
    <span style="color:#2E6BA0;">────</span> enforces&nbsp;&nbsp;
    <span style="color:#AAAAAA;">·····</span> uses<br>
    <hr style="margin:6px 0;border-color:#ddd;">
    <span style="font-size:11px;color:#666;">Click node: highlight neighbors<br>
    Scroll: zoom &bull; Drag: pan</span>
</div>
"""


def _strip_cdn_bootstrap(html: str) -> str:
    """Remove CDN Bootstrap references and replace with minimal inline CSS."""
    import re

    html = re.sub(
        r'<link\s[^>]*bootstrap[^>]*cdn\.jsdelivr[^>]*/?>',
        "",
        html,
    )
    html = re.sub(
        r'<script\s[^>]*bootstrap[^>]*cdn\.jsdelivr[^>]*>.*?</script>',
        "",
        html,
        flags=re.DOTALL,
    )
    html = re.sub(r'<link\s[^>]*bootstrap[^>]*/?>', "", html)
    html = re.sub(
        r'<script\s[^>]*bootstrap[^>]*>.*?</script>',
        "",
        html,
        flags=re.DOTALL,
    )

    # We no longer need Bootstrap CSS since menus are disabled,
    # but keep the injection for defense-in-depth.
    minimal_css = """
<style id="mementic-bootstrap-replacement">
/* placeholder — no Bootstrap needed */
</style>
"""
    html = html.replace("</head>", minimal_css + "\n</head>")
    return html
