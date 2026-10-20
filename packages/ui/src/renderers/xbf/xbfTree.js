import { xbfElementId } from "@text-to-cad/core/lib/xbf/xbfScene.js";

// An engine document's tree, as FreeCAD's tree view shows a document: its assemblies, nesting as
// the document nests them, and under them the parts each places, an instance a row. The tree is
// the engine's (`structure.tree`): an assembly is `{ name, children }`, a placed part
// `{ name, prototype, instance }`. A row's id is its place in the tree (`n0.2.1`); a part row
// selects its instance (`i3`, `xbfElementId`), the id the scene picks, lights and hides it by.

/**
 * @typedef {object} XbfTreeNode
 * @property {string} id  Its place in the tree.
 * @property {"assembly" | "part"} kind
 * @property {string} label  Its name in the document.
 * @property {string} detail  A part's prototype, when its name is not the prototype's.
 * @property {string | null} selectionId  A part's instance id; an assembly has none.
 * @property {string[]} partIds  Every instance id at and under it: what its eye hides.
 * @property {XbfTreeNode[]} children
 */

/**
 * @param {object | null} structure  The engine's structure (`loadXbfDocument`'s `structure`).
 * @returns {{ roots: XbfTreeNode[], nodesById: Map<string, XbfTreeNode>, parentOf: Map<string, string>,
 *   byInstance: Map<string, string> }}
 */
export function buildXbfTree(structure) {
  const nodesById = new Map();
  const parentOf = new Map();
  const byInstance = new Map();
  const prototypes = structure?.prototypes || [];
  const visit = (source, id, parent) => {
    const children = Array.isArray(source?.children) ? source.children : null;
    const placed = Number.isInteger(source?.instance);
    const label = String(source?.name || (placed ? prototypes[source.prototype]?.name : "") || "(unnamed)");
    const node = { id, kind: children ? "assembly" : "part", label, detail: "", selectionId: null, partIds: [], children: [] };
    if (children) {
      node.children = children.map((child, at) => visit(child, `${id}.${at}`, id));
      node.partIds = node.children.flatMap(child => child.partIds);
    } else if (placed) {
      node.selectionId = xbfElementId(source.instance);
      node.partIds = [node.selectionId];
      const prototypeName = String(prototypes[source.prototype]?.name || "");
      node.detail = prototypeName && prototypeName !== label ? prototypeName : "";
      byInstance.set(node.selectionId, id);
    }
    nodesById.set(id, node);
    if (parent) parentOf.set(id, parent);
    return node;
  };
  const roots = structure?.tree ? [visit(structure.tree, "n0", null)] : [];
  return { roots, nodesById, parentOf, byInstance };
}

/** The rows that hold `id`, outermost first: what opens to show it. */
export function xbfTreeAncestors(tree, id) {
  const found = [];
  for (let at = tree.parentOf.get(id); at; at = tree.parentOf.get(at)) found.unshift(at);
  return found;
}

/** Whether every one of `ids` is hidden: an eye's state. */
export function allHidden(ids, hidden) {
  return ids.length > 0 && ids.every(id => hidden.has(id));
}
