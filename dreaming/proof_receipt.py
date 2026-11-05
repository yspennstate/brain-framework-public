"""Exact structural-name Lean receipts for explicitly reviewed source.

Compiler output is not authenticated against arbitrary executable source. A
matching review hash is a caller assertion, never independent review or a sandbox.
Neither compilation nor a named-target receipt proves fidelity to informal prose.
"""
import hashlib
import json
import re

STANDARD_AXIOMS = frozenset({'propext', 'Classical.choice', 'Quot.sound'})
TARGET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_target(target):
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        raise ValueError('Use a simple, fully qualified Lean declaration name')
    return target


def name_tree(name):
    result = ['anonymous']
    for part in name.split('.'):
        result = ['str', result, part]
    return result


def target_commands(target):
    validate_target(target)
    expected = '_root_.Lean.Name.anonymous'
    for part in target.split('.'):
        expected = f'(_root_.Lean.Name.str {expected} "{part}")'

    def literal(text):
        # The interpolated target has passed the ASCII identifier allowlist.
        return ('_root_.Lean.logInfo (_root_.Lean.MessageData.ofFormat '
                f'(_root_.Std.Format.text "{text}"))')

    def with_json(prefix, value):
        return ('_root_.Lean.logInfo (_root_.Lean.MessageData.ofFormat '
                f'(_root_.Std.Format.text (_root_.String.append "{prefix}" {value})))')

    return ('\nopen _root_.Lean _root_.Lean.Elab _root_.Lean.Elab.Command in\nrun_cmd do\n'
            f'  let expected : _root_.Lean.Name := {expected}\n'
            '  let ci ← _root_.Lean.getConstInfo expected\n'
            '  let rec encodeName (n : _root_.Lean.Name) : _root_.Lean.Json :=\n'
            '    match n with\n'
            '    | .anonymous => .arr #[.str "anonymous"]\n'
            '    | .str parent part => .arr #[.str "str", encodeName parent, .str part]\n'
            '    | .num parent number => .arr #[.str "num", encodeName parent, .str (_root_.Nat.repr number)]\n'
            '  let identity := _root_.Lean.Json.compress (encodeName ci.name)\n'
            f'  {with_json("TARGET_NAME " + target + ": ", "identity")}\n'
            '  match ci with\n'
            f'  | .thmInfo _ => {literal("TARGET_KIND " + target + ": theorem=true")}\n'
            f'  | _ => {literal("TARGET_KIND " + target + ": theorem=false")}\n'
            '  let axioms ← _root_.Lean.collectAxioms expected\n'
            '  let encoded := _root_.Array.map encodeName axioms\n'
            '  let dependencies := _root_.Lean.Json.compress (_root_.Lean.Json.arr encoded)\n'
            f'  {with_json("TARGET_AXIOMS " + target + ": ", "dependencies")}\n')


def checked_tree(tree, depth=0):
    """Validate full Name constructors, keeping string and numeric nodes distinct."""
    if depth > 256 or not isinstance(tree, list):
        raise ValueError('Unsupported structural Name')
    if tree == ['anonymous']:
        return ('anonymous',)
    if len(tree) != 3 or tree[0] not in {'str', 'num'} or not isinstance(tree[2], str):
        raise ValueError('Malformed structural Name')
    if tree[0] == 'num' and not re.fullmatch(r'0|[1-9][0-9]*', tree[2]):
        raise ValueError('Malformed numeric Name component')
    return (tree[0], checked_tree(tree[1], depth + 1), tree[2])


ALLOWED_TREES = {checked_tree(name_tree(name)): name for name in STANDARD_AXIOMS}
SORRY_TREE = checked_tree(name_tree('sorryAx'))


