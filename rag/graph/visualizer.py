"""
Interactive HTML graph visualization using pyvis.
Compounds = blue, Reactions = red; node size = degree centrality.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx

_EDGE_COLORS = {
    "HAS_REACTANT":    "#2196F3",
    "HAS_PRODUCT":     "#4CAF50",
    "USES_CATALYST":   "#FF9800",
    "USES_REAGENT":    "#9C27B0",
    "HAS_LOADING":     "#9E9E9E",
    "SIMILAR_TO":      "#00BCD4",
    "SUBSTRUCTURE_OF": "#795548",
    "SIMILAR_REACTION":"#E91E63",
}

_NODE_COLORS = {
    "Compound":    "#1565C0",
    "Reaction":    "#B71C1C",
    "Temperature": "#00838F",
    "Time":        "#00695C",
    "Yield":       "#558B2F",
    "Article":     "#6A1B9A",
    "Table":       "#AD1457",
    "Quantity":    "#E65100",
    "Concept":     "#4527A0",
    "Condition":   "#78909C",
}


def _edge_display_label(etype: str, edge_data: dict, v_data: dict) -> str:
    """Return a short, human-readable label for an edge."""
    specific = edge_data.get("specific_role", "").strip()

    _CATALYST_LABELS = {
        "photocatalyst": "Photocatalyst", "co-catalyst": "Co-catalyst",
        "cocatalyst": "Co-catalyst", "metal catalyst": "Metal catalyst",
        "catalyst": "Catalyst",
    }
    _REAGENT_LABELS = {
        "base": "Base", "oxidant": "Oxidant", "reductant": "Reductant",
        "additive": "Additive", "reagent": "Reagent",
    }

    if etype == "OCCURS_AT":
        return "Temperature"

    if etype == "HAS_DURATION":
        return "Time"

    if etype == "CONDUCTED_IN":
        return "Solvent"

    if etype == "USES_CATALYST":
        return _CATALYST_LABELS.get(specific, "Catalyst")

    if etype == "USES_REAGENT":
        return _REAGENT_LABELS.get(specific, specific.title() if specific else "Reagent")

    if etype == "HAS_REACTANT":
        return "Reactant"

    if etype == "HAS_PRODUCT":
        return "Product"

    if etype in ("ACHIEVES", "HAS_YIELD"):
        return "Yield"

    if etype == "HAS_LOADING":
        return "Loading"

    if etype == "SIMILAR_TO":
        tani = edge_data.get("tanimoto")
        return f"similar ({tani:.2f})" if tani else "similar"

    if etype == "SUBSTRUCTURE_OF":
        return "substructure"

    if etype == "SIMILAR_REACTION":
        return "sim. rxn"

    if etype == "HAS_TABLE":
        return "Table"

    if etype == "HAS_ENTRY":
        return "Entry"

    if etype == "ABOUT":
        return "About"

    if etype == "USES_CONDITION":
        role = edge_data.get("specific_role", "")
        _cond_map = {
            "light": "Light", "light source": "Light",
            "atmosphere": "Atmosphere", "air": "Air",
            "nitrogen": "N₂", "n2": "N₂",
            "argon": "Ar", "degas": "Degassed", "degassed": "Degassed",
        }
        return _cond_map.get(role, role.title() if role else "Condition")

    return etype.replace("_", " ").title()


def visualize_graph(
    G: nx.MultiDiGraph,
    output_path: str | Path,
    top_n: int = 200,
    height: str = "900px",
    width: str = "100%",
) -> None:
    """
    Renders the graph as an interactive HTML file.

    Args:
        G: the reaction network
        output_path: where to write the HTML file
        top_n: limit display to top-N nodes by degree centrality (for readability)
        height/width: pyvis canvas dimensions
    """
    try:
        from pyvis.network import Network
    except ImportError:
        raise ImportError("Install pyvis: pip install pyvis")

    # Select top-N nodes by degree.
    # Quantity nodes have degree=1 so they rank low — only include them when
    # the view is small enough that the extra nodes won't create clutter.
    degree = dict(G.degree())
    top_nodes = set(sorted(degree, key=degree.get, reverse=True)[:top_n])
    if top_n <= 50:
        for node in list(top_nodes):
            ntype = G.nodes[node].get("node_type")
            if ntype == "Compound":
                for succ in G.successors(node):
                    if G.nodes[succ].get("node_type") in ("Quantity", "Yield"):
                        top_nodes.add(succ)
    subG = G.subgraph(top_nodes)

    # Centrality for sizing
    centrality = nx.degree_centrality(subG)
    max_cent = max(centrality.values()) if centrality else 1
    if max_cent == 0:
        max_cent = 1

    net = Network(height=height, width=width, directed=True, notebook=False)
    net.toggle_physics(True)
    net.set_options("""
    {
      "physics": {
        "enabled": true,
        "barnesHut": {"gravitationalConstant": -8000, "springLength": 120, "damping": 0.5},
        "stabilization": {"enabled": true, "iterations": 300, "fit": true}
      },
      "edges": {"smooth": {"type": "curvedCW", "roundness": 0.2}},
      "interaction": {"hover": true, "navigationButtons": false, "zoomView": true, "dragView": true}
    }
    """)

    from .utils import display_name

    for node, data in subG.nodes(data=True):
        ntype = data.get("node_type", "Compound")
        color = _NODE_COLORS.get(ntype, "#888888")
        cent = centrality.get(node, 0)
        size = 10 + 30 * (cent / max_cent)

        if ntype == "Compound":
            label = display_name(data, max_len=20)
            roles = ", ".join(data.get("roles", []))
            title = (
                f"<b>{display_name(data)}</b><br>"
                f"Roles: {roles or '—'}<br>"
                f"SMILES: {data.get('smiles_canonical','')}<br>"
                f"PubChem: {data.get('pubchem_name','—')}<br>"
                f"Papers: {', '.join(data.get('papers',[]))}"
            )
        elif ntype == "Reaction":
            label = f"E{data.get('entry_id','')}"
            yld = data.get("yield")
            verified = data.get("verified")
            v_mark = " ✓" if verified else (" ✗" if verified is False else "")
            title = (
                f"<b>Reaction {data.get('entry_id','')}{v_mark}</b><br>"
                f"Paper: {data.get('paper','')}<br>"
                f"Table: {data.get('table','')}<br>"
                f"Yield: {yld}%<br>"
                f"Temp: {data.get('temperature','?')}°C<br>"
                f"Note: {data.get('verification_note','')}"
            )
        elif ntype == "Temperature":
            label = data.get("label", str(node))
            title = f"<b>Temperature</b><br>{data.get('label','')}"
            size = 12
        elif ntype == "Time":
            label = data.get("label", str(node))
            title = f"<b>Time</b><br>{data.get('label','')}"
            size = 12
        elif ntype in ("YieldRange", "Yield"):
            label = data.get("label", str(node))
            title = f"<b>Yield</b><br>{data.get('label', '')}"
            size = 14
        elif ntype == "Article":
            label = data.get("title", str(node))[:25]
            title = f"<b>Article</b><br>{data.get('title','')}"
            size = 22
        elif ntype == "Table":
            label = data.get("table_name", str(node))[:20]
            summary = data.get("summary", "")
            purpose = data.get("purpose", "")
            best    = data.get("best_entry", "")
            concl   = data.get("conclusion", "")
            title = (
                f"<b>Table: {data.get('table_name','')}</b><br>"
                f"Paper: {data.get('paper','')}<br>"
                + (f"<i>{purpose}</i><br>" if purpose else "")
                + (f"Best: {best}<br>" if best else "")
                + (f"Conclusion: {concl}<br>" if concl else "")
                + (f"<br>{summary}" if summary else "")
            )
            size = 14
        elif ntype == "Quantity":
            label = data.get("label", str(node))
            title = (
                f"<b>Quantity</b><br>{data.get('label','')}<br>"
                f"Role: {data.get('role','')}"
            )
            size = 10
        elif ntype == "Concept":
            label = data.get("label", str(node))[:25]
            title = (
                f"<b>Concept</b><br>"
                f"Transformation: {data.get('transformation','')}<br>"
                f"Catalyst: {data.get('catalyst_type','')}<br>"
                f"Application: {data.get('application','')}"
            )
            size = 20
        elif ntype == "Condition":
            label = data.get("label", str(node))[:20]
            title = f"<b>Condition</b><br>Role: {data.get('role','')}<br>{data.get('text','')}"
            size = 12
        else:
            label = str(node)[:20]
            title = str(data)

        net.add_node(str(node), label=label, color=color, size=size, title=title)

    for u, v, data in subG.edges(data=True):
        etype = data.get("edge_type", "")
        color = _EDGE_COLORS.get(etype, "#AAAAAA")
        v_data = subG.nodes.get(v, {})
        edge_label = _edge_display_label(etype, data, v_data)
        net.add_edge(
            str(u), str(v),
            color=color,
            label=edge_label,
            title=edge_label,
            arrows="to",
            font={"size": 9, "color": "#555555"},
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(output_path))

    # Inject: freeze physics on stabilization + custom zoom controls
    html = output_path.read_text(encoding="utf-8")
    inject = """
