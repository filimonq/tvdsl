"""базовые блоки, cfg, живость и раскраска временных."""
from ir import definitions, uses


def build_cfg(function):
    code = function["code"]
    leaders = {0} if code else set()
    for index, ins in enumerate(code):
        if ins["op"] == "label":
            leaders.add(index)
        if ins["op"] in ("branch", "jump", "return") and index + 1 < len(code):
            leaders.add(index + 1)
    starts = sorted(leaders)
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(code)
        blocks.append({"id": index, "start": start, "end": end,
                       "instructions": code[start:end],
                       "labels": [ins["label"] for ins in code[start:end] if ins["op"] == "label"],
                       "successors": [], "predecessors": []})
    labels = {ins["label"]: block["id"] for block in blocks
              for ins in block["instructions"] if ins["op"] == "label"}
    for block in blocks:
        last = block["instructions"][-1]
        if last["op"] == "jump":
            successors = [labels[last["target"]]]
        elif last["op"] == "branch":
            successors = sorted({labels[last["true"]], labels[last["false"]]})
        elif last["op"] == "return" or block["id"] + 1 == len(blocks):
            successors = []
        else:
            successors = [block["id"] + 1]
        block["successors"] = successors
        for successor in successors:
            blocks[successor]["predecessors"].append(block["id"])
    return blocks


def liveness(function):
    blocks = build_cfg(function)
    for block in blocks:
        defined, used = set(), set()
        for ins in block["instructions"]:
            used |= uses(ins) - defined
            defined |= definitions(ins)
        block.update(use=used, defs=defined, live_in=set(), live_out=set())
    changed = True
    while changed:
        changed = False
        for block in reversed(blocks):
            outgoing = set().union(*(blocks[s]["live_in"] for s in block["successors"]))
            incoming = block["use"] | (outgoing - block["defs"])
            if outgoing != block["live_out"] or incoming != block["live_in"]:
                block["live_in"], block["live_out"] = incoming, outgoing
                changed = True
    return blocks


def interference(function):
    blocks = liveness(function)
    # локальные имена участвуют в живости, но раскрашиваем только временные ir.
    active = set().union(*(uses(i) | definitions(i) for i in function["code"]))
    graph = {name: set() for name in sorted(active) if name.startswith("%t")}

    def edge(left, right):
        if left != right and left in graph and right in graph:
            graph[left].add(right)
            graph[right].add(left)

    def clique(names):
        names = sorted(names & graph.keys())
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                edge(left, right)

    for block in blocks:
        live = set(block["live_out"])
        clique(live)
        for ins in reversed(block["instructions"]):
            dest = definitions(ins)
            # даже мёртвый результат сохранённого call не должен затереть живое значение.
            for name in dest:
                for other in live | dest:
                    edge(name, other)
            live = (live - dest) | uses(ins)
            clique(live)
    colors = {}
    for name in sorted(graph, key=lambda n: (-len(graph[n]), n)):
        forbidden = {colors[neighbor] for neighbor in graph[name] if neighbor in colors}
        color = 0
        while color in forbidden:
            color += 1
        colors[name] = color
    return {"edges": {name: sorted(adjacent) for name, adjacent in graph.items()},
            "colors": colors, "color_count": max(colors.values(), default=-1) + 1}


def analyze(program):
    result = {}
    for function in program["functions"]:
        blocks = liveness(function)
        serializable = [{key: sorted(value) if isinstance(value, set) else value
                         for key, value in block.items() if key != "instructions"}
                        for block in blocks]
        graph = interference(function)
        result[function["name"]] = {
            "blocks": serializable, "interference": graph,
            "metrics": {"instructions": sum(i["op"] not in ("label", "spec")
                                             for i in function["code"]),
                        "temporaries": len(graph["edges"]), "blocks": len(blocks),
                        "colors": graph["color_count"]}}
    return result


def cfg_dot(function):
    from ir import format_ir
    import json
    lines = ["digraph cfg {"]
    for block in build_cfg(function):
        text = format_ir({"functions": [{**function, "code": block["instructions"]}]}).split("\n", 1)[1]
        lines.append(f"  B{block['id']} [shape=box,label={json.dumps(text)}];")
        lines.extend(f"  B{block['id']} -> B{target};" for target in block["successors"])
    return "\n".join(lines + ["}"])


def interference_dot(function):
    import json
    graph = interference(function)
    lines = ["graph interference {"]
    for name, neighbors in graph["edges"].items():
        lines.append(f"  {json.dumps(name)} [label={json.dumps(name + ': ' + str(graph['colors'][name]))}];")
        for other in neighbors:
            if name < other:
                lines.append(f"  {json.dumps(name)} -- {json.dumps(other)};")
    return "\n".join(lines + ["}"])
