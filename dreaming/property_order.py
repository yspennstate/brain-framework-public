"""Narrow ordering aid for already eligible cards; never evidence of truth."""
import re
TERMS={
 'monotonicity':re.compile(r'\b(?:monotonicity|monoton(?:e|ic|ically)|nonincreasing|nondecreasing|increas(?:e|es|ing)|decreas(?:e|es|ing))\b',re.I),
 'convexity':re.compile(r'\b(?:nonconvex|convex|convexity)\b',re.I),
 'concavity':re.compile(r'\b(?:nonconcave|concave|concavity)\b',re.I),
}
OTHER_GOAL=re.compile(r'\b(?:convergence|continuity|continuous|independence|independent|existence|uniqueness|unique|differentiability|integrability|recurrence|transience|spectral gap|eigenvalue)\b',re.I)
DIRECT=re.compile(r'\b(?:prove|show|establish|verify|disprove|check)\s+(?:(?:the|strict|global|local|eventual|joint|coordinatewise)\s+)*(monotonicity|convexity|concavity)\b',re.I)
NAMED=re.compile(r'^\s*(monotonicity|convexity|concavity)\s+of\b',re.I)
QUESTION=re.compile(r'^\s*(?:is|are|does|do|must)\b',re.I)
PREMISE=re.compile(r'\b(?:assuming|given|suppose|using|provided|if|whenever|because)\b',re.I)
ADJECTIVE_END=re.compile(r'^\s*(?:[?.,:;!]|$|(?:in|on|as|when|under|for|with|and|or|rather)\b)',re.I)

def requested_property(prompt):
 families={k for k,pat in TERMS.items() if pat.search(prompt)}
 if len(families)!=1 or OTHER_GOAL.search(prompt):return None
 family=next(iter(families))
 direct=DIRECT.search(prompt) or NAMED.search(prompt)
 if direct:
  # A quoted/example instruction or a premise prefix is not an owned objective.
  prefix=prompt[:direct.start()]
  if prefix.strip() or '"' in prompt or "`" in prompt:return None
  return family
 if not QUESTION.search(prompt) or not prompt.rstrip().endswith("?"):return None
 matches=list(TERMS[family].finditer(prompt))
 for match in matches:
  word=match.group().lower()
  if word in {'monotonicity','convexity','concavity'}:continue
  if PREMISE.search(prompt[:match.start()]):continue
  if not ADJECTIVE_END.search(prompt[match.end():]):continue
  return family
 return None

def order_selected(prompt,hits,pinned_ids=()):
 """Stable preference only among existing unpinned hits; same exact hit objects."""
 family=requested_property(prompt)
 if family is None:return hits
 pins=set(pinned_ids);fixed=[h for h in hits if h.node.id in pins]
 rest=[h for h in hits if h.node.id not in pins]
 good=[h for h in rest if TERMS[family].search(h.node.title)]
 if not good:return hits
 other=[h for h in rest if not TERMS[family].search(h.node.title)]
 return fixed+good+other