<style>
#zoom-controls {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
#zoom-controls button {
  width: 38px;
  height: 38px;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  border: 1px solid #bbb;
  border-radius: 6px;
  background: #ffffff;
  box-shadow: 0 2px 6px rgba(0,0,0,0.18);
  transition: background 0.15s;
}
#zoom-controls button:hover { background: #f0f0f0; }
</style>

<div id="zoom-controls">
  <button title="Zoom in"  onclick="network.moveTo({scale: network.getScale() * 1.3})">+</button>
  <button title="Fit all"  onclick="network.fit({animation:{duration:400}})">&#8982;</button>
  <button title="Zoom out" onclick="network.moveTo({scale: network.getScale() / 1.3})">&#8722;</button>
</div>

<script type="text/javascript">
  (function() {
    var wait = setInterval(function() {
      if (typeof network !== "undefined") {
        clearInterval(wait);
        network.once("stabilizationIterationsDone", function() {
          network.setOptions({ physics: { enabled: false } });
          network.fit({ animation: { duration: 600 } });
        });
        setTimeout(function() {
          network.setOptions({ physics: { enabled: false } });
        }, 8000);
      }
    }, 100);
  })();
</script>
"""
    html = html.replace("</body>", inject + "\n</body>")
    output_path.write_text(html, encoding="utf-8")
    print(f"Graph saved → {output_path}")
