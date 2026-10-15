// The ref grammar: one language, shared with cadgen (cadgen/cad_ref_syntax.py). A ref names
// geometry, never a position: an occurrence by its token (`o:` + 12 hex, minted from its place in
// the tree), an element by `<occurrence token>/<element token>` (`f:`, `e:`, `v:` + 12 hex, minted
// from the element's geometry within its body) -- `#o:4b1f9e2a7c03/f:9d2e8c1b5a70`. A part label
// stands in for an occurrence token (`#base`, `#base/f:9d2e8c1b5a70`); a bare element token
// resolves in a document with one leaf. The cases of cadRefs.parity.json are asserted by both
// languages.
//
// `<file>#<selectors>`, file half optional. Mirrors QUOTED_PREFIX_RE in cadgen/cad_ref_syntax.py.
// The prefix sits LEFT of the '#' so it can never collide with the selector grammar on the right.
// JSON-quote paths containing whitespace or delimiters: `"my part.step"#o:4b1f9e2a7c03`.
const CAD_TOKEN_RE = /^\s*("(?:[^"\\\r\n]|\\.)*"|[^#"\s]*)#([^\s]*)/;
const OCCURRENCE_TOKEN_SOURCE = "o:[0-9a-f]{12}";
const ELEMENT_TOKEN_SOURCE = "[fev]:[0-9a-f]{12}";
const OCCURRENCE_SELECTOR_RE = new RegExp(`^(${OCCURRENCE_TOKEN_SOURCE})$`);
const OCCURRENCE_ELEMENT_SELECTOR_RE = new RegExp(`^(${OCCURRENCE_TOKEN_SOURCE})/(${ELEMENT_TOKEN_SOURCE})$`);
const ELEMENT_SELECTOR_RE = new RegExp(`^(${ELEMENT_TOKEN_SOURCE})$`);
const MATE_SELECTOR_RE = /^m\d+$/i;

// Mirrors LABEL_PATTERN in cadgen/cad_ref_syntax.py. Kept in step by cadRefs.parity.json,
// which both this module's tests and the Python tests assert against.
const LABEL_SOURCE = "[A-Za-z_][A-Za-z0-9_:]*";
const LABEL_SELECTOR_RE = new RegExp(`^(${LABEL_SOURCE})$`);
const LABEL_ELEMENT_SELECTOR_RE = new RegExp(`^(${LABEL_SOURCE})/(${ELEMENT_TOKEN_SOURCE})$`);

// The union of every selector form the viewer treats as a real CAD selector rather than an
// opaque string: a token, a token pair, a bare element token, or a mate.
const NATIVE_CAD_SELECTOR_RE = new RegExp(
  `^(?:${OCCURRENCE_TOKEN_SOURCE}(?:/${ELEMENT_TOKEN_SOURCE})?|${ELEMENT_TOKEN_SOURCE}|m\\d+)$`,
  "i"
);
const OCCURRENCE_TOKEN_RE = new RegExp(`^${OCCURRENCE_TOKEN_SOURCE}$`);
const ELEMENT_TOKEN_RE = new RegExp(`^${ELEMENT_TOKEN_SOURCE}$`);

export function isNativeCadSelector(candidate) {
  return NATIVE_CAD_SELECTOR_RE.test(String(candidate || "").trim());
}

/** Whether `value` is an occurrence token (`o:` + 12 hex). */
export function isOccurrenceToken(value) {
  return OCCURRENCE_TOKEN_RE.test(String(value || "").trim());
}

/** Whether `value` is an element token (`f:`, `e:` or `v:` + 12 hex). */
export function isElementToken(value) {
  return ELEMENT_TOKEN_RE.test(String(value || "").trim());
}

function selectorTypeForKind(kind) {
  if (kind === "f") {
    return "face";
  }
  if (kind === "e") {
    return "edge";
  }
  return "vertex";
}

/** The one composition rule: `<occurrence token>/<element token>`. */
export function elementRef(occurrenceId, token) {
  return `${occurrenceId}/${token}`;
}