def classify(returncode, output, *, source_sha256, reviewed_sha256=None,
             target=None, evidence_integrity=True, timed_out=False):
    output = '\n'.join(output.splitlines())
    if target is not None:
        validate_target(target)
    if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('Source SHA256 required')
    if type(evidence_integrity) is not bool or type(timed_out) is not bool:
        raise ValueError('Integrity and timeout must be explicit booleans')
    result = dict(status=None, formal_target_verified=False, target=target,
                  source_sha256=source_sha256, source_review_assertion=reviewed_sha256,
                  target_kind='UNVERIFIED', target_identity_verified=False,
                  axioms=None, axiom_name_trees=None,
                  allowed_axioms=sorted(STANDARD_AXIOMS), translation_fidelity='UNVERIFIED',
                  mathematical_falsity_established=False,
                  threat_model='Explicitly reviewed source and trusted compiler/dependencies; not a sandbox')
    if not evidence_integrity:
        result['status'] = 'EVIDENCE_DRIFT_OR_MISSING'
    elif timed_out or returncode is None:
        result['status'] = 'RUNTIME_UNVERIFIED'
    elif returncode != 0:
        result['status'] = 'COMPILE_FAILED'
    elif target is None:
        result['status'] = 'COMPILED_NO_NAMED_TARGET'
    elif reviewed_sha256 is None:
        result['status'] = 'COMPILED_SOURCE_UNREVIEWED'
    elif reviewed_sha256 != source_sha256:
        result['status'] = 'SOURCE_REVIEW_MISMATCH'
    else:
        name = re.escape(target)
        identities = re.findall(r'^TARGET_NAME ' + name + r': (.+)$', output, re.M)
        if len(identities) != 1:
            result['status'] = 'TARGET_IDENTITY_MISSING_OR_MISMATCH'
            return result
        try:
            identity = checked_tree(json.loads(identities[0]))
        except (ValueError, TypeError, RecursionError):
            result['status'] = 'TARGET_IDENTITY_MALFORMED'
            return result
        if identity != checked_tree(name_tree(target)):
            result['status'] = 'TARGET_IDENTITY_MISSING_OR_MISMATCH'
            return result
        result['target_identity_verified'] = True
        kinds = re.findall(r'^TARGET_KIND ' + name + r': theorem=(true|false)$', output, re.M)
        if len(kinds) != 1:
            result['status'] = 'TARGET_KIND_MISSING_OR_AMBIGUOUS'
        elif kinds[0] != 'true':
            result.update(status='TARGET_NOT_THEOREM', target_kind='not_theorem')
        else:
            result['target_kind'] = 'theorem'
            records = re.findall(r'^TARGET_AXIOMS ' + name + r': (.+)$', output, re.M)
            if len(records) != 1:
                result['status'] = 'TARGET_AXIOMS_MISSING_OR_AMBIGUOUS'
                return result
            try:
                trees = json.loads(records[0])
                if not isinstance(trees, list):
                    raise ValueError('Axiom receipt must be an array')
                axioms = [checked_tree(tree) for tree in trees]
            except (ValueError, TypeError, RecursionError):
                result['status'] = 'MALFORMED_AXIOM_RECEIPT'
                return result
            result['axiom_name_trees'] = trees
            # Readable labels are diagnostics; permission depends on exact trees.
            result['axioms'] = [ALLOWED_TREES.get(ax, 'sorryAx' if ax == SORRY_TREE else
                              json.dumps(tree, ensure_ascii=True, separators=(',', ':')))
                              for ax, tree in zip(axioms, trees)]
            if SORRY_TREE in axioms:
                result['status'] = 'ADMITTED_TARGET'
            elif any(ax not in ALLOWED_TREES for ax in axioms):
                result['status'] = 'UNAPPROVED_AXIOMS'
            else:
                result.update(status='FORMAL_TARGET_VERIFIED', formal_target_verified=True)
    return result


def exit_code(receipt):
    if receipt['formal_target_verified']:
        return 0
    if receipt['status'] in {'COMPILE_FAILED', 'ADMITTED_TARGET', 'UNAPPROVED_AXIOMS', 'TARGET_NOT_THEOREM'}:
        return 1
    if receipt['status'] in {'RUNTIME_UNVERIFIED', 'EVIDENCE_DRIFT_OR_MISSING', 'SOURCE_REVIEW_MISMATCH'}:
        return 2
    return 3