/** `{ owner, token }` of an element ref (`o:…/f:…`, `label/f:…`), or null for anything else. */
export function splitElementRef(selector) {
  const text = String(selector || "").trim().replace(/^#/, "");
  const slash = text.indexOf("/");
  if (slash <= 0) {
    return null;
  }
  const owner = text.slice(0, slash);
  const token = text.slice(slash + 1);
  return isElementToken(token) ? { owner, token } : null;
}

function parseStructuredSelector(rawSelector, { inheritedOccurrenceId = "", inheritedLabel = "" } = {}) {
  const selector = String(rawSelector || "").trim().replace(/^#/, "");
  if (!selector) {
    return null;
  }

  const occurrenceElementMatch = selector.match(OCCURRENCE_ELEMENT_SELECTOR_RE);
  if (occurrenceElementMatch) {
    const occurrenceId = occurrenceElementMatch[1];
    const token = occurrenceElementMatch[2];
    return {
      selectorType: selectorTypeForKind(token[0]),
      occurrenceId,
      token,
      kind: token[0],
      canonical: elementRef(occurrenceId, token)
    };
  }

  const occurrenceMatch = selector.match(OCCURRENCE_SELECTOR_RE);
  if (occurrenceMatch) {
    return {
      selectorType: "occurrence",
      occurrenceId: occurrenceMatch[1],
      token: "",
      kind: "",
      canonical: occurrenceMatch[1]
    };
  }

  const elementMatch = selector.match(ELEMENT_SELECTOR_RE);
  if (elementMatch) {
    const token = elementMatch[1];
    const kind = token[0];
    if (inheritedOccurrenceId) {
      return {
        selectorType: selectorTypeForKind(kind),
        occurrenceId: inheritedOccurrenceId,
        token,
        kind,
        canonical: elementRef(inheritedOccurrenceId, token)
      };
    }
    // An inherited label carries forward exactly like an inherited occurrence token.
    if (inheritedLabel) {
      return {
        selectorType: selectorTypeForKind(kind),
        occurrenceId: "",
        token,
        kind,
        label: inheritedLabel,
        canonical: elementRef(inheritedLabel, token)
      };
    }
    return {
      selectorType: selectorTypeForKind(kind),
      occurrenceId: "",
      token,
      kind,
      canonical: token
    };
  }

  // Label forms are tried only after every token form, so a token can never be read as a label.
  // Mates look like labels and are excluded explicitly.
  if (!MATE_SELECTOR_RE.test(selector)) {
    const labelElementMatch = selector.match(LABEL_ELEMENT_SELECTOR_RE);
    if (labelElementMatch) {
      const label = labelElementMatch[1];
      const token = labelElementMatch[2];
      return {
        selectorType: selectorTypeForKind(token[0]),
        occurrenceId: "",
        token,
        kind: token[0],
        label,
        canonical: elementRef(label, token)
      };
    }

    const labelMatch = selector.match(LABEL_SELECTOR_RE);
    if (labelMatch) {
      const label = labelMatch[1];
      return {
        selectorType: "label",
        occurrenceId: "",
        token: "",
        kind: "",
        label,
        canonical: label
      };
    }
  }

  return {
    selectorType: "opaque",
    occurrenceId: "",
    token: "",
    kind: "",
    canonical: selector
  };
}

export function parseCadRefSelector(rawSelector, options = {}) {
  return parseStructuredSelector(rawSelector, options);
}

export function normalizeCadRefSelectors(selectors) {
  const rawSelectors = Array.isArray(selectors)
    ? selectors.flatMap((selector) => String(selector || "").split(","))
    : String(selectors || "").split(",");
  const normalizedSelectors = [];
  let inheritedOccurrenceId = "";
  let inheritedLabel = "";

  for (const rawSelector of rawSelectors) {
    const parsedSelector = parseStructuredSelector(rawSelector, { inheritedOccurrenceId, inheritedLabel });
    if (!parsedSelector) {
      continue;
    }
    normalizedSelectors.push(parsedSelector.canonical);
    // Whichever naming scheme was used becomes the context for following bare elements,
    // and clears the other one.
    if (parsedSelector.occurrenceId) {
      inheritedOccurrenceId = parsedSelector.occurrenceId;
      inheritedLabel = "";
    } else if (parsedSelector.label) {
      inheritedLabel = parsedSelector.label;
      inheritedOccurrenceId = "";
    }
  }

  return normalizedSelectors;
}

export function sortCadRefSelectors(selectors) {
  const normalizedSelectors = normalizeCadRefSelectors(selectors);
  const uniqueSelectors = [...new Set(normalizedSelectors.filter(Boolean))];
  const rank = {
    occurrence: 1,
    label: 1,
    face: 3,
    edge: 4,
    vertex: 5,
    opaque: 6
  };
  return uniqueSelectors
    .map((selector, index) => ({ selector, index, parsed: parseStructuredSelector(selector) }))
    .sort((left, right) => {
      const leftOccurrence = left.parsed?.occurrenceId || left.parsed?.label || "";
      const rightOccurrence = right.parsed?.occurrenceId || right.parsed?.label || "";
      if (leftOccurrence !== rightOccurrence) {
        return leftOccurrence.localeCompare(rightOccurrence);
      }
      const leftRank = rank[left.parsed?.selectorType] ?? 99;
      const rightRank = rank[right.parsed?.selectorType] ?? 99;
      if (leftRank !== rightRank) {
        return leftRank - rightRank;
      }
      if (left.selector !== right.selector) {
        return left.selector.localeCompare(right.selector);
      }
      return left.index - right.index;
    })
    .map((item) => item.selector);
}

export function parseCadRefToken(copyText) {
  const match = String(copyText || "").trim().match(CAD_TOKEN_RE);
  if (!match) {
    return null;
  }
  // The prefix is kept RAW: a resolver matches it as a segment-aligned suffix of the document's
  // own path, so normalizing it (dropping its extension, say) breaks that.
  let cadPath = String(match[1] || "").trim();
  if (cadPath.startsWith('"')) {
    try { cadPath = JSON.parse(cadPath); } catch { return null; }
  }
  const selectorText = String(match[2] || "").trim();
  return {
    token: match[0],
    cadPath,
    selectors: normalizeCadRefSelectors(selectorText)
  };
}

export function buildCadRefToken({ cadPath = "", selector = "", selectors } = {}) {
  const prefix = String(cadPath || "").trim();
  const selectorList = selectors !== undefined ? sortCadRefSelectors(selectors) : sortCadRefSelectors(selector ? [selector] : []);
  // `<prefix>#` with no selectors is meaningful -- it names a whole file.
  const encodedPrefix = /[\s#"\\]/.test(prefix) ? JSON.stringify(prefix) : prefix;
  return `${encodedPrefix}#${selectorList.join(",")}`;
}
